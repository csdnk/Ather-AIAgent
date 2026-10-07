"""Persisted integration of the independent heat policy with RF tasks and actions.

Shared policy and models provide the heat calculation. Runtime scheduling has
one owner: Foundation. Every due time, input and action survives process exit.
"""

import asyncio
from dataclasses import asdict
from datetime import datetime
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
from aether_agent_memory.runtime.foundation.common import FoundationError, fingerprint, later
from aether_agent_memory.runtime.foundation.requests import event_context
from aether_agent_memory.runtime.foundation.transactions import native
from aether_agent_memory.runtime.storage.ports import MetadataTransaction

from .service import Operate


def seconds(timestamp: str) -> float:
    return datetime.fromisoformat(timestamp.replace("Z", "+00:00")).timestamp()


class ContinuousOperate(Operate):
    def __init__(self, *args: Any, settings: Settings | None = None, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.settings = settings or Settings()

    @staticmethod
    def key(memory: MemoryRef) -> str:
        return fingerprint([memory.scope.model_dump(mode="json"), memory.memory_id])

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
        view["next_evaluation_at"] = self.identity.clock()
        view["dormant"] = False
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
            sql.write("operate_heat", key, {**asdict(stats), "version": memory.version})
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
            reason = f"heat={stats.heat:.6f}; desired={target.value}; hysteresis_v1"
            levels = [Tier.COLD, Tier.WARM, Tier.HOT]
            current_index, target_index = levels.index(inputs.current_tier), levels.index(target)
            if self.paused or inputs.coverage != "complete":
                outcome, target, reason = (
                    "defer",
                    inputs.current_tier,
                    "observation incomplete or paused",
                )
            elif current_index != target_index and inputs.available_bytes < inputs.content_bytes:
                outcome, target, reason = "defer", inputs.current_tier, "copy capacity unavailable"
            elif current_index < target_index:
                outcome, target = "promote", levels[current_index + 1]
            elif current_index > target_index:
                outcome, target = "demote", levels[current_index - 1]
            tx.write("operate_heat", key, {**asdict(stats), "version": inputs.memory.version})
            view = tx.read("operate_views", key)
            if view:
                delay = (
                    self.settings.retry_seconds
                    if outcome != "keep"
                    else next_delay(stats, seconds(self.identity.clock()), self.settings)
                )
                view["next_evaluation_at"] = later(self.identity.clock(), delay)
                tx.write("operate_views", key, view)
        return PlacementDecision(
            decision_id=fingerprint([inputs.model_dump(mode="json"), asdict(stats), outcome]),
            memory=inputs.memory,
            outcome=outcome,
            current_tier=inputs.current_tier,
            target_tier=target,
            reason=reason,
            policy_version="continuous_heat_v1",
            storage_watermark=inputs.storage_watermark,
            access_watermark=inputs.access_watermark,
        )

    def pending(
        self, tx: MetadataTransaction, memory: MemoryRef, exclude_task_id: str | None = None
    ) -> bool:
        for _, row in tx.rows("tasks"):
            task = row["record"]
            if task["task_id"] == exclude_task_id:
                continue
            if task["kind"] != "operate.evaluate" or task["state"] not in {
                "pending",
                "running",
                "retry_wait",
                "recovery_wait",
            }:
                continue
            if task["subject"]["scope"] != memory.scope.model_dump(mode="json"):
                continue
            content = tx.read("operate_evaluation_inputs", task["task_id"])
            if content == self.key(memory):
                return True
        return False

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
                output = value
                if (
                    output == {"cache_cleanup": "completed"}
                    and view.get("cleanup")
                    and view.get("permanent", False) == inputs["permanent"]
                ):
                    view["cleanup_completed"] = True
                    tx.write("operate_views", key, view)
                    tx.raw.delete("p3_rf_operate_heat", "system", key)
                return
            heat = tx.read("operate_heat", key)
            if not heat or heat["desired"] != "cold" or self.pending(tx, memory, task.task_id):
                return
            now = seconds(self.identity.clock())
            since = heat.get("cold_since")
            if since is None:
                heat["cold_since"] = now
                tx.write("operate_heat", key, heat)
                return
            if now - since < self.settings.stats_retention_seconds:
                return
            pending = tx.read("operate_pending", key)
            action = tx.read("operate_actions", pending) if pending else None
            if action and action["state"] not in {"succeeded", "failed", "cancelled"}:
                return
        observation = await self.executor.observe(ctx, memory, "original")
        if observation.tier != Tier.COLD or not observation.readable:
            return
        with self.uow.transaction() as tx:
            self.identity.revalidate(tx, ctx)
            if tx.read("operate_views", key) != view or tx.read("operate_heat", key) != heat:
                return
            if (
                self.memories.final_guard(tx, ctx, (memory,), "actuate").items[0].decision
                != "allowed"
            ):
                return
        await asyncio.to_thread(self.executor.purge, memory, permanent=False, ctx=ctx)
        with self.uow.transaction() as tx:
            self.identity.revalidate(tx, ctx)
            if tx.read("operate_views", key) != view or tx.read("operate_heat", key) != heat:
                return
            # Keep the durable storage/authority watermark for the next read event.
            # Only heat and optional bytes are disposable, not scheduling provenance.
            view["dormant"] = True
            tx.write("operate_views", key, view)
            tx.raw.delete("p3_rf_operate_heat", "system", key)
        return

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
        return result

    def periodic(self, tick_id: str) -> int:
        with self.uow.transaction() as tx:
            keys = [key for key, _ in tx.rows("operate_views")]
        count = 0
        for key in keys:
            try:
                with self.uow.transaction() as tx:
                    count += self.periodic_item(tx, key, tick_id)
            except FoundationError:
                continue
        return count

    def periodic_item(self, tx: MetadataTransaction, key: str, tick_id: str) -> int:
        count = 0
        now = self.identity.clock()
        view = tx.read("operate_views", key)
        if (
            not view
            or view.get("cleanup_completed")
            or view.get("dormant")
            or not self.should_evaluate(cleanup=view.get("cleanup", False))
            or not view.get("scheduler_event")
            or view.get("next_evaluation_at", "") > now
        ):
            return count
        memory = MemoryRef.model_validate(view["memory"])
        ctx = event_context(tx, EventEnvelope.model_validate(view["scheduler_event"]), now)
        self.identity.revalidate(tx, ctx)
        if not self.pending(tx, memory):
            self.enqueue(
                tx,
                ctx,
                memory,
                fingerprint([tick_id, key]),
                cleanup=view.get("cleanup", False),
                permanent=view.get("permanent", False),
                trigger_kind="periodic",
            )
            count += 1
        view["next_evaluation_at"] = later(now, self.settings.retry_seconds)
        tx.write("operate_views", key, view)
        return count
