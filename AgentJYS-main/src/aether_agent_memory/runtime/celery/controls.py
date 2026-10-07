"""Backend-aware, authorized durable control of the original business task."""

from typing import Any

from aether_agent_memory.runtime.contracts.models import EffectStatus, ErrorCode, OperationRecord
from aether_agent_memory.runtime.contracts.models import Flow, Permission, RecordRef
from aether_agent_memory.runtime.foundation.common import fingerprint
from aether_agent_memory.runtime.foundation.tasks import TERMINAL
from aether_agent_memory.runtime.temporal.controls import ControlAdmission, authorize_task
from aether_agent_memory.runtime.temporal.models import CloseRequest, StepResult, WorkflowInput


class RoutedControls(ControlAdmission):
    def __init__(self, ledger: Any, operators: tuple[str, ...], engine: Any) -> None:
        super().__init__(ledger, operators)
        self.engine = engine

    def task(self, tx: Any, ctx: Any, job_id: str, request: Any) -> OperationRecord:
        binding = tx.read("temporal_bindings", job_id)
        if not binding or binding.get("backend") != "celery":
            return super().task(tx, ctx, job_id, request)
        task_row, task = self.tasks.load(tx, job_id)
        authorize_task(self.ledger, tx, ctx, task)
        signature = fingerprint(
            [ctx.principal.principal_id, "task", job_id, request.model_dump(mode="json")]
        )
        existing = self.existing(tx, request.operation_id, signature)
        if existing is not None:
            return existing
        if task.revision != request.expected_revision:
            tx.abort(ErrorCode.VERSION_CONFLICT, "task changed")
        if task.state in TERMINAL:
            tx.abort(ErrorCode.INVALID_ARGUMENT, "terminal task cannot accept control")
        job = WorkflowInput.model_validate(binding["job"])
        self.ledger.verify_job(tx, job)
        row = tx.read("celery_jobs", job_id)
        operation = OperationRecord(
            operation_id=request.operation_id,
            subject=task.subject,
            phase=f"task.{request.action}",
            state="running",
            task_ids=(job_id,),
            reason=request.reason,
        )
        tx.write(
            "operations",
            request.operation_id,
            {"signature": signature, "record": operation.model_dump(mode="json")},
        )
        tx.write(
            "celery_controls",
            job_id,
            {
                "operation_id": request.operation_id,
                "context": ctx.model_dump(mode="json"),
                "request": request.model_dump(mode="json"),
            },
        )
        self.tasks.change(tx, task_row, task)
        if request.action == "cancel" and task.effect_status != EffectStatus.UNKNOWN:
            self.engine.activities.close_in(
                tx,
                CloseRequest(
                    job=job,
                    result=StepResult(
                        outcome="obsolete",
                        effect_status=task.effect_status,
                        reason_code="CANCELLED",
                    ),
                ),
            )
            tx.write("celery_jobs", job_id, {**row, "state": "terminal", "execution": None})
            intent = tx.read("celery_dispatch_intents", job_id)
            tx.write("celery_dispatch_intents", job_id, {**intent, "state": "terminal"})
            operation = operation.model_copy(update={"state": "completed"})
            tx.write(
                "operations",
                request.operation_id,
                {"signature": signature, "record": operation.model_dump(mode="json")},
            )
            for slot in row.get("slots", []):
                owner = tx.read("celery_capacity", slot)
                if owner and owner["job_id"] == job_id:
                    tx.write("celery_capacity", slot, {**owner, "until": self.tasks.clock()})
        else:
            # Invalidate the old writer before reconciliation. An in-flight request
            # may still exist; its original operation id is the only recovery target.
            self.engine.persist(
                tx,
                job,
                {
                    **row,
                    "generation": row["generation"] + 1,
                    "state": "pending",
                    "mode": "reconcile",
                    "execution": None,
                    "due_at": max(self.tasks.clock(), row.get("lease_until", "")),
                    "lease_until": "",
                },
            )
        tx.before_commit.append(lambda: self.tasks.identity.revalidate(tx, ctx))
        return operation

    def remember_periodic_snapshot(self, tx: Any, ctx: Any) -> dict[str, Any]:
        if not self.tasks.identity.is_maintenance_operator(
            tx, ctx, Permission.CONFIGURE, self.ledger.periodic_operators
        ):
            tx.abort(ErrorCode.FORBIDDEN, "configured deployment operator required")
        deployment = self.ledger.config.deployment_id
        return tx.read("celery_periodic_control", deployment) or {
            "backend": "celery",
            "scope": "remember",
            "revision": 1,
            "enabled": True,
        }

    def remember_periodic(self, tx: Any, ctx: Any, request: Any) -> OperationRecord:
        row = self.remember_periodic_snapshot(tx, ctx)
        signature = fingerprint(
            [ctx.principal.principal_id, "remember_periodic", request.model_dump(mode="json")]
        )
        existing = self.existing(tx, request.operation_id, signature)
        if existing is not None:
            return existing
        if request.expected_revision != row["revision"]:
            tx.abort(ErrorCode.VERSION_CONFLICT, "Remember periodic revision changed")
        subject = RecordRef(
            owner=Flow.RUNTIME,
            object_type="periodic_control",
            object_id=self.ledger.config.deployment_id,
            scope=ctx.principal.home_scope,
        )
        self.tasks.identity.authorize(tx, ctx, Permission.RECOVER, subject)
        operation = OperationRecord(
            operation_id=request.operation_id,
            subject=subject,
            phase="periodic." + request.action,
            state="completed",
            task_ids=(),
            reason=request.reason,
        )
        tx.write(
            "operations",
            request.operation_id,
            {"signature": signature, "record": operation.model_dump(mode="json")},
        )
        tx.write(
            "celery_periodic_control",
            self.ledger.config.deployment_id,
            {**row, "revision": row["revision"] + 1, "enabled": request.action == "reconcile"},
        )
        tx.before_commit.append(lambda: self.tasks.identity.revalidate(tx, ctx))
        return operation
