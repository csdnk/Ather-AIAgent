"""Read pending original objects under an unchanged, authorized recovery hold."""

from hashlib import sha256
from uuid import UUID

from aether_agent_memory.runtime.contracts.client_definitions import ClientDefinitionBinding
from aether_agent_memory.runtime.contracts.client_inputs import ClientInputBinding
from aether_agent_memory.runtime.contracts.client_recovery_reads import (
    ClientRecoveryObjectEvidence,
    ReadClientRecovery,
    RecoveredClientObject,
    RecoveryObjectKind,
)
from aether_agent_memory.runtime.contracts.client_runs import ClientRunRecord
from aether_agent_memory.runtime.contracts.client_state_binding import ClientStateBinding
from aether_agent_memory.runtime.contracts.client_states import ClientExecutionState
from aether_agent_memory.runtime.contracts.client_transfers import ClientRunTransferResult
from aether_agent_memory.runtime.contracts.models import (
    ErrorCode,
    Permission,
    RecordRef,
    TrustedContext,
)
from aether_agent_memory.runtime.contracts.ports import Transaction

from .client_definitions import ClientDefinitions, StoredDefinition
from .client_inputs import ClientInputs, StoredClientInput
from .client_states import ClientStates, StoredClientState
from .client_transfers import ClientTransfers
from .common import FoundationError

StoredObject = StoredDefinition | StoredClientInput | StoredClientState


class ClientRecoveryReads:
    def __init__(self, definitions: ClientDefinitions, inputs: ClientInputs, states: ClientStates):
        self.definitions, self.inputs, self.states = definitions, inputs, states
        self.runs = states.runs

    def held(
        self,
        tx: Transaction,
        ctx: TrustedContext,
        run_id: UUID,
        transfer_id: str,
        expected: ReadClientRecovery,
    ) -> ClientRunRecord:
        run = self.runs._read(tx, ctx, run_id)
        self.runs.identity.authorize(tx, ctx, Permission.RECOVER, self.runs.ref(ctx, run_id))
        try:
            expected.require_original(run, transfer_id)
        except ValueError:
            raise FoundationError(
                ErrorCode.VERSION_CONFLICT, "recovery read owner or record changed"
            ) from None
        index = tx.get(self.runs.ref(ctx, None))
        if index is None or index["active"] != str(run_id):
            raise FoundationError(ErrorCode.VERSION_CONFLICT, "recovery no longer owns active slot")
        raw = tx.get(ClientTransfers.ref(ctx, run_id, transfer_id))
        try:
            if raw is None or ClientRunTransferResult.model_validate(raw).record != run:
                raise ValueError("hold differs from transfer receipt")
        except ValueError:
            raise FoundationError(
                ErrorCode.CONTRACT_VIOLATION, "original recovery hold is not confirmed"
            ) from None
        return run

    def reservation(
        self,
        tx: Transaction,
        ctx: TrustedContext,
        run: ClientRunRecord,
        kind: RecoveryObjectKind,
        operation_id: str | None,
        sequence: int | None,
        stream_id: str | None,
    ) -> tuple[RecordRef, StoredObject]:
        stored: StoredObject | None
        try:
            if kind == "definition":
                ref = self.definitions.ref(ctx, run.run_id)
                stored = self.definitions.metadata(tx, ctx, run)
            elif kind == "input":
                assert operation_id is not None
                _, _, ref, stored = self.inputs.load(tx, ctx, run.run_id, operation_id)
            else:
                assert sequence is not None
                ref = self.states.ref(ctx, run.run_id, sequence, stream_id)
                stored = self.states.metadata(tx, ctx, run, sequence, stream_id)
                if stored is not None and stored.state == "ready":
                    self.states.published(tx, ctx, run, stored)
        except ValueError:
            raise FoundationError(
                ErrorCode.CONTRACT_VIOLATION, "original recovery object metadata is corrupt"
            ) from None
        if stored is None:
            raise FoundationError(ErrorCode.NOT_FOUND, "original recovery object not recorded")
        return ref, stored

    def read(
        self,
        ctx: TrustedContext,
        run_id: UUID,
        transfer_id: str,
        expected: ReadClientRecovery,
        kind: RecoveryObjectKind,
        *,
        operation_id: str | None = None,
        sequence: int | None = None,
        stream_id: str | None = None,
    ) -> RecoveredClientObject:
        with self.runs.uow.transaction() as tx:
            run = self.held(tx, ctx, run_id, transfer_id, expected)
            ref, stored = self.reservation(tx, ctx, run, kind, operation_id, sequence, stream_id)
        binding: ClientDefinitionBinding | ClientInputBinding | ClientStateBinding
        if isinstance(stored, StoredClientInput):
            binding = ClientInputBinding.model_validate(
                stored.model_dump(include=set(ClientInputBinding.model_fields))
            )
            key = self.inputs.object_key(ref, binding.request_hash)
            digest, maximum = binding.request_hash, self.inputs.max_bytes
        else:
            binding = stored.binding
            digest = binding.content_hash
            key = (
                self.definitions.key(ref, digest)
                if kind == "definition"
                else self.states.key(ref, digest)
            )
            maximum = self.definitions.max_bytes if kind == "definition" else self.states.max_bytes
        objects = self.inputs.objects
        if objects is None:
            raise FoundationError(ErrorCode.DEPENDENCY_UNAVAILABLE, "P2 recovery objects required")
        try:
            payload = objects.get_object_sync(key)
        except FoundationError:
            raise
        except Exception:
            raise FoundationError(
                ErrorCode.DEPENDENCY_UNAVAILABLE, "P2 recovery read unavailable"
            ) from None
        # Revalidate even when bytes are missing; stale readers receive no object evidence.
        with self.runs.uow.transaction() as tx:
            current = self.held(tx, ctx, run_id, transfer_id, expected)
            _, after = self.reservation(tx, ctx, current, kind, operation_id, sequence, stream_id)
            if after != stored:
                raise FoundationError(
                    ErrorCode.VERSION_CONFLICT, "recovery object changed during read"
                )
        if payload is None and stored.state == "pending":
            raise FoundationError(
                ErrorCode.COMMIT_UNCONFIRMED, "original pending bytes unavailable"
            )
        if (
            payload is None
            or len(payload) != binding.size_bytes
            or len(payload) > maximum
            or sha256(payload).hexdigest() != digest
        ):
            raise FoundationError(
                ErrorCode.CONTRACT_VIOLATION, "original recovery bytes missing or corrupt"
            )
        if isinstance(stored, StoredClientState):
            try:
                envelope = ClientExecutionState.from_bytes(payload)
                if envelope.binding(payload) != stored.binding:
                    raise ValueError("recovery state binding changed")
                self.states.matches_run(run, envelope)
            except (ValueError, RecursionError, FoundationError):
                raise FoundationError(
                    ErrorCode.CONTRACT_VIOLATION, "original recovery state is corrupt"
                ) from None
        evidence = ClientRecoveryObjectEvidence(
            run_id=run_id,
            transfer_id=transfer_id,
            record_hash=expected.expected_record_hash,
            kind=kind,
            state=stored.state,
            object_revision=stored.revision,
            binding=binding,
        )
        return RecoveredClientObject(evidence, payload)
