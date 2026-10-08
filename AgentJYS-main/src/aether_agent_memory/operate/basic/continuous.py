"""Persisted integration of the independent heat policy with RF tasks and actions.

Shared policy and models provide the heat calculation. Runtime scheduling has
one owner: Foundation. Every due time, input and action survives process exit.
"""

from dataclasses import asdict
from typing import Any

from aether_agent_memory.operate.contracts.models import (
    PlacementDecision,
    SchedulingInput,
    Tier,
)
from aether_agent_memory.operate.standalone.models import Memory, MemoryKey, Stats
from aether_agent_memory.operate.standalone.policy import (
    Settings,
    accumulate,
    evaluate,
    next_delay,
)
from aether_agent_memory.recall.contracts.models import AccessObserved
from aether_agent_memory.remember.contracts.models import MemoryRef, StorageChanged
from aether_agent_memory.runtime.contracts.models import (
    EventEnvelope,
    TaskRecord,
    TrustedContext,
)
from aether_agent_memory.runtime.contracts.ports import Transaction
from aether_agent_memory.runtime.foundation.common import fingerprint, later
from aether_agent_memory.runtime.foundation.transactions import native
from aether_agent_memory.runtime.storage.ports import MetadataTransaction

from .buffer import BufferSchedule
from .triggers import ACTIVE, seconds


class ContinuousOperate(BufferSchedule):
    def __init__(self, *args: Any, settings: Settings | None = None, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.settings = settings or Settings()

    def consume(self, tx: Transaction, event: EventEnvelope) -> None:
        sql = native(tx)
        memory = (
            AccessObserved.model_validate(event.payload).memory
            if event.event_type == "recall.access"
            else StorageChanged.model_validate(event.payload).memory
        )
        key = self.key(memory)
        old = sql.read("operate_views", key)
        previous_reads = old["access_watermark"] if old else 0
        super().consume(tx, event)
        view = sql.read("operate_views", key)
        if view is None or view["memory"] != memory.model_dump(mode="json"):
            return
        # Parent admission rejected old/duplicate/non-read events: do not wake them.
        if view.get("event", {}).get("event_id") != event.event_id or (
            old and old.get("event", {}).get("event_id") == event.event_id
        ):
            return
        if old and old.get("due_key") and old["due_key"] != view.get("due_key"):
            # Version replacement resets the view; remove its superseded queue pointer.
            sql.raw.delete("p3_rf_operate_due", "system", old["due_key"])
        raw = sql.read("operate_heat", key)
        if raw and raw["version"] != memory.version:
            sql.raw.delete("p3_rf_operate_heat", "system", key)
            raw = None
        if event.event_type == "memory.changed":
            change = StorageChanged.model_validate(event.payload)
            view["content_hash"] = change.content_hash
            view["content_bytes"] = change.content_bytes
            view["importance"] = change.importance
        if event.event_type == "recall.access" and view["access_watermark"] > previous_reads:
            stats = self.stats(
                memory,
                view.get("content_hash", "0" * 64),
                view.get("content_bytes", 1),
                view.get("importance", 0.2),
                raw,
            )
            accumulate(
                stats, seconds(event.occurred_at), seconds(self.identity.clock()), self.settings
            )
            stats.cold_since = None
            self.write_heat(sql, key, memory, {**asdict(stats), "version": memory.version})
        if sql.read("operate_heat", key) is None:
            self.drop_heat(sql, key)
        sql.write("operate_views", key, view)

    def stats(
        self,
        ref: MemoryRef,
        digest: str,
        size: int,
        importance: float,
        raw: dict[str, Any] | None,
    ) -> Stats:
        memory = Memory(
            MemoryKey(
                ref.scope.tenant_id,
                ref.memory_id,
                ref.scope.application_id,
                ref.scope.user_id,
                ref.scope.agent_id,
            ),
            ref.version,
            digest,
            max(size, 1),
            importance,
        )
        values = {k: v for k, v in (raw or {}).items() if k not in {"memory", "version"}}
        values.setdefault("updated_at", seconds(self.identity.clock()))
        return Stats(memory=memory, **values)

    def decide(self, ctx: TrustedContext, inputs: SchedulingInput) -> PlacementDecision:
        key = self.key(inputs.memory)
        with self.uow.transaction() as tx:
            self.identity.revalidate(tx, ctx)
            raw = tx.read("operate_heat", key)
            stats = self.stats(
                inputs.memory, "0" * 64, inputs.content_bytes, inputs.importance, raw
            )
            target = Tier(evaluate(stats, seconds(self.identity.clock()), self.settings))
            if target != Tier.COLD:
                stats.cold_since = None
            outcome = "keep"
            reason = f"heat={stats.heat:.6f}; desired={target.value}; two_tier_hysteresis_v2"
            if self.paused or inputs.coverage != "complete" or inputs.current_tier == Tier.WARM:
                outcome, target, reason = (
                    "defer",
                    inputs.current_tier,
                    "observation incomplete or paused",
                )
            elif (
                target == Tier.HOT
                and inputs.current_tier != target
                and inputs.available_bytes < inputs.content_bytes
            ):
                outcome, target, reason = "defer", inputs.current_tier, "copy capacity unavailable"
            elif inputs.current_tier != target:
                outcome = "promote" if target == Tier.HOT else "demote"
            self.write_heat(
                tx, key, inputs.memory, {**asdict(stats), "version": inputs.memory.version}
            )
            view = tx.read("operate_views", key)
            if view:
                view["last_evaluated_at"] = self.identity.clock()
                view["evaluated_access_watermark"] = view["access_watermark"]
                view["evaluated_storage_watermark"] = inputs.storage_watermark
                tx.write("operate_views", key, view)
        return PlacementDecision(
            decision_id=fingerprint([inputs.model_dump(mode="json"), asdict(stats), outcome]),
            memory=inputs.memory,
            outcome=outcome,
            current_tier=inputs.current_tier,
            target_tier=target,
            reason=reason,
            policy_version="ceph_redis_heat_v2",
            storage_watermark=inputs.storage_watermark,
            access_watermark=inputs.access_watermark,
        )

    async def before_completion(
        self, ctx: TrustedContext, task: TaskRecord, value: dict[str, Any]
    ) -> None:
        with self.uow.transaction() as tx:
            inputs = tx.get(task.input_ref)
            if inputs is None:
                return
            memory = MemoryRef.model_validate(inputs["memory"])
            key = self.key(memory)
            view = tx.read("operate_views", key)
            if view is None or view["memory"] != memory.model_dump(mode="json"):
                return
            if inputs["cleanup"]:
                if (
                    value == {"cache_cleanup": "completed"}
                    and view.get("cleanup")
                    and view.get("permanent", False) == inputs["permanent"]
                ):
                    view["cleanup_completed"] = True
                    self.schedule_at(tx, key, view, None, "cleanup_completed")
                    self.drop_heat(tx, key)
                return
            if view.get("cleanup") or self.pending(tx, memory, task.task_id):
                return
            head = tx.read("operate_evaluation_head", key)
            if (head and head["task_id"] != task.task_id) or view.get(
                "trigger_completion_task"
            ) == task.task_id:
                return  # Lost completion response: never advance backoff/retention twice.
            decision = value.get("decision", {})
            if decision.get("reason") == "unsupported_tier_transition":
                view["trigger_completion_task"] = task.task_id
                self.park_capability(tx, key, view)
                return
            succeeded = (
                decision.get("outcome") == "keep" or value.get("action_state") == "succeeded"
            )
            if not succeeded:
                view["trigger_completion_task"] = task.task_id
                self.retry_evaluation(tx, key, view, "temporary_failure")
                return
            heat = tx.read("operate_heat", key)
            if not heat:
                # Upgrading an already running task: re-evaluate with the new policy.
                view["trigger_completion_task"] = task.task_id
                self.schedule_at(tx, key, view, self.evaluation_not_before(view), "upgrade")
                return
            view["failure_count"] = 0
            view.pop("retry_not_before", None)
            dirty = view["access_watermark"] > view.get("evaluated_access_watermark", -1) or view[
                "storage_watermark"
            ] > view.get("evaluated_storage_watermark", -1)
            if dirty:
                view["trigger_completion_task"] = task.task_id
                self.schedule_at(tx, key, view, self.evaluation_not_before(view), "new_input")
                return
            tx.write("operate_views", key, view)
        # Fresh evidence before sleeping: a keep/defer label is not storage proof.
        observation = await self.executor.observe(ctx, memory, "original")
        with self.uow.transaction() as tx:
            self.identity.revalidate(tx, ctx)
            if tx.read("operate_views", key) != view or tx.read("operate_heat", key) != heat:
                return  # A new event already installed its own wake-up.
            if (
                self.memories.final_guard(tx, ctx, (memory,), "actuate").items[0].decision
                != "allowed"
            ):
                return
            view["trigger_completion_task"] = task.task_id
            if not observation.readable or observation.tier.value != heat["desired"]:
                self.retry_evaluation(tx, key, view, "placement_unconfirmed")
                return
            if observation.tier == Tier.COLD:
                view["dormant"] = True
                # One metadata-only expiry, not another heat calculation or P2 read.
                heat["cold_since"] = seconds(self.identity.clock())
                self.write_heat(tx, key, memory, heat)
                self.schedule_at(
                    tx,
                    key,
                    view,
                    later(self.identity.clock(), self.settings.stats_retention_seconds),
                    "retire_stats",
                )
            else:
                stats = self.stats(
                    memory,
                    heat["memory"]["content_hash"],
                    heat["memory"]["size"],
                    heat["memory"]["importance"],
                    heat,
                )
                delay = next_delay(stats, seconds(self.identity.clock()), self.settings)
                view["dormant"] = False
                # No repeated five-minute audit; the next wake is the predicted crossing.
                self.schedule_at(
                    tx,
                    key,
                    view,
                    later(self.identity.clock(), delay) if delay is not None else None,
                    "threshold_crossing",
                )

    def enqueue(
        self,
        tx: MetadataTransaction,
        ctx: TrustedContext,
        memory: MemoryRef,
        trigger: str,
        *,
        cleanup: bool,
        permanent: bool,
        trigger_kind: str = "unrecorded",
    ) -> str:
        # Coalesce notifications, never the access statistics. Cleanup has its
        # own admission path so archive/delete cannot hide behind a read task.
        head = tx.read("operate_evaluation_head", self.key(memory)) if not cleanup else None
        row = tx.read("tasks", head["task_id"]) if head else None
        if (
            head
            and head["memory"] == memory.model_dump(mode="json")
            and row
            and row["record"]["state"] in ACTIVE
        ):
            return str(head["task_id"])
        result = super().enqueue(
            tx,
            ctx,
            memory,
            trigger,
            cleanup=cleanup,
            permanent=permanent,
            trigger_kind=trigger_kind,
        )
        tx.write("operate_evaluation_inputs", result, self.key(memory))
        if not cleanup:
            tx.write(
                "operate_evaluation_head",
                self.key(memory),
                {"task_id": result, "memory": memory.model_dump(mode="json")},
            )
        return result
