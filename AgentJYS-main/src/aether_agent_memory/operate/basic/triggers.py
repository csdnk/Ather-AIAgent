"""Durable notification admission: one indexed wake-up per scoped memory.

This queue stores identifiers/times, never bodies or a timestamp per read. The
Foundation periodic worker owns execution; this module creates no timer thread.
"""

from datetime import datetime
from typing import Any

from aether_agent_memory.operate.standalone.policy import Settings
from aether_agent_memory.remember.contracts.models import MemoryRef
from aether_agent_memory.runtime.contracts.models import ErrorCode, EventEnvelope, TrustedContext
from aether_agent_memory.runtime.foundation.common import FoundationError, fingerprint, later
from aether_agent_memory.runtime.foundation.placement_authority import placement_context
from aether_agent_memory.runtime.foundation.requests import event_context
from aether_agent_memory.runtime.storage.ports import MetadataTransaction

from .service import Operate

ACTIVE = {"pending", "running", "retry_wait", "recovery_wait", "attention_required"}


def seconds(timestamp: str) -> float:
    return datetime.fromisoformat(timestamp.replace("Z", "+00:00")).timestamp()


class TriggerSchedule(Operate):
    """Operate admission and durable wake-ups, sharing the event transaction boundary."""

    settings: Settings

    @staticmethod
    def key(memory: MemoryRef) -> str:
        return fingerprint([memory.scope.model_dump(mode="json"), memory.memory_id])

    def capability_signature(self) -> str:
        return fingerprint(
            [
                getattr(self.executor, "provider_id", "unknown"),
                getattr(self.executor, "supported_moves", None),
                getattr(self.executor, "policy_managed", False),
                getattr(self, "paused", False),
            ]
        )

    def schedule_at(
        self, tx: MetadataTransaction, key: str, view: dict[str, Any], when: str | None, reason: str
    ) -> None:
        old_key = view.pop("due_key", None)
        if old_key:
            tx.raw.delete("p3_rf_operate_due", "system", old_key)
        view["next_evaluation_at"] = when if reason != "retire_stats" else None
        view["next_retention_at"] = when if reason == "retire_stats" else None
        view["wake_reason"] = reason
        view["trigger_schema"] = 1
        if when is not None:
            due_key = f"{int(seconds(when) * 1_000_000):020d}:{key}"
            view["due_key"] = due_key
            tx.write("operate_due", due_key, {"memory_key": key, "due_at": when})
        tx.write("operate_views", key, view)

    def evaluation_attempt_budget(self, *, cleanup: bool) -> int:
        # A 24h autonomous job needs room for 1/5/15/60m backoff, not a
        # request's default three attempts. Deadline and query budgets still bound it.
        return self.tasks.max_attempts if cleanup else max(32, self.tasks.max_attempts)

    def evaluation_not_before(self, view: dict[str, Any]) -> str:
        now = self.identity.clock()
        last = view.get("last_evaluated_at")
        earliest = later(last, self.settings.evaluation_window_seconds) if last else now
        # A new access never bypasses a dependency failure's backoff.
        return max(now, earliest, view.get("retry_not_before") or now)

    def park_capability(self, tx: MetadataTransaction, key: str, view: dict[str, Any]) -> None:
        view["waiting_capability"] = self.capability_signature()
        self.schedule_at(tx, key, view, None, "capability_change")

    def schedule_evaluation(
        self,
        tx: MetadataTransaction,
        ctx: TrustedContext,
        memory: MemoryRef,
        trigger: str,
        *,
        cleanup: bool,
        permanent: bool,
        trigger_kind: str,
    ) -> None:
        key = self.key(memory)
        view = tx.read("operate_views", key)
        if cleanup:
            # Lifecycle cleanup bypasses windows, dependency backoff and capability gates.
            self.enqueue(
                tx,
                ctx,
                memory,
                trigger,
                cleanup=True,
                permanent=permanent,
                trigger_kind=trigger_kind,
            )
            if view and view["memory"] == memory.model_dump(mode="json"):
                self.schedule_at(tx, key, view, None, "cleanup")
            return
        if not view:
            return
        view["dormant"] = False
        if (
            not self.should_evaluate(cleanup=False)
            or self.paused
            or view.get("waiting_capability") == self.capability_signature()
        ):
            self.park_capability(tx, key, view)
            return
        view.pop("waiting_capability", None)
        when, reason = self.evaluation_not_before(view), "new_input"
        pressure_due = view.get("next_evaluation_at")
        if view.get("wake_reason") == "buffer_high_watermark" and pressure_due:
            pressure_due = max(
                self.identity.clock(), pressure_due, view.get("retry_not_before") or pressure_due
            )
            if seconds(pressure_due) <= seconds(when):
                when, reason = pressure_due, "buffer_high_watermark"
        self.schedule_at(tx, key, view, when, reason)

    def pending(
        self, tx: MetadataTransaction, memory: MemoryRef, exclude_task_id: str | None = None
    ) -> bool:
        # O(1): do not enumerate the complete task history on every notification.
        head = tx.read("operate_evaluation_head", self.key(memory))
        if (
            head
            and head["memory"] == memory.model_dump(mode="json")
            and head["task_id"] != exclude_task_id
        ):
            row = tx.read("tasks", head["task_id"])
            if row and row["record"]["state"] in ACTIVE:
                return True
        return False

    def retry_evaluation(
        self, tx: MetadataTransaction, key: str, view: dict[str, Any], reason: str
    ) -> None:
        attempts = view.get("failure_count", 0) + 1
        delay = self.settings.retry_delays[min(attempts - 1, len(self.settings.retry_delays) - 1)]
        view["failure_count"] = attempts
        view["retry_not_before"] = later(self.identity.clock(), delay)
        view["dormant"] = False
        self.schedule_at(tx, key, view, view["retry_not_before"], reason)

    def refresh_scheduler(self, tx: MetadataTransaction) -> None:
        """One bounded migration sweep, and another only when capabilities change."""
        signature = self.capability_signature()
        control = tx.read("operate_scheduler", "control") or {}
        if control.get("signature") != signature:
            control = {"signature": signature, "cursor": "", "refreshing": True}
        if not control.get("refreshing"):
            return
        rows = tx.rows_after("operate_views", control["cursor"], limit=self.settings.batch_size)
        for key, view in rows:
            legacy = view.get("trigger_schema") != 1
            changed = bool(view.get("waiting_capability"))
            if (legacy or changed) and not view.get("cleanup_completed"):
                if not view.get("cleanup") and (
                    not self.should_evaluate(cleanup=False) or self.paused
                ):
                    self.park_capability(tx, key, view)
                elif view.get("scheduler_event"):
                    view.pop("waiting_capability", None)
                    view["dormant"] = False
                    self.schedule_at(
                        tx,
                        key,
                        view,
                        self.evaluation_not_before(view),
                        "capability_change" if changed else "upgrade",
                    )
            control["cursor"] = key
        control["refreshing"] = len(rows) == self.settings.batch_size
        tx.write("operate_scheduler", "control", control)

    def periodic_rows(
        self, tx: MetadataTransaction, cursor: str, limit: int
    ) -> list[tuple[str, Any]]:
        self.refresh_scheduler(tx)
        # Timestamp prefix uses the existing (namespace, tenant, key) primary index.
        # At most one page is fetched, even when every remaining wake-up is in the future.
        rows = tx.rows_after("operate_due", cursor, limit=min(limit, self.settings.batch_size))
        now = self.identity.clock()
        return [(key, value) for key, value in rows if seconds(value["due_at"]) <= seconds(now)]

    def periodic_due(self, tx: MetadataTransaction, due_key: str, tick_id: str) -> int:
        due = tx.read("operate_due", due_key)
        if not due:
            return 0
        key = due["memory_key"]
        view = tx.read("operate_views", key)
        if not view or view.get("due_key") != due_key:
            tx.raw.delete("p3_rf_operate_due", "system", due_key)
            return 0
        return self.periodic_item(tx, key, tick_id)

    def drop_heat(self, tx: MetadataTransaction, key: str) -> None:
        tx.raw.delete("p3_rf_operate_heat", "system", key)

    def periodic_item(self, tx: MetadataTransaction, key: str, tick_id: str) -> int:
        view = tx.read("operate_views", key)
        if not view or view.get("cleanup_completed"):
            return 0
        now = self.identity.clock()
        if view.get("wake_reason") == "retire_stats":
            if view.get("next_retention_at") and seconds(view["next_retention_at"]) <= seconds(now):
                self.drop_heat(tx, key)
                self.schedule_at(tx, key, view, None, "stable_cold")
            return 0
        if not view.get("cleanup") and (not self.should_evaluate(cleanup=False) or self.paused):
            self.park_capability(tx, key, view)
            return 0
        due = view.get("next_evaluation_at")
        if (
            view.get("dormant")
            or not view.get("scheduler_event")
            or not due
            or seconds(due) > seconds(now)
        ):
            return 0
        memory = MemoryRef.model_validate(view["memory"])
        if not view.get("cleanup") and self.pending(tx, memory):
            # Only inspect task state on this wake; never re-evaluate or resubmit it.
            self.schedule_at(
                tx,
                key,
                view,
                later(now, self.settings.evaluation_window_seconds),
                "pending_completion",
            )
            return 0
        try:
            trigger = fingerprint([tick_id, key, due])
            ctx = (
                event_context(tx, EventEnvelope.model_validate(view["scheduler_event"]), now)
                if view.get("cleanup")
                else placement_context(
                    self.identity,
                    tx,
                    key,
                    fingerprint(["operate", trigger, memory.model_dump(mode="json")]),
                )
            )
            self.identity.revalidate(tx, ctx)
        except FoundationError as exc:
            if exc.code not in {ErrorCode.FORBIDDEN, ErrorCode.UNAUTHENTICATED}:
                raise
            # A revoked event is not retryable input. A new authorized event can wake it.
            view["dormant"] = True
            self.schedule_at(tx, key, view, None, "authorization_required")
            return 0
        if not view.get("cleanup"):
            # Autonomous reconciliation may outlive the originating request. Each
            # stage still rechecks current identity/epoch and memory permission.
            ctx = ctx.model_copy(
                update={"deadline_at": later(now, self.settings.evaluation_timeout_seconds)}
            )
        self.enqueue(
            tx,
            ctx,
            memory,
            trigger,
            cleanup=view.get("cleanup", False),
            permanent=view.get("permanent", False),
            trigger_kind=view.get("wake_reason", "due"),
        )
        self.schedule_at(tx, key, view, None, "in_flight")
        return 1

    def periodic(self, tick_id: str) -> int:
        with self.uow.transaction() as tx:
            rows = self.periodic_rows(tx, "", self.settings.batch_size)
        count = 0
        for key, _ in rows:
            try:
                with self.uow.transaction() as tx:
                    count += self.periodic_due(tx, key, tick_id)
            except FoundationError:
                continue
        return count
