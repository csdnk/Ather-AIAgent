"""Confirm the original definition before releasing an initialization recovery hold."""

from hashlib import sha256
from uuid import UUID

from aether_agent_memory.runtime.contracts.client_definitions import ClientDefinitionReceipt
from aether_agent_memory.runtime.contracts.client_recovery_reads import ReadClientRecovery
from aether_agent_memory.runtime.contracts.client_runs import ClientRunRecord
from aether_agent_memory.runtime.contracts.models import ErrorCode, TrustedContext
from aether_agent_memory.runtime.contracts.ports import Transaction

from .client_definitions import StoredDefinition
from .client_recovery_reads import ClientRecoveryReads
from .common import FoundationError


class ClientRecoveryInitialization:
    def __init__(self, reads: ClientRecoveryReads):
        self.reads = reads
        self.definitions, self.runs = reads.definitions, reads.runs

    def original(
        self,
        tx: Transaction,
        ctx: TrustedContext,
        run_id: UUID,
        transfer_id: str,
        expected: ReadClientRecovery,
    ) -> tuple[ClientRunRecord, StoredDefinition | None]:
        run = self.reads.held(tx, ctx, run_id, transfer_id, expected)
        if run.snapshot.get("operations") or run.execution_state is not None:
            raise FoundationError(ErrorCode.VERSION_CONFLICT, "initialization already started")
        try:
            stored = self.definitions.metadata(tx, ctx, run)
        except ValueError:
            raise FoundationError(
                ErrorCode.CONTRACT_VIOLATION, "original definition metadata corrupt"
            ) from None
        if run.snapshot["state"] != "queued" and (stored is None or stored.state != "ready"):
            raise FoundationError(
                ErrorCode.CONTRACT_VIOLATION, "started run lacks confirmed original definition"
            )
        return run, stored

    def confirm(
        self,
        ctx: TrustedContext,
        run_id: UUID,
        transfer_id: str,
        expected: ReadClientRecovery,
        payload: bytes | None,
    ) -> ClientDefinitionReceipt:
        if payload is not None and (not payload or len(payload) > self.definitions.max_bytes):
            raise FoundationError(
                ErrorCode.INVALID_ARGUMENT, "caller definition exceeds ingress limit"
            )
        with self.runs.uow.transaction() as tx:
            run, stored = self.original(tx, ctx, run_id, transfer_id, expected)
            binding = run.definition
            assert binding is not None
            if payload is not None and (
                len(payload) != binding.size_bytes
                or sha256(payload).hexdigest() != binding.content_hash
            ):
                raise FoundationError(
                    ErrorCode.IDEMPOTENCY_CONFLICT, "original definition bytes changed"
                )
            objects = self.definitions.objects
            if objects is None:
                raise FoundationError(
                    ErrorCode.DEPENDENCY_UNAVAILABLE, "P2 definition objects required"
                )
            ref = self.definitions.ref(ctx, run_id)
            if stored is None:
                if payload is None:
                    raise FoundationError(
                        ErrorCode.COMMIT_UNCONFIRMED, "original definition bytes required"
                    )
                stored = StoredDefinition(
                    run_id=run_id,
                    binding=binding,
                    writer_id=run.owner_id,
                    state="pending",
                    revision=1,
                )
                tx.put_if_revision(ref, stored.model_dump(mode="json"), None)

        def unchanged(tx: Transaction) -> None:
            _, current = self.original(tx, ctx, run_id, transfer_id, expected)
            if current != stored:
                raise FoundationError(
                    ErrorCode.VERSION_CONFLICT, "definition changed during recovery IO"
                )

        key = self.definitions.key(ref, binding.content_hash)
        try:
            original = objects.get_object_sync(key)
            with self.runs.uow.transaction() as tx:
                unchanged(tx)
            if original is None:
                if stored.state == "ready":
                    raise FoundationError(
                        ErrorCode.CONTRACT_VIOLATION, "confirmed definition bytes missing"
                    )
                if payload is None:
                    raise FoundationError(
                        ErrorCode.COMMIT_UNCONFIRMED, "original pending bytes unavailable"
                    )
                objects.put_object_sync(key, payload)
                original = objects.get_object_sync(key)
            with self.runs.uow.transaction() as tx:
                unchanged(tx)
                if original is None:
                    raise FoundationError(
                        ErrorCode.COMMIT_UNCONFIRMED, "P2 definition not confirmed"
                    )
                if (
                    len(original) != binding.size_bytes
                    or len(original) > self.definitions.max_bytes
                    or sha256(original).hexdigest() != binding.content_hash
                ):
                    raise FoundationError(
                        ErrorCode.CONTRACT_VIOLATION, "original definition bytes corrupt"
                    )
                if stored.state == "pending":
                    ready = stored.model_copy(
                        update={"state": "ready", "revision": stored.revision + 1}
                    )
                    tx.put_if_revision(ref, ready.model_dump(mode="json"), stored.revision)
                else:
                    ready = stored
                return ready.receipt()
        except FoundationError:
            raise
        except Exception:
            raise FoundationError(
                ErrorCode.DEPENDENCY_UNAVAILABLE, "P2 definition confirmation unavailable"
            ) from None
