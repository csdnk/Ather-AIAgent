"""Immutable mutation receipts committed inside the caller's business transaction."""

from collections.abc import Callable

from pydantic import JsonValue

from aether_agent_memory.runtime.contracts.client_admission import ClientOperationTargets
from aether_agent_memory.runtime.contracts.http_evidence import HttpRequestEvidence
from aether_agent_memory.runtime.contracts.models import (
    ContractModel,
    ErrorCode,
    Identifier,
    Permission,
    Positive,
    RecordRef,
    Scope,
    TrustedContext,
)
from aether_agent_memory.runtime.contracts.mutation_receipts import (
    MutationKind,
    MutationLookup,
    MutationReceipt,
    MutationResult,
)
from aether_agent_memory.runtime.contracts.ports import IdentityPort, Transaction, UnitOfWork

from .client_admission import ClientAdmissions
from .client_runs import ClientRuns
from .common import FoundationError, fingerprint, now


class MutationRecord(ContractModel):
    principal_id: Identifier
    home_scope: Scope
    auth_epoch: Positive
    receipt: MutationReceipt
    response: dict[str, JsonValue]


class MutationWrite:
    def __init__(
        self,
        tx: Transaction,
        ctx: TrustedContext,
        ref: RecordRef,
        kind: MutationKind,
        targets: tuple[RecordRef, ...],
        intent_hash: str,
        previous: MutationRecord | None,
        clock: Callable[[], str],
        http_request: HttpRequestEvidence | None,
    ) -> None:
        self.tx, self.ctx, self.ref, self.kind = tx, ctx, ref, kind
        self.targets, self.intent_hash, self.clock = targets, intent_hash, clock
        self.previous = None if previous is None else previous.response
        self.http_request = http_request

    def finish(self, response: dict[str, JsonValue], *, task_ids: tuple[str, ...] = ()) -> None:
        # Lifecycle returns a hydrated DTO; replay reads the immutable body through P2.
        lifecycle = self.kind == "remember.lifecycle"
        stored = {
            key: value for key, value in response.items() if not lifecycle or key != "content"
        }
        record = MutationRecord(
            principal_id=self.ctx.principal.principal_id,
            home_scope=self.ctx.principal.home_scope,
            auth_epoch=self.ctx.principal.auth_epoch,
            receipt=MutationReceipt(
                operation_id=self.ctx.operation_id,
                kind=self.kind,
                targets=self.targets,
                intent_hash=self.intent_hash,
                result_hash=fingerprint(stored),
                result_basis="metadata_without_content" if lifecycle else "response",
                task_ids=task_ids,
                committed_at=self.clock(),
                http_request=self.http_request,
            ),
            response=stored,
        )
        self.tx.put_if_revision(self.ref, record.model_dump(mode="json"), None)


class MutationReceipts:
    def __init__(self, uow: UnitOfWork, identity: IdentityPort, *, clock: Callable[[], str] = now):
        self.uow, self.identity, self.clock = uow, identity, clock
        self.clients = ClientAdmissions(ClientRuns(uow, identity, clock=clock))

    @staticmethod
    def ref(ctx: TrustedContext, operation_id: str, kind: MutationKind) -> RecordRef:
        return RecordRef(
            owner="runtime",
            object_type="mutation_receipt",
            object_id=fingerprint([ctx.principal.principal_id, kind, operation_id]),
            scope=ctx.principal.home_scope,
        )

    def _read(
        self,
        tx: Transaction,
        ctx: TrustedContext,
        operation_id: str,
        kind: MutationKind,
    ) -> MutationRecord | None:
        self.identity.revalidate(tx, ctx)
        raw = tx.get(self.ref(ctx, operation_id, kind))
        if raw is None:
            return None
        record = MutationRecord.model_validate(raw)
        if (
            record.principal_id != ctx.principal.principal_id
            or record.home_scope != ctx.principal.home_scope
            or record.auth_epoch != ctx.principal.auth_epoch
        ):
            raise FoundationError(ErrorCode.FORBIDDEN, "mutation receipt identity changed")
        if (
            record.receipt.operation_id != operation_id
            or record.receipt.kind != kind
            or fingerprint(record.response) != record.receipt.result_hash
            or (kind == "remember.lifecycle" and "content" in record.response)
        ):
            raise FoundationError(
                ErrorCode.IDEMPOTENCY_CONFLICT, "mutation receipt binding changed"
            )
        return record

    def begin(
        self,
        tx: Transaction,
        ctx: TrustedContext,
        kind: MutationKind,
        targets: tuple[RecordRef, ...],
        request: JsonValue,
        *,
        http_request: HttpRequestEvidence | None = None,
    ) -> MutationWrite:
        if not targets:
            raise FoundationError(ErrorCode.INVALID_ARGUMENT, "mutation requires targets")
        if http_request is not None and not http_request.matches_kind(kind):
            raise FoundationError(
                ErrorCode.CONTRACT_VIOLATION, "HTTP evidence mutation kind differs"
            )
        permission = (
            Permission.DELETE
            if kind in {"remember.delete", "source.delete", "source.revoke"}
            else Permission.WRITE
        )
        for target in targets:
            self.identity.authorize(tx, ctx, permission, target)
        if http_request is not None:
            self.clients.require(
                tx,
                ctx,
                http_request,
                kind,
                targets=ClientOperationTargets(scopes=tuple(target.scope for target in targets)),
            )
        previous = self._read(tx, ctx, ctx.operation_id, kind)
        intent_hash = fingerprint([kind, [ref.model_dump(mode="json") for ref in targets], request])
        if previous and previous.receipt.intent_hash != intent_hash:
            raise FoundationError(
                ErrorCode.IDEMPOTENCY_CONFLICT, "mutation target or intent changed"
            )
        return MutationWrite(
            tx,
            ctx,
            self.ref(ctx, ctx.operation_id, kind),
            kind,
            targets,
            intent_hash,
            previous,
            self.clock,
            http_request,
        )

    def lookup(self, ctx: TrustedContext, operation_id: str, kind: MutationKind) -> MutationLookup:
        with self.uow.transaction() as tx:
            record = self._read(tx, ctx, operation_id, kind)
            if record is not None:
                for target in record.receipt.targets:
                    self.identity.authorize(tx, ctx, Permission.READ, target)
            return MutationLookup(
                operation_id=operation_id,
                kind=kind,
                state="committed" if record else "unconfirmed",
                receipt=record.receipt if record else None,
            )

    def result(self, ctx: TrustedContext, operation_id: str, kind: MutationKind) -> MutationResult:
        with self.uow.transaction() as tx:
            record = self._read(tx, ctx, operation_id, kind)
            if record is None:
                raise FoundationError(
                    ErrorCode.COMMIT_UNCONFIRMED, "original mutation result is not confirmed"
                )
            for target in record.receipt.targets:
                self.identity.authorize(tx, ctx, Permission.READ, target)
            return MutationResult(receipt=record.receipt, response=record.response)
