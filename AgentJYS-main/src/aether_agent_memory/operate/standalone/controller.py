"""Standalone reconciliation: real policy decisions against an injected P2 interface.

Single-process/single-event-loop demo. No runtime imports. All external awaits are
outside mutation of the plan owner. A pending intent is reconciled before replanning.
"""

import asyncio
import math
from collections.abc import Callable
from uuid import uuid4

from .models import Intent, Memory, MemoryKey, Operation, Stats
from .policy import Settings, accumulate, evaluate, next_delay
from .ports import P2Port, SubmissionRejectedError
from .store import MemoryStore, Pending


class Controller:
    def __init__(
        self, p2: P2Port, clock: Callable[[], float], *, settings: Settings | None = None
    ) -> None:
        self.p2, self.clock = p2, clock
        self.settings = settings or Settings()
        self.store = MemoryStore(self.settings)
        self._tick_lock = asyncio.Lock()
        self._last_time = float("-inf")

    def now(self) -> float:
        value = self.clock()
        if not math.isfinite(value) or value < self._last_time:
            raise ValueError("Operate clock must be finite and monotonic")
        self._last_time = value
        return value

    def register(self, memory: Memory) -> None:
        """Metadata signal only. Does not create or repair any P2 object."""
        previous = self.store.stats.get(memory.key)
        if previous:
            if memory.version < previous.memory.version:
                raise ValueError("stale memory version")
            if memory.version == previous.memory.version:
                if memory != previous.memory:
                    raise ValueError("same version cannot change metadata")
                return
        elif len(self.store.stats) >= self.settings.max_memories:
            raise BufferError("statistics capacity reached: reject input, caller must retry")
        stats = Stats(memory, self.now(), sequence=previous.sequence + 1 if previous else 1)
        self.store.stats[memory.key] = stats
        self.store.wake(memory.key, "register")
        self._touch(memory.key)

    async def receive_access(
        self,
        key: MemoryKey,
        event_id: str,
        *,
        version: int | None = None,
        event_at: float | None = None,
        entered_context: bool = True,
    ) -> bool:
        """Ingress after reclamation: reload metadata, never recreate P2 data.

        False means ignored/duplicate input. Errors mean unaccepted input and must
        be retried by the caller with the SAME event ID and original timestamp.
        """
        if not entered_context:
            return False
        if not event_id:
            raise ValueError("event_id is required for deduplication")
        when = self.now() if event_at is None else event_at
        if not math.isfinite(when):
            raise ValueError("event timestamp must be finite")
        if self.now() - when >= self.settings.dedup_seconds:
            return False
        if key not in self.store.stats:
            observation = await asyncio.wait_for(
                self.p2.observe(key), timeout=self.settings.io_timeout_seconds
            )
            if (
                observation is None
                or not observation.known
                or not observation.readable
                or observation.memory.key != key
            ):
                raise ConnectionError("cannot reload trustworthy metadata; retry original input")
            memory = observation.memory
            if version is not None and version != memory.version:
                return False
            # A concurrent ingress may already have restored this key. Never
            # replace its newer metadata with the result of an earlier read.
            if key not in self.store.stats:
                now = self.now()
                if now - when >= self.settings.dedup_seconds:
                    return False
                identity = (key, memory.version, event_id)
                self.store.expire_dedup(now, self.settings.batch_size)
                if self.store.dedup.get(identity, float("-inf")) > now:
                    return False
                self.store.dedup.pop(identity, None)
                if len(self.store.dedup) >= self.settings.dedup_limit:
                    raise BufferError(
                        "dedup capacity reached: input not counted, caller must retry"
                    )
                self.register(memory)
        # No await between registering and counting the event.
        return self.access(key, event_id, version=version, event_at=when)

    def access(
        self,
        key: MemoryKey,
        event_id: str,
        *,
        version: int | None = None,
        event_at: float | None = None,
        entered_context: bool = True,
    ) -> bool:
        """Count one successful Context entry. Retries must reuse the same event ID."""
        if not entered_context:
            return False
        if not event_id:
            raise ValueError("event_id is required for deduplication")
        stats = self.store.stats.get(key)
        if stats is None:
            raise KeyError("Memory is not tracked; use await receive_access() to reload metadata")
        if version is not None and version != stats.memory.version:
            return False
        now = self.now()
        when = now if event_at is None else event_at
        if not math.isfinite(when):
            raise ValueError("event timestamp must be finite")
        if now - when >= self.settings.dedup_seconds:
            return False
        self.store.expire_dedup(now, self.settings.batch_size)
        identity = (key, stats.memory.version, event_id)
        if self.store.dedup.get(identity, float("-inf")) > now:
            return False
        self.store.dedup.pop(identity, None)
        if len(self.store.dedup) >= self.settings.dedup_limit:
            raise BufferError("dedup capacity reached: input not counted, caller must retry")
        self.store.dedup[identity] = now + self.settings.dedup_seconds
        accumulate(stats, when, now, self.settings)
        stats.sequence += 1
        stats.cold_since = None
        self.store.wake(key, "context_access")
        self._touch(key)
        return True

    def _touch(self, key: MemoryKey) -> None:
        self.store.buffer[key] = None
        self.store.buffer.move_to_end(key)
        if len(self.store.buffer) < self.settings.high_watermark:
            return

        # Sort only bounded resident entries, never the full Memory catalog.
        def last_access(key: MemoryKey) -> float:
            value = self.store.stats[key].last_access
            return value if value is not None else float("-inf")

        victims = sorted(self.store.buffer, key=last_access)
        for victim in victims[: len(self.store.buffer) - self.settings.low_watermark]:
            stats = self.store.stats[victim]
            evaluate(stats, self.now(), self.settings)
            self.store.wake(victim, "buffer_evict")
            del self.store.buffer[victim]
            self._note(victim, "buffer_evicted", "statistics retained; cooling check queued")

    def _note(self, key: MemoryKey, state: str, reason: str = "") -> None:
        self.store.stats[key].last_reason = reason or state
        self.store.history.append(
            {
                "at": self.now(),
                "tenant": key.tenant,
                "memory_id": key.memory_id,
                "state": state,
                "reason": reason,
            }
        )

    def _retry(self, key: MemoryKey, reason: str) -> None:
        self.store.stats[key].cold_since = None
        self._note(key, "waiting", reason)
        self.store.schedule(key, self.now() + self.settings.retry_seconds)

    async def tick(self) -> int:
        """One bounded worker batch; no sleeps, no fake execution advancement."""
        async with self._tick_lock:
            now = self.now()
            self.store.wake_due(now, self.settings.batch_size)
            self.store.expire_dedup(now, self.settings.batch_size)
            keys = []
            for _ in range(min(self.settings.workers, self.settings.batch_size)):
                if not self.store.ready:
                    break
                key, _reasons = self.store.ready.popitem(last=False)
                keys.append(key)
            await asyncio.gather(*(self._safe_reconcile(key) for key in keys))
            return len(keys)

    async def _safe_reconcile(self, key: MemoryKey) -> None:
        try:
            await asyncio.wait_for(self._reconcile(key), timeout=self.settings.io_timeout_seconds)
        except asyncio.CancelledError:
            self._retry(key, "worker cancelled; retain original plan if submitted")
            raise
        except (ConnectionError, TimeoutError) as exc:
            self._retry(key, type(exc).__name__ + ": " + str(exc))

    async def _reconcile(self, key: MemoryKey) -> None:
        pending = self.store.pending.get(key)
        if pending:
            self.store.stats[key].cold_since = None
            feedback = await self.p2.query(pending.intent.action_id)
            if feedback.action_id != pending.intent.action_id:
                self._retry(key, "action response identity mismatch")
                return
            if feedback.state in {"running", "unknown", "not_found"}:
                self._retry(key, "original action " + feedback.state + "; do not resubmit")
                return
            if feedback.state == "failed":
                del self.store.pending[key]
                self._note(key, "action_failed", feedback.reason)
                self.store.schedule(key, self.now() + self.settings.retry_seconds)
                return
            intent = pending.intent
            observation = await self.p2.observe(key)
            if (
                observation is None
                or not observation.known
                or not observation.readable
                or observation.memory != intent.memory
                or observation.epoch <= intent.epoch
            ):
                self._retry(key, "action succeeded but placement evidence is incomplete")
                return
            matches = (
                observation.hot
                if intent.operation == "ensure_hot_replica"
                else observation.base == intent.target
                and (intent.operation != "remove_hot_replica" or not observation.hot)
            )
            base_ok = await self.p2.verify_read(intent.memory, observation.base)
            target_ok = await self.p2.verify_read(intent.memory, intent.target)
            if not matches or not base_ok or not target_ok:
                self._retry(key, "action succeeded but target/base read verification failed")
                return
            del self.store.pending[key]
            self._note(key, "action_confirmed", intent.operation)
            # Always reread latest stats, including inputs received during this action.
            self.store.wake(key, "action_completed")
            return

        stats = self.store.stats[key]
        memory, sequence = stats.memory, stats.sequence
        observation = await self.p2.observe(key)
        if (
            observation is None
            or not observation.known
            or not observation.readable
            or observation.memory != memory
        ):
            self._retry(key, "placement missing/Unknown/version mismatch; no new action")
            return
        target = evaluate(stats, self.now(), self.settings)
        if target != "cold" or observation.base != "cold" or observation.hot:
            stats.cold_since = None
        operation: Operation | None = None
        if target == "hot" and not observation.hot:
            operation = "ensure_hot_replica"
        elif target != "hot" and observation.base != target:
            operation = "move_base"
        elif target != "hot" and observation.hot:
            operation = "remove_hot_replica"
        if operation is None:
            if not await self.p2.verify_read(memory, target):
                self._retry(key, "current target read failed; repair policy required")
                return
            # Read verification awaits can admit new input or a new version.
            # Recheck ownership immediately before any reclamation, without await.
            if self.store.stats.get(key) is not stats or stats.sequence != sequence:
                self.store.wake(key, "newer_input")
                return
            now = self.now()
            if evaluate(stats, now, self.settings) != target:
                self.store.wake(key, "target_changed_during_verification")
                return
            delay = next_delay(stats, now, self.settings)
            if target == "cold":
                if stats.cold_since is None:
                    stats.cold_since = now
                remaining = stats.cold_since + self.settings.stats_retention_seconds - now
                if remaining <= 0 and self.store.forget(key, stats, sequence):
                    self.store.history.append(
                        {
                            "at": now,
                            "tenant": key.tenant,
                            "memory_id": key.memory_id,
                            "state": "statistics_reclaimed",
                            "reason": "cold verified; retention elapsed",
                        }
                    )
                    return
                delay = min(delay, max(0.01, remaining))
            self._note(key, "converged", f"heat={stats.heat:.4f}; target={target}")
            self.store.schedule(key, now + delay)
            return
        if operation != "remove_hot_replica":
            available = await self.p2.resources()
            if target not in available or available[target] < memory.size:
                self._retry(key, "waiting for known " + target + " capacity")
                return
        # Inputs can arrive during an await. Superseded unsent plans are discarded.
        latest = self.store.stats[key]
        if latest.sequence != sequence or latest.memory != memory:
            self.store.wake(key, "newer_input")
            return
        intent = Intent(uuid4().hex, memory, observation.epoch, operation, target)
        self.store.pending[key] = Pending(intent, sequence)
        self._note(key, "action_submitted", operation)
        self.store.schedule(key, self.now() + self.settings.retry_seconds)
        try:
            # Save original ID first. Lost responses never cause blind submission of a new ID.
            await self.p2.submit(intent)
        except SubmissionRejectedError as exc:
            del self.store.pending[key]
            self._retry(key, str(exc))
            return
        except (ConnectionError, TimeoutError):
            self._retry(key, "submission response lost; query original action")
            return
        self.store.schedule(key, self.now() + self.settings.retry_seconds)

    def snapshot(self, key: MemoryKey) -> dict[str, object]:
        stats = self.store.stats.get(key)
        pending = self.store.pending.get(key)
        if stats is None:
            return {
                "tenant": key.tenant,
                "memory_id": key.memory_id,
                "tracked": False,
                "access_count": None,
                "heat": None,
                "evaluated_at": None,
                "desired": None,
                "version": None,
                "in_buffer": False,
                "pending_action": pending.intent.action_id if pending else None,
                "cold_since": None,
                "reclaim_at": None,
                "timer_scheduled": key in self.store.timers,
                "tracked_memories": len(self.store.stats),
                "reason": "not tracked; reload metadata on access",
            }
        return {
            "tenant": key.tenant,
            "memory_id": key.memory_id,
            "tracked": True,
            "cold_since": stats.cold_since,
            "reclaim_at": (
                stats.cold_since + self.settings.stats_retention_seconds
                if stats.cold_since is not None
                else None
            ),
            "timer_scheduled": key in self.store.timers,
            "tracked_memories": len(self.store.stats),
            "access_count": stats.access_count,
            "heat": round(stats.heat, 4) if stats.evaluated_at is not None else None,
            "evaluated_at": stats.evaluated_at,
            "desired": stats.desired if stats.evaluated_at is not None else None,
            "version": stats.memory.version,
            "in_buffer": key in self.store.buffer,
            "pending_action": pending.intent.action_id if pending else None,
            "reason": stats.last_reason,
        }
