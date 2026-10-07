"""Deterministic orchestration. All business I/O and authority checks live in Activities."""

import asyncio
from contextlib import suppress
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

    async def pause(self, seconds: float, *, interruptible: bool = False) -> None:
        try:
            if interruptible:
                controls = len(self.controls)
                with suppress(TimeoutError):
                    await workflow.wait_condition(
                        lambda: len(self.controls) > controls,
                        timeout=timedelta(seconds=seconds),
                    )
            else:
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
        # Keep historical workflow replay on the old timer commands.
        paced_operate = job.kind == "operate.evaluate"
        paced_operate = paced_operate and workflow.patched("operate-backoff-v1")
        failures = 0
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
                failures += 1
                delay = operate_retry_delay(failures) if paced_operate else plan.retry_seconds
                if paced_operate:
                    remaining = (datetime.fromisoformat(deadline) - workflow.now()).total_seconds()
                    await self.pause(min(delay, max(0, remaining)), interruptible=True)
                else:
                    await self.pause(delay)
                continue
            finally:
                self.active = None
            if result.outcome == "done":
                failures = 0
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
            failures += 1
            delay = operate_retry_delay(failures) if paced_operate else plan.retry_seconds
            if paced_operate:
                remaining = (datetime.fromisoformat(deadline) - workflow.now()).total_seconds()
                await self.pause(min(delay, max(0, remaining)), interruptible=True)
            else:
                await self.pause(delay)


def operate_retry_delay(failures: int) -> float:
    return (60, 300, 900, 3600)[min(max(failures - 1, 0), 3)]


@workflow.defn
class EventDeliveryWorkflow(P3TaskWorkflow):
    @workflow.run
    async def run(self, job: WorkflowInput) -> StepResult:
        return await super().run(job)


def workflow_name(kind: str) -> str:
    return "EventDeliveryWorkflow" if kind == "runtime.event_delivery" else "P3TaskWorkflow"
