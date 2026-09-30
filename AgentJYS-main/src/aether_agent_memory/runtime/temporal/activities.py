"""Fenced I/O boundary. Workflow history receives references and bounded reason codes."""

import asyncio
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
from contextvars import ContextVar, copy_context
from dataclasses import dataclass
from datetime import datetime
from typing import TypeVar

from temporalio import activity
from temporalio.exceptions import ApplicationError

from aether_agent_memory.runtime.contracts.models import (
    EffectStatus,
    ErrorCode,
    Permission,
    TaskRecord,
    TaskState,
    TrustedContext,
)
from aether_agent_memory.runtime.foundation.common import FoundationError, fingerprint, later
from aether_agent_memory.runtime.foundation.storage import SQLiteTransaction, business_write_guard
from aether_agent_memory.runtime.foundation.tasks import TERMINAL

from .ledger import ExecutionLedger
from .locking import ExecutionLimits
from .models import CloseRequest, ExecutionRef, StepRequest, StepResult, TaskPlan, WorkflowInput
from .registry import StageRegistry

T = TypeVar("T")
_context: ContextVar["StageContext"] = ContextVar("p3_temporal_stage")


@dataclass(frozen=True)
class StageContext:
    ledger: ExecutionLedger
    task: TaskRecord
    context: TrustedContext
    execution: ExecutionRef
    executor: ThreadPoolExecutor

    @staticmethod
    def current() -> "StageContext":
        return _context.get()

    def guard(self, tx: SQLiteTransaction) -> TaskRecord:
        return self.ledger.guard(tx, self.task.task_id, self.execution)

    def commit_fence(self, tx: SQLiteTransaction) -> None:
        """Protect every domain transaction, including atomic completion in a stage."""
        _, current = self.ledger.tasks.load(tx, self.task.task_id)
        if current.execution != self.execution:
            tx.abort(ErrorCode.VERSION_CONFLICT, "business write belongs to an old delivery")
        self.ledger.tasks.identity.revalidate(tx, self.context)
        if fingerprint(tx.get(current.input_ref)) != current.input_hash:
            tx.abort(ErrorCode.VERSION_CONFLICT, "business input changed")

    async def blocking(self, function: Callable[[], T]) -> T:
        """Bounded executor; captured execution must still guard every commit."""
        context = copy_context()
        return await asyncio.get_running_loop().run_in_executor(
            self.executor, context.run, function
        )


class Activities:
    def __init__(
        self,
        ledger: ExecutionLedger,
        registry: StageRegistry,
        limits: ExecutionLimits,
        executor: ThreadPoolExecutor,
    ) -> None:
        self.ledger, self.registry, self.limits, self.executor = ledger, registry, limits, executor
        self.tasks = ledger.tasks

    @activity.defn(name="p3.plan")
    async def plan(self, job: WorkflowInput) -> TaskPlan:
        try:
            policies = self.registry.policies(job.kind)
            with self.tasks.uow.transaction() as tx:
                binding = self.ledger.verify_job(tx, job)
                _, task = self.tasks.load(tx, job.job_id)
                if job.plan_version != "1":
                    tx.abort(ErrorCode.CONTRACT_VIOLATION, "unsupported stage plan")
                query_deadline = binding.get("query_deadline_at", later(task.deadline_at, 60))
                tx.write(
                    "temporal_bindings",
                    job.job_id,
                    {**binding, "query_deadline_at": query_deadline},
                )
                return TaskPlan(
                    first_stage=next(iter(policies)),
                    stages=policies,
                    deadline_at=task.deadline_at,
                    query_deadline_at=query_deadline,
                    retry_seconds=self.tasks.retry_seconds,
                )
        except Exception:
            raise ApplicationError(
                "P3 plan unavailable", type="P3_PLAN_INVALID", non_retryable=True
            ) from None

    def authorized_context(
        self, tx: SQLiteTransaction, task: TaskRecord, deadline: str
    ) -> TrustedContext:
        row, _ = self.tasks.load(tx, task.task_id)
        ctx = TrustedContext.model_validate(row["context"]).model_copy(
            update={"deadline_at": deadline}
        )
        self.tasks.identity.authorize(
            tx, ctx, self.tasks.permissions.get(task.kind, Permission.WRITE), task.subject
        )
        self.tasks.identity.authorize(tx, ctx, Permission.READ, task.input_ref)
        tx.before_commit.append(lambda: self.tasks.identity.revalidate(tx, ctx))
        return ctx

    @activity.defn(name="p3.step")
    async def execute_step(self, step: StepRequest) -> StepResult:
        # Never serialize provider exceptions into failure messages or chained causes.
        try:
            return await self._execute(step)
        except asyncio.CancelledError:
            raise
        except FoundationError as exc:
            outcome = StepResult(
                outcome="attention", effect_status=EffectStatus.UNKNOWN, reason_code=exc.code.value
            )
            with self.tasks.uow.transaction() as tx:
                _, current = self.tasks.load(tx, step.job.job_id)
                info = activity.info()
                owner = current.execution
                if (
                    owner is None
                    or owner.run_id != info.workflow_run_id
                    or owner.activity_id != info.activity_id
                    or owner.delivery_attempt != info.attempt
                ):
                    return outcome
                return self.close_in(tx, CloseRequest(job=step.job, result=outcome))
        except Exception:
            raise ApplicationError(
                "P3 stage interrupted", type="P3_STAGE_INTERRUPTED", non_retryable=True
            ) from None

    async def _execute(self, step: StepRequest) -> StepResult:
        stage = self.registry.get(step.job.kind, step.stage)
        with self.tasks.uow.transaction() as tx:
            binding = self.ledger.verify_job(tx, step.job)
            row, task = self.tasks.load(tx, step.job.job_id)
            deadline = task.deadline_at if step.mode == "execute" else binding["query_deadline_at"]
            self.authorized_context(tx, task, deadline)
            cached = self.ledger.load_step(tx, step)
            if cached is not None:
                return cached
            if task.state in TERMINAL:
                return self.terminal_result(task)
            execution_class = row["class"]
        remaining = (
            datetime.fromisoformat(deadline) - datetime.fromisoformat(self.tasks.clock())
        ).total_seconds()
        if remaining <= 0:
            return self.close(
                CloseRequest(
                    job=step.job,
                    result=StepResult(
                        outcome="attention",
                        effect_status=EffectStatus.UNKNOWN,
                        reason_code="DEADLINE_EXCEEDED",
                    ),
                )
            )
        async with (
            asyncio.timeout(min(remaining, stage.policy.timeout_seconds)),
            self.limits.slot(
                task.subject.scope.tenant_id,
                fingerprint(task.subject.scope.model_dump(mode="json")),
                execution_class,
            ),
        ):
            with self.tasks.uow.transaction() as tx:
                row, task = self.tasks.load(tx, step.job.job_id)
                counter = tx.read("temporal_stage_budgets", self.ledger.step_key(step)) or {
                    "executions": 0
                }
                exhausted = (
                    task.query_attempt >= self.tasks.query_max_attempts
                    if step.mode == "reconcile"
                    else task.attempt >= task.max_attempts and counter["executions"] > 0
                )
                if exhausted:
                    return self.close_in(
                        tx,
                        CloseRequest(
                            job=step.job,
                            result=StepResult(
                                outcome="attention"
                                if task.effect_status == EffectStatus.UNKNOWN
                                else "failed",
                                effect_status=task.effect_status,
                                reason_code="BUDGET_EXHAUSTED",
                            ),
                        ),
                    )
                deadline = min(deadline, later(self.tasks.clock(), stage.policy.timeout_seconds))
                tx.write("tasks", task.task_id, {**row, "execution_deadline": deadline})
                info = activity.info()
                execution = self.ledger.begin(
                    tx,
                    step,
                    ExecutionRef(
                        namespace=info.workflow_namespace,
                        workflow_id=info.workflow_id,
                        run_id=info.workflow_run_id,
                        activity_id=info.activity_id,
                        delivery_attempt=info.attempt,
                        epoch=0,
                    ),
                )
                row, task = self.tasks.load(tx, task.task_id)
                attempt = task.attempt
                if step.mode == "execute":
                    attempt = max(1, attempt + (1 if counter["executions"] else 0))
                    counter["executions"] += 1
                    tx.write("temporal_stage_budgets", self.ledger.step_key(step), counter)
                task = self.tasks.change(
                    tx,
                    row,
                    task,
                    attempt=attempt,
                    query_attempt=task.query_attempt + (1 if step.mode == "reconcile" else 0),
                    effect_status=EffectStatus.UNKNOWN
                    if stage.policy.effect_mode != "read"
                    else EffectStatus.NO_EFFECT,
                )
                ctx = self.authorized_context(tx, task, deadline)
                self.tasks.identity.authorize(tx, ctx, stage.permission, task.subject)
            context = StageContext(self.ledger, task, ctx, execution, self.executor)
            token = _context.set(context)
            fence_token = business_write_guard.set(context.commit_fence)
            beat = asyncio.create_task(
                self.heartbeat(step, min(1, stage.policy.timeout_seconds / 3))
            )
            try:
                telemetry = self.tasks.uow.telemetry
                trace = (
                    telemetry.span(
                        ctx,
                        "runtime.temporal.step",
                        {
                            "task_id": task.task_id,
                            "stage": step.stage,
                            "mode": step.mode,
                            "execution": execution,
                        },
                    )
                    if telemetry is not None
                    else nullcontext()
                )
                with trace as span:
                    try:
                        handler = stage.execute if step.mode == "execute" else stage.reconcile
                        result = await handler(step)
                    except FoundationError as exc:
                        if exc.code not in {
                            ErrorCode.DEPENDENCY_UNAVAILABLE,
                            ErrorCode.COMMIT_UNCONFIRMED,
                            ErrorCode.EXECUTION_INTERRUPTED,
                            ErrorCode.REQUEST_IN_PROGRESS,
                        }:
                            raise
                        result = StepResult(
                            outcome="retry" if stage.policy.effect_mode == "read" else "query",
                            effect_status=EffectStatus.NO_EFFECT
                            if stage.policy.effect_mode == "read"
                            else EffectStatus.UNKNOWN,
                            reason_code=exc.code.value,
                        )
                    except Exception:
                        result = StepResult(
                            outcome="retry" if stage.policy.effect_mode == "read" else "query",
                            effect_status=EffectStatus.NO_EFFECT
                            if stage.policy.effect_mode == "read"
                            else EffectStatus.UNKNOWN,
                            reason_code="PROVIDER_INTERRUPTED",
                        )
                    if span is not None:
                        span.output = result
                    return self.record(step, context, result)
            finally:
                beat.cancel()
                await asyncio.gather(beat, return_exceptions=True)
                _context.reset(token)
                business_write_guard.reset(fence_token)

    @staticmethod
    async def heartbeat(step: StepRequest, interval: float) -> None:
        while True:
            activity.heartbeat(step.ordinal)
            await asyncio.sleep(interval)

    def record(self, step: StepRequest, context: StageContext, result: StepResult) -> StepResult:
        with self.tasks.uow.transaction() as tx:
            row, current = self.tasks.load(tx, context.task.task_id)
            if current.execution != context.execution:
                tx.abort(ErrorCode.VERSION_CONFLICT, "late stage result")
            if current.state == TaskState.SUCCEEDED:
                self.authorized_context(tx, current, context.context.deadline_at)
                return self.terminal_result(current)
            context.guard(tx)
            if result.outcome == "done":
                if result.next_stage is not None:
                    self.registry.get(step.job.kind, result.next_stage)
                self.ledger.save_step(tx, step, context.execution, result)
                if result.next_stage is None:
                    assert result.result_ref is not None
                    self.ledger.complete(tx, current.task_id, context.execution, result.result_ref)
                else:
                    self.tasks.change(tx, row, current, effect_status=result.effect_status)
            else:
                state = {
                    "query": TaskState.RECOVERY_WAIT,
                    "retry": TaskState.RETRY_WAIT,
                    "obsolete": TaskState.CANCELLED,
                    "failed": TaskState.FAILED,
                    "attention": TaskState.ATTENTION,
                }[result.outcome]
                original = row.get("original_operation_id")
                if (
                    original
                    and result.original_operation_id
                    and original != result.original_operation_id
                ):
                    tx.abort(ErrorCode.IDEMPOTENCY_CONFLICT, "original operation changed")
                self.tasks.change(
                    tx,
                    {
                        **row,
                        "original_operation_id": original or result.original_operation_id,
                        "terminal_reason": result.reason_code,
                    },
                    current,
                    state=state,
                    effect_status=result.effect_status,
                )
            return result

    @staticmethod
    def terminal_result(task: TaskRecord) -> StepResult:
        outcome = {
            TaskState.SUCCEEDED: "done",
            TaskState.FAILED: "failed",
            TaskState.CANCELLED: "obsolete",
            TaskState.ATTENTION: "attention",
        }[task.state]
        return StepResult.model_validate(
            dict(
                outcome=outcome,
                result_ref=task.result_ref,
                effect_status=task.effect_status,
                reason_code="BUSINESS_TERMINAL",
            )
        )

    def close_in(self, tx: SQLiteTransaction, request: CloseRequest) -> StepResult:
        self.ledger.verify_job(tx, request.job)
        row, task = self.tasks.load(tx, request.job.job_id)
        if task.state in TERMINAL:
            self.tasks.project_terminal(tx, task)
            return self.terminal_result(task)
        # This infrastructure-only projection cannot claim a business effect or run a provider.
        effect = task.effect_status
        outcome = request.result.outcome
        if effect == EffectStatus.UNKNOWN or outcome == "done":
            outcome = "attention"
        state = (
            TaskState.ATTENTION
            if outcome == "attention"
            else (TaskState.CANCELLED if outcome == "obsolete" else TaskState.FAILED)
        )
        if task.state == TaskState.PENDING and state == TaskState.ATTENTION:
            state = TaskState.FAILED
            outcome = "failed"
        try:
            error_code: ErrorCode | None = ErrorCode(request.result.reason_code)
        except ValueError:
            error_code = task.error_code
        task = self.tasks.change(
            tx,
            {**row, "terminal_reason": request.result.reason_code},
            task,
            state=state,
            effect_status=effect,
            error_code=error_code,
        )
        return StepResult.model_validate(
            {**request.result.model_dump(), "outcome": outcome, "effect_status": effect}
        )

    def close(self, request: CloseRequest) -> StepResult:
        with self.tasks.uow.transaction() as tx:
            return self.close_in(tx, request)

    @activity.defn(name="p3.close")
    async def finish_workflow(self, request: CloseRequest) -> StepResult:
        try:
            return self.close(request)
        except Exception:
            raise ApplicationError(
                "P3 closure requires attention", type="P3_CLOSE_FAILED", non_retryable=True
            ) from None
