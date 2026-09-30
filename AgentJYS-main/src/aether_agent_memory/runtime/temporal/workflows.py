"""Deterministic orchestration. All business I/O and authority checks live in Activities."""

import asyncio
from datetime import datetime, timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError

with workflow.unsafe.imports_passed_through():
    from aether_agent_memory.runtime.contracts.models import EffectStatus

    from .models import (
        CloseRequest,
        ControlIntent,
        StepRequest,
        StepResult,
        TaskPlan,
        WorkflowInput,
    )


@workflow.defn
class P3TaskWorkflow:
    def __init__(self) -> None:
        self.controls: dict[str, ControlIntent] = {}
        self.cancel_requested = False
        self.reconcile_requested = False
        self.active: asyncio.Future[StepResult] | None = None

    @workflow.signal
    def control(self, intent: ControlIntent) -> None:
        if intent.control_id in self.controls:
            return
        self.controls[intent.control_id] = intent
        if intent.action == "cancel":
            self.cancel_requested = True
            if self.active is not None:
                self.active.cancel()
        else:
            self.reconcile_requested = True

    @workflow.query
    def state(self) -> dict[str, object]:
        return {
            "cancel_requested": self.cancel_requested,
            "acknowledged_controls": sorted(self.controls),
        }

    async def finish(self, job: WorkflowInput, result: StepResult) -> StepResult:
        closed: StepResult = await workflow.execute_activity(
            "p3.close",
            CloseRequest(job=job, result=result, controls=tuple(self.controls.values())),
            result_type=StepResult,
            start_to_close_timeout=timedelta(seconds=10),
            retry_policy=RetryPolicy(maximum_attempts=3),
        )
        return closed

    async def pause(self, seconds: float) -> None:
        try:
            await workflow.sleep(seconds)
        except asyncio.CancelledError:
            self.cancel_requested = True

    @workflow.run
    async def run(self, job: WorkflowInput) -> StepResult:
        plan: TaskPlan = await workflow.execute_activity(
            "p3.plan",
            job,
            result_type=TaskPlan,
            start_to_close_timeout=timedelta(seconds=10),
            retry_policy=RetryPolicy(maximum_attempts=3),
        )
        stage, ordinal, mode = plan.first_stage, 0, job.entry
        while True:
            policy = plan.stages[stage]
            if self.reconcile_requested:
                mode, self.reconcile_requested = "reconcile", False
            if self.cancel_requested:
                mode = "reconcile"
            deadline = plan.deadline_at if mode == "execute" else plan.query_deadline_at
            remaining = (datetime.fromisoformat(deadline) - workflow.now()).total_seconds()
            if remaining <= 0:
                if mode == "execute":
                    mode = "reconcile"
                    continue
                return await self.finish(
                    job,
                    StepResult(
                        outcome="attention",
                        effect_status=EffectStatus.UNKNOWN,
                        reason_code="DEADLINE_EXCEEDED",
                    ),
                )
            try:
                self.active = workflow.start_activity(
                    "p3.step",
                    StepRequest(job=job, stage=stage, ordinal=ordinal, mode=mode),
                    result_type=StepResult,
                    start_to_close_timeout=timedelta(
                        seconds=min(policy.timeout_seconds, remaining)
                    ),
                    heartbeat_timeout=timedelta(seconds=min(10, policy.timeout_seconds)),
                    retry_policy=RetryPolicy(maximum_attempts=1),
                    cancellation_type=workflow.ActivityCancellationType.TRY_CANCEL,
                )
                result: StepResult = await self.active
            except asyncio.CancelledError:
                self.cancel_requested = True
                mode = "reconcile"
                continue
            except ActivityError:
                mode = (
                    "execute"
                    if policy.effect_mode == "read" and not self.cancel_requested
                    else "reconcile"
                )
                await self.pause(plan.retry_seconds)
                continue
            finally:
                self.active = None
            if result.outcome == "done":
                if result.next_stage is None:
                    return result
                if self.cancel_requested:
                    return await self.finish(
                        job,
                        StepResult(
                            outcome="obsolete",
                            effect_status=result.effect_status,
                            reason_code="CANCEL_REQUESTED",
                        ),
                    )
                stage, ordinal, mode = result.next_stage, ordinal + 1, "execute"
                continue
            if result.outcome in {"attention", "failed", "obsolete"}:
                return await self.finish(job, result)
            if self.cancel_requested and result.outcome == "retry":
                return await self.finish(
                    job,
                    StepResult(
                        outcome="obsolete",
                        effect_status=result.effect_status,
                        reason_code="CANCEL_REQUESTED",
                    ),
                )
            mode = "reconcile" if result.outcome == "query" else "execute"
            await self.pause(plan.retry_seconds)


@workflow.defn
class EventDeliveryWorkflow(P3TaskWorkflow):
    @workflow.run
    async def run(self, job: WorkflowInput) -> StepResult:
        return await super().run(job)


def workflow_name(kind: str) -> str:
    return "EventDeliveryWorkflow" if kind == "runtime.event_delivery" else "P3TaskWorkflow"
