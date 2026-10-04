"""Run definition storage uses caller transactions and existing P2 object IO."""

from hashlib import sha256
from typing import Literal
from uuid import UUID

from aether_agent_memory.runtime.contracts.client_definitions import (
    ClientDefinitionBinding,
    ClientDefinitionReceipt,
)
from aether_agent_memory.runtime.contracts.client_runs import ClientRunRecord
from aether_agent_memory.runtime.contracts.models import (
    ContractModel,
    ErrorCode,
    Identifier,
    Positive,
    RecordRef,
    TrustedContext,
)
from aether_agent_memory.runtime.contracts.ports import Transaction

from .client_inputs import InputObjects
from .client_runs import ClientRuns
from .common import FoundationError, fingerprint


class StoredDefinition(ContractModel):
    run_id: UUID
    binding: ClientDefinitionBinding
    writer_id: Identifier
    state: Literal["pending", "ready"]
    revision: Positive

    def receipt(self) -> ClientDefinitionReceipt:
        if self.state != "ready":
            raise FoundationError(ErrorCode.COMMIT_UNCONFIRMED, "caller definition not confirmed")
        return ClientDefinitionReceipt(run_id=self.run_id, **self.binding.model_dump())


class ClientDefinitions:
    def __init__(self, runs: ClientRuns, objects: InputObjects | None, max_bytes: int):
        self.runs, self.objects, self.max_bytes = runs, objects, max_bytes

    @staticmethod
    def ref(ctx: TrustedContext, run_id: UUID) -> RecordRef:
        return ClientRuns.ref(ctx, run_id).model_copy(
            update={"object_type": "client_run_definition"}
        )

    @staticmethod
    def key(ref: RecordRef, content_hash: str) -> str:
        return (
            "runtime/client-definitions/"
            + fingerprint(ref.model_dump(mode="json"))
            + "/"
            + content_hash
        )

    @classmethod
    def metadata(
        cls, tx: Transaction, ctx: TrustedContext, run: ClientRunRecord
    ) -> StoredDefinition | None:
        if run.definition is None:
            raise FoundationError(ErrorCode.VERSION_CONFLICT, "original definition binding missing")
        raw = tx.get(cls.ref(ctx, run.run_id))
        stored = StoredDefinition.model_validate(raw) if raw is not None else None
        if stored is not None and (stored.run_id != run.run_id or stored.binding != run.definition):
            raise FoundationError(
                ErrorCode.CONTRACT_VIOLATION, "original definition binding changed"
            )
        return stored

    @classmethod
    def require_ready(cls, tx: Transaction, ctx: TrustedContext, run: ClientRunRecord) -> None:
        stored = cls.metadata(tx, ctx, run)
        if stored is None:
            raise FoundationError(ErrorCode.COMMIT_UNCONFIRMED, "caller definition not stored")
        stored.receipt()

    def writable(
        self, tx: Transaction, ctx: TrustedContext, run: ClientRunRecord, owner: str, revision: int
    ) -> None:
        index = tx.get(self.runs.ref(ctx, None))
        if (
            run.owner_id != owner
            or run.recovery_held
            or run.revision != revision
            or run.snapshot["state"] != "queued"
            or run.snapshot.get("operations")
            or index is None
            or index["active"] != str(run.run_id)
        ):
            raise FoundationError(
                ErrorCode.VERSION_CONFLICT, "definition writer changed or started"
            )

    def prepare(
        self, ctx: TrustedContext, run_id: UUID, owner: str, revision: int, payload: bytes
    ) -> ClientDefinitionReceipt:
        if not payload or len(payload) > self.max_bytes:
            raise FoundationError(
                ErrorCode.INVALID_ARGUMENT, "caller definition exceeds ingress limit"
            )
        with self.runs.uow.transaction() as tx:
            run = self.runs._read(tx, ctx, run_id)
            stored = self.metadata(tx, ctx, run)
            assert run.definition is not None
            if run.owner_id != owner:
                raise FoundationError(ErrorCode.VERSION_CONFLICT, "definition owner changed")
            if (
                len(payload) != run.definition.size_bytes
                or sha256(payload).hexdigest() != run.definition.content_hash
            ):
                raise FoundationError(ErrorCode.IDEMPOTENCY_CONFLICT, "definition bytes changed")
            ready = stored is not None and stored.state == "ready"
            if not ready:
                self.writable(tx, ctx, run, owner, revision)
            if self.objects is None:
                raise FoundationError(
                    ErrorCode.DEPENDENCY_UNAVAILABLE, "P2 definition objects required"
                )
            ref = self.ref(ctx, run_id)
            if stored is None:
                stored = StoredDefinition(
                    run_id=run_id,
                    binding=run.definition,
                    writer_id=owner,
                    state="pending",
                    revision=1,
                )
                tx.put_if_revision(ref, stored.model_dump(mode="json"), None)
        if ready:
            self.read(ctx, run_id)
        else:
            key = self.key(ref, stored.binding.content_hash)
            try:
                previous = self.objects.get_object_sync(key)
                if previous is None:
                    self.objects.put_object_sync(key, payload)
                elif previous != payload:
                    raise FoundationError(
                        ErrorCode.CONTRACT_VIOLATION, "original definition changed"
                    )
                if self.objects.get_object_sync(key) != payload:
                    raise FoundationError(
                        ErrorCode.COMMIT_UNCONFIRMED, "P2 definition not confirmed"
                    )
            except FoundationError:
                raise
            except Exception:
                raise FoundationError(
                    ErrorCode.DEPENDENCY_UNAVAILABLE, "P2 definition write unavailable"
                ) from None
        with self.runs.uow.transaction() as tx:
            run = self.runs._read(tx, ctx, run_id)
            current = self.metadata(tx, ctx, run)
            if current is None or run.owner_id != owner:
                raise FoundationError(
                    ErrorCode.VERSION_CONFLICT, "definition owner changed during IO"
                )
            if ready:
                if current != stored:
                    raise FoundationError(
                        ErrorCode.VERSION_CONFLICT, "definition changed during read"
                    )
            else:
                self.writable(tx, ctx, run, owner, revision)
                if current.state == "pending":
                    current = current.model_copy(
                        update={"state": "ready", "revision": current.revision + 1}
                    )
                    tx.put_if_revision(ref, current.model_dump(mode="json"), current.revision - 1)
            return current.receipt()

    def read(self, ctx: TrustedContext, run_id: UUID) -> bytes:
        with self.runs.uow.transaction() as tx:
            run = self.runs._read(tx, ctx, run_id)
            stored = self.metadata(tx, ctx, run)
            if stored is None:
                raise FoundationError(ErrorCode.NOT_FOUND, "caller definition not stored")
            stored.receipt()
        if self.objects is None:
            raise FoundationError(
                ErrorCode.DEPENDENCY_UNAVAILABLE, "P2 definition objects required"
            )
        try:
            payload = self.objects.get_object_sync(
                self.key(self.ref(ctx, run_id), stored.binding.content_hash)
            )
        except FoundationError:
            raise
        except Exception:
            raise FoundationError(
                ErrorCode.DEPENDENCY_UNAVAILABLE, "P2 definition read unavailable"
            ) from None
        if (
            payload is None
            or len(payload) != stored.binding.size_bytes
            or len(payload) > self.max_bytes
            or sha256(payload).hexdigest() != stored.binding.content_hash
        ):
            raise FoundationError(
                ErrorCode.CONTRACT_VIOLATION, "original definition missing or corrupt"
            )
        with self.runs.uow.transaction() as tx:
            run = self.runs._read(tx, ctx, run_id)
            if self.metadata(tx, ctx, run) != stored:
                raise FoundationError(ErrorCode.VERSION_CONFLICT, "definition changed during read")
        return payload
