"""Fence journaled caller operations inside the accepting business transaction."""

from uuid import UUID

from pydantic import JsonValue

from aether_agent_memory.runtime.contracts.client_admission import ClientOperationTargets
from aether_agent_memory.runtime.contracts.client_runs import ClientOperation, ClientRunRecord
from aether_agent_memory.runtime.contracts.http_evidence import (
    HttpRequestEvidence,
    effective_http_target,
)
from aether_agent_memory.runtime.contracts.models import (
    ContractModel,
    Digest,
    ErrorCode,
    RecordRef,
    TrustedContext,
)
from aether_agent_memory.runtime.contracts.ports import Transaction

from .client_definitions import ClientDefinitions
from .client_inputs import ClientInputs, StoredClientInput
from .client_runs import ClientRuns
from .client_scopes import require_targets
from .client_states import ClientStates
from .common import FoundationError, fingerprint


class RegisteredClientOperation(ContractModel):
    run_id: UUID
    binding_digest: Digest


class ClientAdmissions:
    def __init__(self, runs: ClientRuns):
        self.runs = runs

    @staticmethod
    def ref(ctx: TrustedContext, operation_id: str) -> RecordRef:
        return RecordRef(
            owner="runtime",
            object_type="client_operation",
            object_id=fingerprint([ctx.principal.principal_id, operation_id]),
            scope=ctx.principal.home_scope,
        )

    @classmethod
    def bind_new(
        cls,
        tx: Transaction,
        ctx: TrustedContext,
        prior: ClientRunRecord,
        snapshot: dict[str, JsonValue],
    ) -> None:
        before, after = prior.snapshot.get("operations", []), snapshot.get("operations", [])
        assert isinstance(before, list) and isinstance(after, list)
        old_ids = {ClientOperation.model_validate(value).operation_id for value in before}
        for value in after:
            operation = ClientOperation.model_validate(value)
            if operation.operation_id in old_ids:
                continue  # Existing records do not acquire retrospective admission evidence.
            assert operation.binding is not None  # ClientRuns._progress validates new intents.
            ref = cls.ref(ctx, operation.operation_id)
            if tx.get(ref) is not None:
                raise FoundationError(
                    ErrorCode.IDEMPOTENCY_CONFLICT, "caller operation already belongs to a run"
                )
            tx.put_if_revision(
                ref,
                RegisteredClientOperation(
                    run_id=prior.run_id, binding_digest=operation.binding.digest
                ).model_dump(mode="json"),
                None,
            )

    def require(
        self,
        tx: Transaction,
        ctx: TrustedContext,
        evidence: HttpRequestEvidence,
        kind: str,
        *,
        targets: ClientOperationTargets | None = None,
    ) -> None:
        raw = tx.get(self.ref(ctx, ctx.operation_id))
        claim = evidence.client_run
        if raw is None and claim is None:
            require_targets(tx, targets, None, None)
            return  # Unregistered calls retain the ordinary authenticated API contract.
        if raw is None or claim is None:
            raise FoundationError(ErrorCode.VERSION_CONFLICT, "caller admission binding missing")
        registered = RegisteredClientOperation.model_validate(raw)
        if registered.run_id != claim.run_id:
            raise FoundationError(ErrorCode.VERSION_CONFLICT, "caller admission run changed")
        run = self.runs._read(tx, ctx, registered.run_id)
        index = tx.get(self.runs.ref(ctx, None))
        if (
            run.owner_id != claim.owner_id
            or run.recovery_held
            or run.revision != claim.revision
            or run.snapshot["state"] != "running"
            or index is None
            or index["active"] != str(run.run_id)
        ):
            raise FoundationError(ErrorCode.VERSION_CONFLICT, "caller executor changed or closed")
        operation = ClientInputs.operation(run, ctx.operation_id)
        assert operation.binding is not None
        if operation.phase != "prepared":
            raise FoundationError(ErrorCode.VERSION_CONFLICT, "caller operation already observed")
        if (
            registered.binding_digest != operation.binding.digest
            or not evidence.matches_kind(kind)
            or evidence.method != operation.method
            or evidence.route != operation.path
            or evidence.target != effective_http_target(operation.path, operation.binding.target)
            or evidence.content_type != operation.binding.content_type
            or evidence.body_hash != operation.request_hash
        ):
            raise FoundationError(ErrorCode.IDEMPOTENCY_CONFLICT, "original caller request changed")
        if run.definition is not None:
            ClientDefinitions.require_ready(tx, ctx, run)
        ClientStates.require_ready(tx, ctx, run)
        value = tx.get(ClientInputs.reference(ctx, run.run_id, ctx.operation_id))
        stored = StoredClientInput.model_validate(value) if value is not None else None
        if stored is None or stored.state != "ready":
            raise FoundationError(ErrorCode.COMMIT_UNCONFIRMED, "original caller input not ready")
        if (
            stored.run_id != run.run_id
            or stored.operation_id != ctx.operation_id
            or stored.request_hash != operation.request_hash
            or stored.binding_digest != operation.binding.digest
        ):
            raise FoundationError(ErrorCode.CONTRACT_VIOLATION, "original caller input changed")
        require_targets(tx, targets, run, self.runs.ref(ctx, run.run_id))
