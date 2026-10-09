"""Retire persisted summary continuations without rewriting original Working.

Task/stage entry points remain solely to drain old Temporal histories. There is
no summary producer, selector, body publisher, cache writer or version writer.
"""

from typing import Any, Protocol

from aether_agent_memory.remember.contracts.models import MemoryRef, MemorySnapshot
from aether_agent_memory.runtime.contracts.models import (
    EffectStatus,
    ErrorCode,
    RecordRef,
    RunResult,
    TaskRecord,
    TrustedContext,
)
from aether_agent_memory.runtime.foundation.common import fingerprint


class SummaryPort(Protocol):
    """Deprecated injection shape retained for existing pipeline construction."""

    async def select(
        self, ctx: TrustedContext, text: str, task_context: str, max_chars: int
    ) -> tuple[str, ...]: ...


class WorkingSummaries:
    def __init__(self, owner: Any, provider: SummaryPort | None = None) -> None:
        self.owner = owner
        # Keep constructor compatibility; a configured provider is never called.
        self.provider = provider

    def retire(self, ctx: TrustedContext, task: TaskRecord) -> RunResult:
        owner = self.owner
        with owner.uow.transaction() as tx:
            owner.tasks.guard(tx, task)
            owner.identity.revalidate(tx, ctx)
            if task.kind != "remember.summarize":
                tx.abort(ErrorCode.CONTRACT_VIOLATION, "not a retired summary task")
            # The frozen input supplies identity only. Do not hydrate its body or
            # load old model output, including already-published summary output.
            frozen = tx.get(task.input_ref)
            if isinstance(frozen, dict) and frozen.get("kind") == "working":
                ref = MemoryRef.model_validate(frozen["ref"])
                memory = ref.model_dump(mode="json")
                summary = tx.read("remember_working_summaries", ref.memory_id)
                matching = bool(
                    summary
                    and summary.get("memory") == memory
                    and summary.get("task_id") in {None, task.task_id}
                )
                if matching:
                    tx.write(
                        "remember_working_summaries",
                        ref.memory_id,
                        {
                            **summary,
                            "state": "obsolete",
                            "task_id": task.task_id,
                            "reason": "working_summary_retired",
                        },
                    )
                pending = tx.read("remember_pending", ref.memory_id)
                pointer = tx.read("remember_current", ref.memory_id)
                current = tx.get(RecordRef.model_validate(pointer)) if pointer else None
                if (
                    matching
                    and pending
                    and pending.get("ref") == memory
                    and pending.get("state") in {"scheduled", "waiting_summary", "pending"}
                    and (
                        pending.get("task_id") == task.task_id
                        or pending.get("summary_task_id") == task.task_id
                    )
                    and isinstance(current, dict)
                    and current.get("ref") == memory
                    and current.get("status") == "active"
                ):
                    # Release exactly the original held input. No follow-up task
                    # is admitted here; existing periodic scheduling handles it.
                    released = {
                        **pending,
                        "state": "pending",
                        "reschedule_operation_id": fingerprint([task.task_id, "summary_retired"]),
                    }
                    released.pop("task_id", None)
                    released.pop("summary_task_id", None)
                    tx.write("remember_pending", ref.memory_id, released)
        return RunResult(
            outcome="obsolete",
            effect_status=EffectStatus.NO_EFFECT,
            reason="Working summary production retired; original Working retained",
        )

    async def process(
        self, ctx: TrustedContext, task: TaskRecord, item: MemorySnapshot
    ) -> RunResult:
        return self.retire(ctx, task)

    async def generate(
        self, ctx: TrustedContext, task: TaskRecord, item: MemorySnapshot
    ) -> RunResult:
        return self.retire(ctx, task)

    async def commit(
        self, ctx: TrustedContext, task: TaskRecord, item: MemorySnapshot, prepared: dict[str, Any]
    ) -> RunResult:
        return self.retire(ctx, task)

    def commit_metadata(
        self, ctx: TrustedContext, task: TaskRecord, item: MemorySnapshot, prepared: dict[str, Any]
    ) -> RunResult:
        return self.retire(ctx, task)
