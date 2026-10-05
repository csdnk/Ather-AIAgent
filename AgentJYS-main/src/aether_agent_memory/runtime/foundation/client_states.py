"""Fenced execution-state publication through existing transactions and P2 IO."""

from hashlib import sha256
from typing import Literal
from uuid import UUID

from aether_agent_memory.runtime.contracts.client_runs import ClientOperation, ClientRunRecord
from aether_agent_memory.runtime.contracts.client_state_binding import (
    STATE_MAX_BYTES,
    ClientStateBinding,
    StateSequence,
)
from aether_agent_memory.runtime.contracts.client_states import (
    ClientExecutionState,
    operations_hash,
)
from aether_agent_memory.runtime.contracts.models import (
    ContractModel,
    ErrorCode,
    Identifier,
    Positive,
    RecordRef,
    TrustedContext,
)
from aether_agent_memory.runtime.contracts.ports import Transaction

from .client_definitions import ClientDefinitions
from .client_inputs import InputObjects
from .client_runs import ClientRuns
from .common import FoundationError, fingerprint


class StoredClientState(ContractModel):
    run_id: UUID
    binding: ClientStateBinding
    writer_id: Identifier
    expected_run_revision: Positive
    state: Literal["pending", "ready"]
    revision: Positive

    def require_ready(self) -> None:
        if self.state != "ready":
            raise FoundationError(ErrorCode.COMMIT_UNCONFIRMED, "execution state not confirmed")


class ClientStates:
    def __init__(self, runs: ClientRuns, objects: InputObjects | None, max_bytes: int):
        self.runs, self.objects = runs, objects
        self.max_bytes = min(max_bytes, STATE_MAX_BYTES)

    @staticmethod
    def ref(
        ctx: TrustedContext, run_id: UUID, sequence: int, stream_id: str | None = None
    ) -> RecordRef:
        run_ref = ClientRuns.ref(ctx, run_id)
        identity = [run_ref.model_dump(mode="json"), sequence]
        if stream_id is not None:
            identity.append(stream_id)
        return run_ref.model_copy(
            update={
                "object_type": "client_run_state",
                "object_id": fingerprint(identity),
            }
        )

    @staticmethod
    def key(ref: RecordRef, content_hash: str) -> str:
        return (
            "runtime/client-states/" + fingerprint(ref.model_dump(mode="json")) + "/" + content_hash
        )

    @classmethod
    def metadata(
        cls,
        tx: Transaction,
        ctx: TrustedContext,
        run: ClientRunRecord,
        sequence: int,
        stream_id: str | None = None,
    ) -> StoredClientState | None:
        if run.state_policy is None or run.definition is None:
            raise FoundationError(
                ErrorCode.VERSION_CONFLICT, "original execution-state policy missing"
            )
        raw = tx.get(cls.ref(ctx, run.run_id, sequence, stream_id))
        stored = StoredClientState.model_validate(raw) if raw is not None else None
        if stored is not None and (
            stored.run_id != run.run_id
            or stored.binding.sequence != sequence
            or stored.binding.stream_id != stream_id
            or stored.binding.definition_hash != run.definition.content_hash
        ):
            raise FoundationError(
                ErrorCode.CONTRACT_VIOLATION, "original execution-state binding changed"
            )
        return stored

    @classmethod
    def require_ready(cls, tx: Transaction, ctx: TrustedContext, run: ClientRunRecord) -> None:
        if run.state_policy is None:
            return
        head = run.execution_state
        if head is None:
            raise FoundationError(ErrorCode.COMMIT_UNCONFIRMED, "execution state not saved")
        stored = cls.metadata(tx, ctx, run, head.sequence, head.stream_id)
        if stored is None:
            raise FoundationError(ErrorCode.COMMIT_UNCONFIRMED, "execution state not stored")
        stored.require_ready()
        if stored.binding != head:
            raise FoundationError(ErrorCode.CONTRACT_VIOLATION, "execution-state head changed")
        if head.operations_hash != cls.journal_hash(run):
            raise FoundationError(
                ErrorCode.COMMIT_UNCONFIRMED, "current operation journal not checkpointed"
            )

    @staticmethod
    def journal_hash(run: ClientRunRecord) -> str:
        raw = run.snapshot.get("operations", [])
        assert isinstance(raw, list)
        return operations_hash([ClientOperation.model_validate(item) for item in raw])

    def writer(
        self, tx: Transaction, ctx: TrustedContext, run: ClientRunRecord, owner: str
    ) -> None:
        index = tx.get(self.runs.ref(ctx, None))
        if (
            run.owner_id != owner
            or run.recovery_held
            or run.snapshot["state"] not in {"queued", "running"}
            or index is None
            or index["active"] != str(run.run_id)
        ):
            raise FoundationError(
                ErrorCode.VERSION_CONFLICT, "execution-state writer changed or closed"
            )
        ClientDefinitions.require_ready(tx, ctx, run)

    @staticmethod
    def matches_run(run: ClientRunRecord, envelope: ClientExecutionState) -> None:
        if (
            envelope.run_id != run.run_id
            or envelope.scenario_id != run.scenario_id
            or envelope.scope_id != run.scope_id
            or run.definition is None
            or envelope.definition_hash != run.definition.content_hash
        ):
            raise FoundationError(
                ErrorCode.VERSION_CONFLICT, "execution state differs from original run"
            )

    def fence(
        self,
        tx: Transaction,
        ctx: TrustedContext,
        run: ClientRunRecord,
        stored: StoredClientState,
        owner: str,
        revision: int,
    ) -> None:
        self.writer(tx, ctx, run, owner)
        if stored.binding.stream_id != run.state_stream_id:
            raise FoundationError(ErrorCode.VERSION_CONFLICT, "execution-state stream changed")
        if stored.writer_id != owner or stored.expected_run_revision != revision:
            raise FoundationError(
                ErrorCode.VERSION_CONFLICT, "original execution-state writer changed"
            )
        if stored.state == "ready":
            if run.execution_state != stored.binding or run.revision != revision + 1:
                raise FoundationError(
                    ErrorCode.VERSION_CONFLICT, "execution-state retry no longer current"
                )
        else:
            head = run.execution_state
            if (
                run.revision != revision
                or stored.binding.sequence != (1 if head is None else head.sequence + 1)
                or stored.binding.parent_hash != (None if head is None else head.content_hash)
                or stored.binding.parent_stream_id != (None if head is None else head.stream_id)
                or stored.binding.operations_hash != self.journal_hash(run)
            ):
                raise FoundationError(
                    ErrorCode.VERSION_CONFLICT,
                    "execution-state parent or operation journal changed",
                )
            if head is not None:
                parent = self.metadata(tx, ctx, run, head.sequence, head.stream_id)
                if parent is None or parent.state != "ready" or parent.binding != head:
                    raise FoundationError(
                        ErrorCode.CONTRACT_VIOLATION, "execution-state parent not confirmed"
                    )

    def prepare(
        self,
        ctx: TrustedContext,
        run_id: UUID,
        sequence: StateSequence,
        owner: str,
        revision: int,
        payload: bytes,
    ) -> ClientRunRecord:
        if not payload or len(payload) > self.max_bytes:
            raise FoundationError(
                ErrorCode.INVALID_ARGUMENT, "execution state exceeds ingress limit"
            )
        try:
            envelope = ClientExecutionState.from_bytes(payload)
            binding = envelope.binding(payload)
        except (ValueError, RecursionError):
            raise FoundationError(ErrorCode.INVALID_ARGUMENT, "invalid execution state") from None
        if envelope.run_id != run_id or envelope.sequence != sequence:
            raise FoundationError(
                ErrorCode.VERSION_CONFLICT, "execution-state request target changed"
            )
        ref = self.ref(ctx, run_id, sequence, binding.stream_id)
        with self.runs.uow.transaction() as tx:
            run = self.runs._read(tx, ctx, run_id)
            self.matches_run(run, envelope)
            stored = self.metadata(tx, ctx, run, sequence, binding.stream_id)
            if stored is None:
                stored = StoredClientState(
                    run_id=run_id,
                    binding=binding,
                    writer_id=owner,
                    expected_run_revision=revision,
                    state="pending",
                    revision=1,
                )
                self.fence(tx, ctx, run, stored, owner, revision)
                tx.put_if_revision(ref, stored.model_dump(mode="json"), None)
            else:
                if stored.binding != binding:
                    raise FoundationError(
                        ErrorCode.IDEMPOTENCY_CONFLICT, "original execution-state bytes changed"
                    )
                self.fence(tx, ctx, run, stored, owner, revision)
        self.store_bytes(ref, stored, payload)
        with self.runs.uow.transaction() as tx:
            run = self.runs._read(tx, ctx, run_id)
            current = self.metadata(tx, ctx, run, sequence, binding.stream_id)
            if current is None or current.binding != stored.binding:
                raise FoundationError(
                    ErrorCode.CONTRACT_VIOLATION, "execution-state reservation changed"
                )
            self.fence(tx, ctx, run, current, owner, revision)
            if current.state == "ready":
                return run
            confirmed = current.model_copy(
                update={"state": "ready", "revision": current.revision + 1}
            )
            updated = run.model_copy(
                update={
                    "execution_state": binding,
                    "revision": run.revision + 1,
                    "updated_at": self.runs.clock(),
                }
            )
            tx.put_if_revision(ref, confirmed.model_dump(mode="json"), current.revision)
            tx.put_if_revision(
                self.runs.ref(ctx, run_id), updated.model_dump(mode="json"), run.revision
            )
            return updated

    def store_bytes(self, ref: RecordRef, stored: StoredClientState, payload: bytes) -> None:
        if self.objects is None:
            raise FoundationError(ErrorCode.DEPENDENCY_UNAVAILABLE, "P2 state objects required")
        key = self.key(ref, stored.binding.content_hash)
        try:
            previous = self.objects.get_object_sync(key)
            if stored.state == "ready":
                self.verify_bytes(previous, stored.binding)
                return
            if previous is None:
                self.objects.put_object_sync(key, payload)
            elif previous != payload:
                raise FoundationError(
                    ErrorCode.CONTRACT_VIOLATION, "original execution state changed"
                )
            if self.objects.get_object_sync(key) != payload:
                raise FoundationError(
                    ErrorCode.COMMIT_UNCONFIRMED, "P2 execution state not confirmed"
                )
        except FoundationError:
            raise
        except Exception:
            raise FoundationError(
                ErrorCode.DEPENDENCY_UNAVAILABLE, "P2 execution-state write unavailable"
            ) from None

    def verify_bytes(self, payload: bytes | None, binding: ClientStateBinding) -> bytes:
        if (
            payload is None
            or len(payload) != binding.size_bytes
            or len(payload) > self.max_bytes
            or sha256(payload).hexdigest() != binding.content_hash
        ):
            raise FoundationError(
                ErrorCode.CONTRACT_VIOLATION, "original execution state missing or corrupt"
            )
        return payload

    @classmethod
    def published(
        cls, tx: Transaction, ctx: TrustedContext, run: ClientRunRecord, stored: StoredClientState
    ) -> None:
        head = run.execution_state
        sequence = stored.binding.sequence
        if head is None or sequence > head.sequence:
            raise FoundationError(ErrorCode.CONTRACT_VIOLATION, "execution state not published")
        current = cls.metadata(tx, ctx, run, head.sequence, head.stream_id)
        if current is None or current.state != "ready" or current.binding != head:
            raise FoundationError(ErrorCode.CONTRACT_VIOLATION, "execution-state head changed")
        cursor = head
        # Sequence decreases on every read, with the contract's hard bound of 1024.
        while cursor.sequence > sequence:
            parent = cls.metadata(tx, ctx, run, cursor.sequence - 1, cursor.parent_stream_id)
            if (
                parent is None
                or parent.state != "ready"
                or parent.binding.content_hash != cursor.parent_hash
            ):
                raise FoundationError(
                    ErrorCode.CONTRACT_VIOLATION, "execution-state parent link changed"
                )
            cursor = parent.binding
        if cursor != stored.binding:
            raise FoundationError(
                ErrorCode.CONTRACT_VIOLATION, "execution state is not a published ancestor"
            )

    def read(
        self,
        ctx: TrustedContext,
        run_id: UUID,
        sequence: StateSequence,
        stream_id: str | None = None,
    ) -> bytes:
        with self.runs.uow.transaction() as tx:
            run = self.runs._read(tx, ctx, run_id)
            stored = self.metadata(tx, ctx, run, sequence, stream_id)
            if stored is None:
                raise FoundationError(ErrorCode.NOT_FOUND, "execution state not stored")
            stored.require_ready()
            self.published(tx, ctx, run, stored)
        if self.objects is None:
            raise FoundationError(ErrorCode.DEPENDENCY_UNAVAILABLE, "P2 state objects required")
        try:
            result = self.objects.get_object_sync(
                self.key(self.ref(ctx, run_id, sequence, stream_id), stored.binding.content_hash)
            )
        except FoundationError:
            raise
        except Exception:
            raise FoundationError(
                ErrorCode.DEPENDENCY_UNAVAILABLE, "P2 execution-state read unavailable"
            ) from None
        payload = self.verify_bytes(result, stored.binding)
        with self.runs.uow.transaction() as tx:
            current = self.runs._read(tx, ctx, run_id)
            if self.metadata(tx, ctx, current, sequence, stream_id) != stored:
                raise FoundationError(
                    ErrorCode.VERSION_CONFLICT, "execution state changed during read"
                )
            self.published(tx, ctx, current, stored)
        return payload
