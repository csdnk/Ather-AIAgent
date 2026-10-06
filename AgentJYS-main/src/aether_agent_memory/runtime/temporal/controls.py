"""Authorized business control intents. RPC delivery always targets the original chain."""

from typing import Any, Literal

from aether_agent_memory.runtime.contracts.models import (
    ContractModel,
    ErrorCode,
    Flow,
    Identifier,
    MaintenanceRecord,
    NonEmpty,
    OperationRecord,
    Permission,
    Positive,
    RecordRef,
    RecoveryRequest,
    TaskRecord,
    TrustedContext,
)
from aether_agent_memory.runtime.foundation.common import fingerprint, later, now
from aether_agent_memory.runtime.foundation.tasks import TERMINAL
from aether_agent_memory.runtime.storage.ports import MetadataTransaction

from .ledger import ExecutionLedger
from .models import ControlIntent, WorkflowBinding, WorkflowInput


class ControlRequest(ContractModel):
    operation_id: Identifier
    action: Literal["cancel", "reconcile"]
    expected_revision: Positive
    reason: NonEmpty


class ControlAdmission:
    def __init__(self, ledger: ExecutionLedger, operators: tuple[str, ...]) -> None:
        self.ledger, self.tasks = ledger, ledger.tasks
        ledger.periodic_operators = operators

    def recovery(
        self, tx: MetadataTransaction, ctx: TrustedContext, request: RecoveryRequest
    ) -> OperationRecord:
        return self.task(
            tx,
            ctx,
            request.task_id,
            ControlRequest(**request.model_dump(exclude={"task_id"}), action="reconcile"),
        )

    def status(self, ctx: TrustedContext, operation_id: str) -> OperationRecord:
        with self.tasks.uow.transaction() as tx:
            row = tx.read("operations", operation_id)
            if row is None:
                tx.abort(ErrorCode.NOT_FOUND, "control operation not found")
            operation = OperationRecord.model_validate(row["record"])
            if operation.phase in {"task.cancel", "task.reconcile"} and operation.task_ids:
                for task_id in operation.task_ids:
                    _, task = self.tasks.load(tx, task_id)
                    authorize_task(self.ledger, tx, ctx, task)
            else:
                self.tasks.identity.authorize(tx, ctx, Permission.RECOVER, operation.subject)
            return operation

    def task(
        self, tx: MetadataTransaction, ctx: TrustedContext, job_id: str, request: ControlRequest
    ) -> OperationRecord:
        row, task = self.tasks.load(tx, job_id)
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
            tx.abort(ErrorCode.INVALID_ARGUMENT, "terminal business task cannot accept control")
        stored = tx.read("temporal_bindings", job_id)
        if not stored or not stored["binding"]:
            tx.abort(ErrorCode.REQUEST_IN_PROGRESS, "original workflow binding is not acknowledged")
        job = WorkflowInput.model_validate(stored["job"])
        self.ledger.verify_job(tx, job)
        binding = WorkflowBinding.model_validate(stored["binding"])
        if (
            job.input_hash != task.input_hash
            or job.kind != task.kind
            or binding.input_hash != task.input_hash
            or binding.plan_version != job.plan_version
            or binding.namespace != stored["namespace"]
            or binding.workflow_id != stored["workflow_id"]
        ):
            tx.abort(ErrorCode.VERSION_CONFLICT, "original task binding differs")
        # Revision serializes admission; the signal decides when to reconcile. Do not
        # invent an unknown effect or invalidate a still-running business Activity.
        self.tasks.change(tx, row, task)
        return self.persist(tx, ctx, request, signature, task.subject, binding, job_id, "task")

    def periodic_snapshot(self, tx: MetadataTransaction, ctx: TrustedContext) -> dict[str, Any]:
        if not self.tasks.identity.is_maintenance_operator(
            tx, ctx, Permission.CONFIGURE, self.ledger.periodic_operators
        ):
            tx.abort(ErrorCode.FORBIDDEN, "configured deployment operator required")
        row = tx.read("temporal_periodic_binding", self.ledger.config.deployment_id)
        if not row or not row["binding"]:
            tx.abort(ErrorCode.REQUEST_IN_PROGRESS, "periodic binding pending")
        return {**row, "revision": row.get("revision", 1)}

    def periodic(
        self, tx: MetadataTransaction, ctx: TrustedContext, request: ControlRequest
    ) -> OperationRecord:
        row = self.periodic_snapshot(tx, ctx)
        deployment = self.ledger.config.deployment_id
        signature = fingerprint(
            [ctx.principal.principal_id, "periodic", deployment, request.model_dump(mode="json")]
        )
        existing = self.existing(tx, request.operation_id, signature)
        if existing is not None:
            return existing
        if row["revision"] != request.expected_revision:
            tx.abort(ErrorCode.VERSION_CONFLICT, "periodic control revision changed")
        binding = WorkflowBinding.model_validate(row["binding"])
        if binding.input_hash != fingerprint(row["plan"]):
            tx.abort(ErrorCode.VERSION_CONFLICT, "periodic plan changed")
        subject = RecordRef(
            owner=Flow.RUNTIME,
            object_type="periodic_control",
            object_id=deployment,
            scope=ctx.principal.home_scope,
        )
        self.tasks.identity.authorize(tx, ctx, Permission.RECOVER, subject)
        tx.write("temporal_periodic_binding", deployment, {**row, "revision": row["revision"] + 1})
        return self.persist(tx, ctx, request, signature, subject, binding, deployment, "periodic")

    @staticmethod
    def existing(
        tx: MetadataTransaction, operation_id: str, signature: str
    ) -> OperationRecord | None:
        row = tx.read("operations", operation_id)
        if row is None:
            return None
        if row["signature"] != signature:
            tx.abort(ErrorCode.IDEMPOTENCY_CONFLICT, "control operation ID already used")
        return OperationRecord.model_validate(row["record"])

    def persist(
        self,
        tx: MetadataTransaction,
        ctx: TrustedContext,
        request: ControlRequest,
        signature: str,
        subject: RecordRef,
        binding: WorkflowBinding,
        job_id: str,
        target: str,
    ) -> OperationRecord:
        control = ControlIntent(
            control_id=fingerprint(["p3-control", request.operation_id]),
            job_id=job_id,
            action=request.action,
            expected_revision=request.expected_revision,
        )
        operation = OperationRecord(
            operation_id=request.operation_id,
            subject=subject,
            phase=f"{target}.{request.action}",
            state="accepted",
            task_ids=(job_id,) if target == "task" else (),
            reason=request.reason,
        )
        tx.write(
            "operations",
            operation.operation_id,
            {"record": operation.model_dump(mode="json"), "signature": signature},
        )
        tx.write(
            "temporal_control_intents",
            control.control_id,
            {
                "intent": control.model_dump(mode="json"),
                "state": "pending",
                "target": target,
                "binding": binding.model_dump(mode="json"),
                "operation_id": operation.operation_id,
                "context": ctx.model_dump(mode="json"),
                "subject": subject.model_dump(mode="json"),
            },
        )
        audit = MaintenanceRecord(
            record_id=control.control_id,
            actor_id=ctx.principal.principal_id,
            operation_id=operation.operation_id,
            subject=subject,
            reason=request.reason,
            occurred_at=self.tasks.clock(),
            phase="accepted",
            config_version="temporal_1",
        )
        tx.write("maintenance", audit.record_id, audit.model_dump(mode="json"))
        tx.before_commit.append(lambda: self.tasks.identity.revalidate(tx, ctx))
        return operation


def authorize_task(
    ledger: ExecutionLedger, tx: MetadataTransaction, ctx: TrustedContext, task: TaskRecord
) -> None:
    if not ledger.tasks.identity.permits_task_maintenance(
        tx, ctx, Permission.RECOVER, task, ledger.periodic_operators
    ):
        tx.abort(ErrorCode.FORBIDDEN, "task control access denied")


def authorize_delivery(
    ledger: ExecutionLedger, tx: MetadataTransaction, row: dict[str, Any]
) -> None:
    ctx = TrustedContext.model_validate(row["context"]).model_copy(
        update={"deadline_at": later(ledger.tasks.clock(), 30)}
    )
    subject = RecordRef.model_validate(row["subject"])
    if row["target"] == "task":
        _, task = ledger.tasks.load(tx, row["intent"]["job_id"])
        if task.subject != subject:
            tx.abort(ErrorCode.VERSION_CONFLICT, "control target changed")
        authorize_task(ledger, tx, ctx, task)
    else:
        ledger.tasks.identity.authorize(tx, ctx, Permission.RECOVER, subject)
    if row["target"] == "periodic" and not ledger.tasks.identity.is_maintenance_operator(
        tx, ctx, Permission.CONFIGURE, ledger.periodic_operators
    ):
        tx.abort(ErrorCode.FORBIDDEN, "deployment operator no longer authorized")


def settle_control(
    tx: MetadataTransaction, row: dict[str, Any], *, error: str | None = None
) -> None:
    """Transport completion is separate from the outcome of controlled task work."""
    if "operation_id" not in row:
        return
    stored = tx.read("operations", row["operation_id"])
    if stored is None or stored["record"]["state"] not in {"accepted", "running"}:
        return
    state = "failed" if error else "completed" if row["target"] == "periodic" else "running"
    tx.write(
        "operations",
        row["operation_id"],
        {
            **stored,
            "record": {
                **stored["record"],
                "state": state,
                "reason": f"CONTROL_NOT_DELIVERED:{error}" if error else stored["record"]["reason"],
            },
        },
    )
    if state in {"failed", "completed"}:
        audit = tx.read("maintenance", row["intent"]["control_id"])
        if audit is not None:
            key = fingerprint([row["intent"]["control_id"], "delivery", state])
            tx.write(
                "maintenance",
                key,
                {**audit, "record_id": key, "phase": state, "occurred_at": now()},
            )
