"""Atomically move caller ownership into a recovery hold using the existing UOW."""

from uuid import UUID

from aether_agent_memory.runtime.contracts.client_ownership import (
    MAX_OWNER_EPOCHS,
    ClientOwnerEpoch,
    ClientOwnership,
)
from aether_agent_memory.runtime.contracts.client_transfers import (
    ClientRunTransferLookup,
    ClientRunTransferResult,
    TransferClientRun,
    client_run_hash,
)
from aether_agent_memory.runtime.contracts.models import (
    ErrorCode,
    Permission,
    RecordRef,
    TrustedContext,
)

from .client_runs import ClientRuns
from .common import FoundationError, fingerprint


class ClientTransfers:
    def __init__(self, runs: ClientRuns):
        self.runs = runs

    @staticmethod
    def ref(ctx: TrustedContext, run_id: UUID, transfer_id: str) -> RecordRef:
        return ClientRuns.ref(ctx, run_id).model_copy(
            update={
                "object_type": "client_run_transfer",
                "object_id": fingerprint([str(run_id), transfer_id, ctx.principal.principal_id]),
            }
        )

    def lookup(
        self, ctx: TrustedContext, run_id: UUID, transfer_id: str
    ) -> ClientRunTransferLookup:
        with self.runs.uow.transaction() as tx:
            self.runs._read(tx, ctx, run_id)
            value = tx.get(self.ref(ctx, run_id, transfer_id))
            result = ClientRunTransferResult.model_validate(value) if value is not None else None
            return ClientRunTransferLookup(
                run_id=run_id,
                transfer_id=transfer_id,
                state="committed" if result else "unconfirmed",
                result=result,
            )

    def transfer(
        self, ctx: TrustedContext, run_id: UUID, transfer_id: str, spec: TransferClientRun
    ) -> tuple[bool, ClientRunTransferResult]:
        with self.runs.uow.transaction() as tx:
            before = self.runs._read(tx, ctx, run_id)
            run_ref = self.runs.ref(ctx, run_id)
            self.runs.identity.authorize(tx, ctx, Permission.RECOVER, run_ref)
            receipt_ref = self.ref(ctx, run_id, transfer_id)
            prior = tx.get(receipt_ref)
            if prior is not None:
                result = ClientRunTransferResult.model_validate(prior)
                if (
                    result.run_id != run_id
                    or result.transfer_id != transfer_id
                    or result.request != spec
                ):
                    raise FoundationError(
                        ErrorCode.IDEMPOTENCY_CONFLICT, "original transfer intent changed"
                    )
                return False, result
            index = tx.get(self.runs.ref(ctx, None))
            ownership = before.ownership
            if (
                ownership is None
                or before.snapshot["state"] not in {"queued", "running", "unconfirmed"}
                or index is None
                or index["active"] != str(run_id)
                or before.owner_id != spec.expected_owner_id
                or before.revision != spec.expected_revision
                or client_run_hash(before) != spec.expected_record_hash
                or spec.new_owner_id in {epoch.owner_id for epoch in ownership.epochs}
            ):
                raise FoundationError(
                    ErrorCode.VERSION_CONFLICT, "original owner, active slot or record changed"
                )
            if len(ownership.epochs) >= MAX_OWNER_EPOCHS:
                raise FoundationError(
                    ErrorCode.CAPACITY_EXCEEDED, "caller ownership history limit reached"
                )
            updated = before.model_copy(
                update={
                    "owner_id": spec.new_owner_id,
                    "revision": before.revision + 1,
                    "updated_at": self.runs.clock(),
                    "ownership": ClientOwnership(
                        epochs=(
                            *ownership.epochs,
                            ClientOwnerEpoch(
                                owner_id=spec.new_owner_id,
                                first_revision=before.revision + 1,
                                transfer_id=transfer_id,
                            ),
                        ),
                        recovery_transfer_id=transfer_id,
                    ),
                }
            )
            result = ClientRunTransferResult(
                run_id=run_id,
                transfer_id=transfer_id,
                request=spec,
                previous=before,
                record=updated,
            )
            tx.put_if_revision(run_ref, updated.model_dump(mode="json"), before.revision)
            tx.put_if_revision(receipt_ref, result.model_dump(mode="json"), None)
            return True, result
