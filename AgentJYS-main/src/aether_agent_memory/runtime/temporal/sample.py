"""Explicit engineering demo only; never registered by the P3 service."""

from aether_agent_memory.runtime.contracts.models import EffectStatus, Permission
from aether_agent_memory.runtime.foundation.sample import Sample

from .activities import StageContext
from .models import StepRequest, StepResult
from .registry import StageRegistry


def register_sample(registry: StageRegistry, sample: Sample) -> None:
    async def commit(step: StepRequest) -> StepResult:
        c = StageContext.current()
        result = await sample.run(c.context, c.task)
        return StepResult(
            outcome="done" if result.outcome == "committed" else "obsolete",
            result_ref=result.result_ref,
            effect_status=result.effect_status,
            reason_code="ENGINEERING_RESULT",
        )

    async def reconcile(step: StepRequest) -> StepResult:
        c = StageContext.current()
        result = await sample.recover(c.context, c.task)
        return StepResult(
            outcome="retry" if result.effect_status == EffectStatus.NO_EFFECT else "attention",
            effect_status=result.effect_status,
            reason_code="ENGINEERING_RECONCILED",
        )

    registry.register(sample.KIND, "commit", commit, reconcile, Permission.WRITE, "idempotent")
