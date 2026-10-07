"""Resolve existing HTTP ingress identities without admission or task scanning."""

from aether_agent_memory.runtime.contracts.http_evidence import HttpRequestEvidence
from aether_agent_memory.runtime.contracts.models import ErrorCode, Permission, TrustedContext
from aether_agent_memory.runtime.contracts.operation_lookup import HTTPCommandKind, OperationLookup
from aether_agent_memory.runtime.foundation.common import FoundationError, fingerprint
from aether_agent_memory.runtime.foundation.requests import request_key

from .ingress import InputStore
from .ledger import ExecutionLedger
from .models import WorkflowInput


def lookup_operation(
    ledger: ExecutionLedger,
    inputs: InputStore,
    ctx: TrustedContext,
    operation_id: str,
    kind: HTTPCommandKind,
) -> OperationLookup:
    original_ctx = ctx.model_copy(update={"operation_id": operation_id})
    ref = inputs.reference(ctx, operation_id)
    job_id = (
        request_key(original_ctx, "recall")
        if kind == "recall.execute"
        else fingerprint([ref.model_dump(mode="json"), kind])
    )
    identity = ledger.tasks.identity
    with inputs.uow.transaction() as tx:
        identity.revalidate(tx, ctx)
        if tx.read("tasks", job_id) is None:
            # Input upload, delayed admission or pre-upgrade work may still exist.
            return OperationLookup(operation_id=operation_id, kind=kind, state="unconfirmed")
        row, task = ledger.tasks.load(tx, job_id)
        admitted_ctx = TrustedContext.model_validate(row["context"])
        if (
            task.initiator_id != ctx.principal.principal_id
            or admitted_ctx.principal.principal_id != ctx.principal.principal_id
            or admitted_ctx.principal.home_scope != ctx.principal.home_scope
            or task.initiator_auth_epoch != ctx.principal.auth_epoch
            or admitted_ctx.principal.auth_epoch != ctx.principal.auth_epoch
        ):
            raise FoundationError(ErrorCode.FORBIDDEN, "original operation identity changed")
        identity.authorize(tx, ctx, Permission.READ, task.subject)
        identity.authorize(tx, ctx, Permission.READ, task.input_ref)
        value = tx.get(task.input_ref)
        if (
            task.kind != kind
            or admitted_ctx.operation_id != operation_id
            or value is None
            or fingerprint(value) != task.input_hash
            or (kind != "recall.execute" and task.input_ref != ref)
            or (kind == "recall.execute" and value.get("recall_id") != job_id)
            or (kind != "recall.execute" and value.get("operation_id") != operation_id)
        ):
            raise FoundationError(ErrorCode.VERSION_CONFLICT, "original operation input changed")
        bound = tx.read("temporal_bindings", job_id)
        if bound is None:
            raise FoundationError(ErrorCode.VERSION_CONFLICT, "original Temporal binding missing")
        job = WorkflowInput.model_validate(bound["job"])
        prefix = "celery" if bound.get("backend") == "celery" else "p3"
        expected_workflow = f"{prefix}/{ledger.config.deployment_id}/{kind}/{job_id}"
        if (
            job.job_id != job_id
            or job.kind != kind
            or job.input_hash != task.input_hash
            or job.deployment_id != ledger.config.deployment_id
            or job.plan_version != "1"
            or bound["namespace"] != ledger.config.namespace
            or bound["workflow_id"] != expected_workflow
        ):
            raise FoundationError(ErrorCode.VERSION_CONFLICT, "original Temporal binding changed")
        return OperationLookup(
            operation_id=operation_id,
            kind=kind,
            state="found",
            job_id=job_id,
            task_state=task.state,
            input_hash=task.input_hash,
            workflow_id=expected_workflow,
            http_request=HttpRequestEvidence.model_validate(row["http_request"])
            if row.get("http_request") is not None
            else None,
        )
