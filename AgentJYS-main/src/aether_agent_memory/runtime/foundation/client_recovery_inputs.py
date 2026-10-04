"""Confirm existing original input bytes without releasing the recovery hold."""

from uuid import UUID

from aether_agent_memory.runtime.contracts.client_inputs import ClientInputReceipt
from aether_agent_memory.runtime.contracts.client_recovery_reads import ReadClientRecovery
from aether_agent_memory.runtime.contracts.models import ErrorCode, TrustedContext

from .client_inputs import StoredClientInput
from .client_recovery_reads import ClientRecoveryReads
from .common import FoundationError


class ClientRecoveryInputs:
    def __init__(self, reads: ClientRecoveryReads):
        self.reads = reads

    def confirm(
        self,
        ctx: TrustedContext,
        run_id: UUID,
        transfer_id: str,
        operation_id: str,
        expected: ReadClientRecovery,
    ) -> ClientInputReceipt:
        with self.reads.runs.uow.transaction() as tx:
            run = self.reads.held(tx, ctx, run_id, transfer_id, expected)
            ref, stored = self.reads.reservation(tx, ctx, run, "input", operation_id, None, None)
            assert isinstance(stored, StoredClientInput)
        # The recovery reader validates the original hash/size and rechecks authority
        # after object IO. Confirmation never replaces or uploads input bytes.
        self.reads.read(ctx, run_id, transfer_id, expected, "input", operation_id=operation_id)
        with self.reads.runs.uow.transaction() as tx:
            run = self.reads.held(tx, ctx, run_id, transfer_id, expected)
            _, current = self.reads.reservation(tx, ctx, run, "input", operation_id, None, None)
            if current != stored:
                raise FoundationError(
                    ErrorCode.VERSION_CONFLICT, "original input changed during confirmation"
                )
            if stored.state == "pending":
                ready = stored.model_copy(
                    update={"state": "ready", "revision": stored.revision + 1}
                )
                tx.put_if_revision(ref, ready.model_dump(mode="json"), stored.revision)
            else:
                ready = stored
            return ready.receipt()
