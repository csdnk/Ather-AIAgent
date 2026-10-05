"""Pre-send byte storage over existing P2 object and transaction contracts."""

from hashlib import sha256
from typing import Any, Literal, Protocol
from uuid import UUID

from aether_agent_memory.runtime.contracts.client_inputs import (
    ClientInputBinding,
    ClientInputReceipt,
)
from aether_agent_memory.runtime.contracts.client_runs import ClientOperation, ClientRunRecord
from aether_agent_memory.runtime.contracts.models import (
    ErrorCode,
    Identifier,
    Positive,
    RecordRef,
    TrustedContext,
)
from aether_agent_memory.runtime.contracts.ports import Transaction

from .client_runs import ClientRuns
from .common import FoundationError, fingerprint


class InputObjects(Protocol):
    def put_object_sync(self, key: str, body: bytes) -> Any: ...
    def get_object_sync(self, key: str) -> bytes | None: ...


class StoredClientInput(ClientInputBinding):
    writer_id: Identifier
    state: Literal["pending", "ready"]
    revision: Positive

    def receipt(self) -> ClientInputReceipt:
        if self.state != "ready":
            raise FoundationError(ErrorCode.COMMIT_UNCONFIRMED, "caller input not confirmed")
        return ClientInputReceipt.model_validate(
            self.model_dump(include=set(ClientInputBinding.model_fields))
        )


class ClientInputs:
    def __init__(self, runs: ClientRuns, objects: InputObjects | None, max_bytes: int):
        self.runs, self.objects, self.max_bytes = runs, objects, max_bytes

    @staticmethod
    def reference(ctx: TrustedContext, run_id: UUID, operation_id: str) -> RecordRef:
        run_ref = ClientRuns.ref(ctx, run_id)
        return run_ref.model_copy(
            update={
                "object_type": "client_request_input",
                "object_id": fingerprint([run_ref.model_dump(mode="json"), operation_id]),
            }
        )

    @staticmethod
    def object_key(ref: RecordRef, digest: str) -> str:
        return "runtime/client-inputs/" + fingerprint(ref.model_dump(mode="json")) + "/" + digest

    @staticmethod
    def operation(run: ClientRunRecord, operation_id: str) -> ClientOperation:
        values = run.snapshot.get("operations", [])
        assert isinstance(values, list)
        for value in values:
            operation = ClientOperation.model_validate(value)
            if operation.operation_id == operation_id:
                if operation.binding is None:
                    raise FoundationError(
                        ErrorCode.VERSION_CONFLICT, "original HTTP binding missing"
                    )
                return operation
        raise FoundationError(ErrorCode.NOT_FOUND, "original caller operation missing")

    def load(
        self,
        tx: Transaction,
        ctx: TrustedContext,
        run_id: UUID,
        operation_id: str,
    ) -> tuple[ClientRunRecord, ClientOperation, RecordRef, StoredClientInput | None]:
        run = self.runs._read(tx, ctx, run_id)
        operation = self.operation(run, operation_id)
        ref = self.reference(ctx, run_id, operation_id)
        value = tx.get(ref)
        stored = StoredClientInput.model_validate(value) if value is not None else None
        assert operation.binding is not None
        if stored is not None and (
            stored.run_id != run_id
            or stored.operation_id != operation_id
            or stored.request_hash != operation.request_hash
            or stored.binding_digest != operation.binding.digest
            or stored.size_bytes > self.max_bytes
        ):
            raise FoundationError(ErrorCode.CONTRACT_VIOLATION, "original input binding changed")
        return run, operation, ref, stored

    def writable(
        self,
        tx: Transaction,
        ctx: TrustedContext,
        run: ClientRunRecord,
        operation: ClientOperation,
        owner_id: str,
        expected_revision: int,
    ) -> None:
        index = tx.get(self.runs.ref(ctx, None))
        if (
            run.owner_id != owner_id
            or run.recovery_held
            or run.revision != expected_revision
            or run.snapshot["state"] not in {"queued", "running"}
            or operation.phase != "prepared"
            or index is None
            or index["active"] != str(run.run_id)
        ):
            raise FoundationError(
                ErrorCode.VERSION_CONFLICT, "caller input writer changed or closed"
            )

    def prepare(
        self,
        ctx: TrustedContext,
        run_id: UUID,
        operation_id: str,
        owner_id: str,
        expected_revision: int,
        payload: bytes,
    ) -> ClientInputReceipt:
        if len(payload) > self.max_bytes:
            raise FoundationError(ErrorCode.INVALID_ARGUMENT, "caller input exceeds ingress limit")
        with self.runs.uow.transaction() as tx:
            run, operation, ref, stored = self.load(tx, ctx, run_id, operation_id)
            if run.owner_id != owner_id:
                raise FoundationError(ErrorCode.VERSION_CONFLICT, "caller input owner changed")
            if sha256(payload).hexdigest() != operation.request_hash:
                raise FoundationError(ErrorCode.IDEMPOTENCY_CONFLICT, "caller input bytes changed")
            if stored is not None and stored.size_bytes != len(payload):
                raise FoundationError(ErrorCode.IDEMPOTENCY_CONFLICT, "caller input size changed")
            ready = stored is not None and stored.state == "ready"
            if not ready:
                self.writable(tx, ctx, run, operation, owner_id, expected_revision)
            if self.objects is None:
                raise FoundationError(ErrorCode.DEPENDENCY_UNAVAILABLE, "P2 input objects required")
            if stored is None:
                assert operation.binding is not None
                stored = StoredClientInput(
                    run_id=run_id,
                    operation_id=operation_id,
                    request_hash=operation.request_hash,
                    binding_digest=operation.binding.digest,
                    size_bytes=len(payload),
                    writer_id=owner_id,
                    state="pending",
                    revision=1,
                )
                tx.put_if_revision(ref, stored.model_dump(mode="json"), None)
        if ready:
            self.read(ctx, run_id, operation_id)
            with self.runs.uow.transaction() as tx:
                run, _, _, current = self.load(tx, ctx, run_id, operation_id)
                if run.owner_id != owner_id or current != stored:
                    raise FoundationError(
                        ErrorCode.VERSION_CONFLICT, "caller input owner changed during read"
                    )
            return stored.receipt()
        key = self.object_key(ref, stored.request_hash)
        try:
            previous = self.objects.get_object_sync(key)
            if previous is None:
                self.objects.put_object_sync(key, payload)
            elif previous != payload:
                raise FoundationError(ErrorCode.CONTRACT_VIOLATION, "original input object changed")
            if self.objects.get_object_sync(key) != payload:
                raise FoundationError(ErrorCode.COMMIT_UNCONFIRMED, "P2 input bytes not confirmed")
        except FoundationError:
            raise
        except Exception:
            raise FoundationError(
                ErrorCode.DEPENDENCY_UNAVAILABLE, "P2 input write unavailable"
            ) from None
        with self.runs.uow.transaction() as tx:
            run, operation, ref, current = self.load(tx, ctx, run_id, operation_id)
            self.writable(tx, ctx, run, operation, owner_id, expected_revision)
            if current is None:
                raise FoundationError(ErrorCode.CONTRACT_VIOLATION, "input preparation disappeared")
            if current.state == "pending":
                current = current.model_copy(
                    update={"state": "ready", "revision": current.revision + 1}
                )
                tx.put_if_revision(ref, current.model_dump(mode="json"), current.revision - 1)
            return current.receipt()

    def read(self, ctx: TrustedContext, run_id: UUID, operation_id: str) -> bytes:
        with self.runs.uow.transaction() as tx:
            _, _, ref, stored = self.load(tx, ctx, run_id, operation_id)
            if stored is None:
                raise FoundationError(ErrorCode.NOT_FOUND, "caller input not recorded")
            stored.receipt()
        if self.objects is None:
            raise FoundationError(ErrorCode.DEPENDENCY_UNAVAILABLE, "P2 input objects required")
        try:
            payload = self.objects.get_object_sync(self.object_key(ref, stored.request_hash))
        except FoundationError:
            raise
        except Exception:
            raise FoundationError(
                ErrorCode.DEPENDENCY_UNAVAILABLE, "P2 input read unavailable"
            ) from None
        if (
            payload is None
            or len(payload) != stored.size_bytes
            or sha256(payload).hexdigest() != stored.request_hash
        ):
            raise FoundationError(ErrorCode.CONTRACT_VIOLATION, "original input missing or corrupt")
        # Permissions and the authoritative binding may change while the P2 call is running.
        with self.runs.uow.transaction() as tx:
            _, _, _, current = self.load(tx, ctx, run_id, operation_id)
            if current != stored:
                raise FoundationError(
                    ErrorCode.VERSION_CONFLICT, "caller input changed during read"
                )
        return payload
