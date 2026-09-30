"""Atomic admission, execution fences and completion evidence; no scheduler."""

from functools import partial
from typing import Any

from aether_agent_memory.runtime.contracts.models import (
    ErrorCode,
    RecordRef,
    TaskRecord,
    TaskSpec,
    TaskState,
    TrustedContext,
)
from aether_agent_memory.runtime.foundation.common import fingerprint
from aether_agent_memory.runtime.foundation.storage import SQLiteTransaction
from aether_agent_memory.runtime.foundation.tasks import TERMINAL, Tasks

from .config import TemporalConfiguration
from .models import ExecutionRef, StartIntent, StepRequest, StepResult, WorkflowInput


class ExecutionLedger:
    def __init__(self, tasks: Tasks, config: TemporalConfiguration) -> None:
        from .projections import project_terminal

        self.tasks, self.config = tasks, config
        self.periodic_operators: tuple[str, ...] = ()
        tasks.on_terminal = partial(project_terminal, tasks)

    def admit(self, tx: SQLiteTransaction, ctx: TrustedContext, spec: TaskSpec) -> WorkflowInput:
        task = self.tasks.enqueue(tx, ctx, spec)
        return self.bind_admitted(tx, task)

    def bind_admitted(self, tx: SQLiteTransaction, task: TaskRecord) -> WorkflowInput:
        prior = tx.read("temporal_bindings", task.task_id)
        if prior:
            job = WorkflowInput.model_validate(prior["job"])
            if job.deployment_id != self.config.deployment_id or job.input_hash != task.input_hash:
                tx.abort(ErrorCode.IDEMPOTENCY_CONFLICT, "task execution binding changed")
            return job
        if task.state in TERMINAL:
            tx.abort(ErrorCode.CONTRACT_VIOLATION, "historical terminal work needs no new workflow")
        job = WorkflowInput(
            job_id=task.task_id,
            kind=task.kind,
            deployment_id=self.config.deployment_id,
            input_hash=task.input_hash,
            entry="reconcile"
            if task.state in {TaskState.RUNNING, TaskState.RECOVERY_WAIT}
            else "execute",
        )
        workflow_id = f"p3/{job.deployment_id}/{job.kind}/{job.job_id}"
        row, _ = self.tasks.load(tx, task.task_id)
        intent = StartIntent(
            intent_id=fingerprint(workflow_id),
            job=job,
            task_queue=f"{self.config.task_queue_prefix}.{row['class']}",
        )
        tx.write(
            "temporal_bindings",
            task.task_id,
            {
                "job": job.model_dump(mode="json"),
                "namespace": self.config.namespace,
                "workflow_id": workflow_id,
                "binding": None,
                "epoch": 0,
            },
        )
        tx.write(
            "temporal_start_intents",
            intent.intent_id,
            {"state": "pending", "intent": intent.model_dump(mode="json")},
        )
        return job

    def verify_job(self, tx: SQLiteTransaction, job: WorkflowInput) -> dict[str, Any]:
        if job.plan_version != "1":
            tx.abort(ErrorCode.CONTRACT_VIOLATION, "unsupported Temporal plan version")
        binding: dict[str, Any] | None = tx.read("temporal_bindings", job.job_id)
        if binding is None or binding["job"] != job.model_dump(mode="json"):
            tx.abort(ErrorCode.VERSION_CONFLICT, "workflow input does not match durable binding")
        return binding

    def begin(
        self, tx: SQLiteTransaction, step: StepRequest, execution: ExecutionRef
    ) -> ExecutionRef:
        binding = self.verify_job(tx, step.job)
        if (
            execution.namespace != binding["namespace"]
            or execution.workflow_id != binding["workflow_id"]
        ):
            tx.abort(ErrorCode.VERSION_CONFLICT, "Activity belongs to another workflow")
        acknowledged = binding["binding"]
        bound_run = (
            acknowledged["current_run_id"] if acknowledged else binding.get("observed_run_id")
        )
        if bound_run and bound_run != execution.run_id:
            tx.abort(ErrorCode.VERSION_CONFLICT, "Activity belongs to another run")
        last_ordinal = binding.get("ordinal", 0)
        if step.ordinal < last_ordinal:
            tx.abort(ErrorCode.VERSION_CONFLICT, "stage already advanced")
        if step.ordinal > last_ordinal:
            prior = tx.read("temporal_steps", f"{step.job.job_id}:{last_ordinal}")
            if (
                step.ordinal != last_ordinal + 1
                or prior is None
                or prior["result"]["next_stage"] != step.stage
            ):
                tx.abort(ErrorCode.VERSION_CONFLICT, "stage has no committed predecessor")
        if step.ordinal == last_ordinal and binding.get("stage", step.stage) != step.stage:
            tx.abort(ErrorCode.VERSION_CONFLICT, "stage route changed")
        delivery_key = fingerprint([execution.workflow_id, execution.run_id, execution.activity_id])
        old_delivery = tx.read("temporal_deliveries", delivery_key)
        if old_delivery and old_delivery["epoch"] != binding["epoch"]:
            tx.abort(ErrorCode.VERSION_CONFLICT, "delivery was superseded")
        row, task = self.tasks.load(tx, step.job.job_id)
        if task.state in TERMINAL:
            tx.abort(ErrorCode.VERSION_CONFLICT, "terminal task cannot start another delivery")
        previous = task.execution
        if previous and previous.activity_id == execution.activity_id:
            if previous.delivery_attempt > execution.delivery_attempt:
                tx.abort(ErrorCode.VERSION_CONFLICT, "late Activity delivery")
            if previous.delivery_attempt == execution.delivery_attempt:
                self.guard(tx, task.task_id, previous)
                return previous
        epoch = int(binding["epoch"]) + 1
        fenced = execution.model_copy(update={"epoch": epoch})
        tx.write(
            "temporal_bindings",
            task.task_id,
            {
                **binding,
                "epoch": epoch,
                "observed_run_id": execution.run_id,
                "ordinal": step.ordinal,
                "stage": step.stage,
            },
        )
        tx.write("temporal_deliveries", delivery_key, {"epoch": epoch})
        updated = self.tasks.change(
            tx,
            {**row, "execution_deadline": row.get("execution_deadline", task.deadline_at)},
            task,
            state=TaskState.RECOVERY_WAIT
            if task.state == TaskState.RECOVERY_WAIT
            else TaskState.RUNNING,
            lease=None,
            execution=fenced,
        )
        self.tasks.guard(tx, updated)
        return fenced

    def guard(self, tx: SQLiteTransaction, job_id: str, execution: ExecutionRef) -> TaskRecord:
        _, current = self.tasks.load(tx, job_id)
        return self.tasks.guard(tx, current.model_copy(update={"execution": execution}))[1]

    @staticmethod
    def step_key(step: StepRequest) -> str:
        return f"{step.job.job_id}:{step.ordinal}"

    def load_step(self, tx: SQLiteTransaction, step: StepRequest) -> StepResult | None:
        self.verify_job(tx, step.job)
        row = tx.read("temporal_steps", self.step_key(step))
        if row is None:
            return None
        if row["stage"] != step.stage or row["input_hash"] != step.job.input_hash:
            tx.abort(ErrorCode.VERSION_CONFLICT, "step plan or input changed")
        _, task = self.tasks.load(tx, step.job.job_id)
        if tx.get(task.input_ref) is None or fingerprint(tx.get(task.input_ref)) != task.input_hash:
            tx.abort(ErrorCode.VERSION_CONFLICT, "committed input changed")
        result = StepResult.model_validate(row["result"])
        if result.result_ref is not None and (
            result.result_ref.scope != task.subject.scope
            or tx.get(result.result_ref) is None
            or row.get("result_hash") != fingerprint(tx.get(result.result_ref))
        ):
            tx.abort(ErrorCode.VERSION_CONFLICT, "committed result changed")
        return result

    def save_step(
        self, tx: SQLiteTransaction, step: StepRequest, execution: ExecutionRef, result: StepResult
    ) -> None:
        task = self.guard(tx, step.job.job_id, execution)
        self.verify_job(tx, step.job)
        if result.result_ref is not None and (
            result.result_ref.scope != task.subject.scope or tx.get(result.result_ref) is None
        ):
            tx.abort(ErrorCode.CONTRACT_VIOLATION, "step output must exist in task scope")
        prior = self.load_step(tx, step)
        if prior is not None and prior != result:
            tx.abort(ErrorCode.IDEMPOTENCY_CONFLICT, "completed step changed")
        tx.write(
            "temporal_steps",
            self.step_key(step),
            {
                "stage": step.stage,
                "input_hash": step.job.input_hash,
                "execution": execution.model_dump(mode="json"),
                "result_hash": fingerprint(tx.get(result.result_ref))
                if result.result_ref
                else None,
                "result": result.model_dump(mode="json"),
            },
        )

    def complete(
        self, tx: SQLiteTransaction, job_id: str, execution: ExecutionRef, result: RecordRef
    ) -> TaskRecord:
        task = self.guard(tx, job_id, execution)
        row, _ = self.tasks.load(tx, job_id)
        ctx = TrustedContext.model_validate(row["context"])
        ctx = ctx.model_copy(
            update={"deadline_at": row.get("execution_deadline", task.deadline_at)}
        )
        self.tasks.complete(tx, ctx, task, result)
        return self.tasks.load(tx, job_id)[1]
