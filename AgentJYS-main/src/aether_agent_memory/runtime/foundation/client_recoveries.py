"""Stage original recovery bytes, then atomically activate the owned state chain."""

from typing import Self
from uuid import UUID

from pydantic import model_validator

from aether_agent_memory.runtime.contracts.client_recoveries import (
    ActivateClientRun,
    ClientRunRecoveryLookup,
    ClientRunRecoveryResult,
    PrepareClientRecovery,
)
from aether_agent_memory.runtime.contracts.client_runs import ClientRunRecord
from aether_agent_memory.runtime.contracts.client_transfers import ClientRunTransferResult
from aether_agent_memory.runtime.contracts.models import (
    ContractModel,
    ErrorCode,
    Identifier,
    Permission,
    Positive,
    RecordRef,
    TrustedContext,
)
from aether_agent_memory.runtime.contracts.ports import Transaction

from .client_definitions import ClientDefinitions
from .client_states import ClientStates, StoredClientState
from .client_transfers import ClientTransfers
from .common import FoundationError


class StoredClientRecovery(ContractModel):
    run_id: UUID
    transfer_id: Identifier
    request: ActivateClientRun
    result: ClientRunRecoveryResult | None = None
    revision: Positive

    @model_validator(mode="after")
    def coherent(self) -> Self:
        if self.revision != (1 if self.result is None else 2):
            raise ValueError("recovery revision differs from its commit state")
        if self.result is not None and (
            self.result.run_id != self.run_id
            or self.result.transfer_id != self.transfer_id
            or self.result.request != self.request
        ):
            raise ValueError("recovery receipt differs from original reservation")
        return self


class ClientRecoveries:
    def __init__(self, states: ClientStates):
        self.states, self.runs = states, states.runs

    @staticmethod
    def ref(ctx: TrustedContext, run_id: UUID, transfer_id: str) -> RecordRef:
        return ClientTransfers.ref(ctx, run_id, transfer_id).model_copy(
            update={"object_type": "client_run_recovery"}
        )

    def stored(
        self, tx: Transaction, ctx: TrustedContext, run_id: UUID, transfer_id: str
    ) -> StoredClientRecovery | None:
        raw = tx.get(self.ref(ctx, run_id, transfer_id))
        if raw is None:
            return None
        try:
            value = StoredClientRecovery.model_validate(raw)
        except ValueError:
            raise FoundationError(
                ErrorCode.CONTRACT_VIOLATION, "original recovery metadata is corrupt"
            ) from None
        if value.run_id != run_id or value.transfer_id != transfer_id:
            raise FoundationError(
                ErrorCode.CONTRACT_VIOLATION, "original recovery identity changed"
            )
        return value

    def lookup(
        self, ctx: TrustedContext, run_id: UUID, transfer_id: str
    ) -> ClientRunRecoveryLookup:
        with self.runs.uow.transaction() as tx:
            self.runs._read(tx, ctx, run_id)
            stored = self.stored(tx, ctx, run_id, transfer_id)
            result = None if stored is None else stored.result
            return ClientRunRecoveryLookup(
                run_id=run_id,
                transfer_id=transfer_id,
                state="committed" if result else "unconfirmed",
                result=result,
            )

    def held(
        self,
        tx: Transaction,
        ctx: TrustedContext,
        run_id: UUID,
        transfer_id: str,
        intent: ActivateClientRun,
    ) -> ClientRunRecord:
        run = self.runs._read(tx, ctx, run_id)
        self.runs.identity.authorize(tx, ctx, Permission.RECOVER, self.runs.ref(ctx, run_id))
        try:
            intent.require_original(run, transfer_id)
        except ValueError:
            raise FoundationError(
                ErrorCode.VERSION_CONFLICT, "original recovery owner or record changed"
            ) from None
        index = tx.get(self.runs.ref(ctx, None))
        if index is None or index["active"] != str(run_id):
            raise FoundationError(ErrorCode.VERSION_CONFLICT, "recovery no longer owns active slot")
        raw = tx.get(ClientTransfers.ref(ctx, run_id, transfer_id))
        if raw is None or ClientRunTransferResult.model_validate(raw).record != run:
            raise FoundationError(
                ErrorCode.CONTRACT_VIOLATION, "original recovery hold is not confirmed"
            )
        if intent.target_state == "running":
            ClientDefinitions.require_ready(tx, ctx, run)
            head = run.execution_state
            if head is not None:
                parent = self.states.metadata(tx, ctx, run, head.sequence, head.stream_id)
                if parent is None or parent.state != "ready" or parent.binding != head:
                    raise FoundationError(
                        ErrorCode.CONTRACT_VIOLATION, "recovery parent is not confirmed"
                    )
        return run

    def finish(
        self,
        tx: Transaction,
        ctx: TrustedContext,
        run: ClientRunRecord,
        stored: StoredClientRecovery,
    ) -> ClientRunRecoveryResult:
        assert run.ownership is not None
        updated = run.model_copy(
            update={
                "revision": run.revision + 1,
                "updated_at": self.runs.clock(),
                "snapshot": {**run.snapshot, "state": stored.request.target_state},
                "execution_state": stored.request.state,
                "ownership": run.ownership.model_copy(update={"recovery_transfer_id": None}),
            }
        )
        result = ClientRunRecoveryResult(
            run_id=run.run_id,
            transfer_id=stored.transfer_id,
            request=stored.request,
            previous=run,
            record=updated,
        )
        tx.put_if_revision(
            self.runs.ref(ctx, run.run_id), updated.model_dump(mode="json"), run.revision
        )
        completed = stored.model_copy(update={"result": result, "revision": stored.revision + 1})
        tx.put_if_revision(
            self.ref(ctx, run.run_id, stored.transfer_id),
            completed.model_dump(mode="json"),
            stored.revision,
        )
        return result

    def activate(
        self, ctx: TrustedContext, run_id: UUID, transfer_id: str, spec: PrepareClientRecovery
    ) -> tuple[bool, ClientRunRecoveryResult]:
        payload, intent = spec.payload(), spec.intent()
        if payload is not None and len(payload) > self.states.max_bytes:
            raise FoundationError(
                ErrorCode.INVALID_ARGUMENT, "recovery state exceeds ingress limit"
            )
        with self.runs.uow.transaction() as tx:
            self.runs._read(tx, ctx, run_id)
            self.runs.identity.authorize(tx, ctx, Permission.RECOVER, self.runs.ref(ctx, run_id))
            prior = self.stored(tx, ctx, run_id, transfer_id)
            if prior is not None:
                if prior.request != intent:
                    raise FoundationError(
                        ErrorCode.IDEMPOTENCY_CONFLICT, "original recovery intent changed"
                    )
                if prior.result is not None:
                    return False, prior.result
            run = self.held(tx, ctx, run_id, transfer_id, intent)
            if prior is None:
                prior = StoredClientRecovery(
                    run_id=run_id, transfer_id=transfer_id, request=intent, revision=1
                )
                tx.put_if_revision(
                    self.ref(ctx, run_id, transfer_id), prior.model_dump(mode="json"), None
                )
            if intent.target_state == "queued":
                return True, self.finish(tx, ctx, run, prior)
            assert (
                intent.state is not None
                and spec.execution_state is not None
                and payload is not None
            )
            self.states.matches_run(run, spec.execution_state)
            binding = intent.state
            state_ref = self.states.ref(ctx, run_id, binding.sequence, binding.stream_id)
            pending = self.states.metadata(tx, ctx, run, binding.sequence, binding.stream_id)
            expected = StoredClientState(
                run_id=run_id,
                binding=binding,
                writer_id=run.owner_id,
                expected_run_revision=run.revision,
                state="pending",
                revision=1,
            )
            if pending is None:
                pending = expected
                tx.put_if_revision(state_ref, pending.model_dump(mode="json"), None)
            elif pending != expected:
                raise FoundationError(
                    ErrorCode.IDEMPOTENCY_CONFLICT, "recovery state reservation changed"
                )
        self.states.store_bytes(state_ref, pending, payload)
        with self.runs.uow.transaction() as tx:
            stored = self.stored(tx, ctx, run_id, transfer_id)
            self.runs._read(tx, ctx, run_id)
            self.runs.identity.authorize(tx, ctx, Permission.RECOVER, self.runs.ref(ctx, run_id))
            if stored is None or stored.request != intent:
                raise FoundationError(ErrorCode.CONTRACT_VIOLATION, "recovery intent disappeared")
            if stored.result is not None:
                return False, stored.result
            run = self.held(tx, ctx, run_id, transfer_id, intent)
            current = self.states.metadata(tx, ctx, run, binding.sequence, binding.stream_id)
            if current != pending:
                raise FoundationError(
                    ErrorCode.CONTRACT_VIOLATION, "recovery reservation changed during IO"
                )
            ready = pending.model_copy(update={"state": "ready", "revision": pending.revision + 1})
            tx.put_if_revision(state_ref, ready.model_dump(mode="json"), pending.revision)
            return True, self.finish(tx, ctx, run, stored)
