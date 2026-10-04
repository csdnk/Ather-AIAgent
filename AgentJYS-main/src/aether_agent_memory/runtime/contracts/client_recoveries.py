"""Explicit activation intent and immutable receipt; state bytes remain in P2."""

from typing import Literal, Self
from uuid import UUID

import rfc8785
from pydantic import model_validator

from .client_runs import ClientOperation, ClientRunRecord
from .client_state_binding import ClientStateBinding
from .client_states import ClientExecutionState, operations_hash
from .client_transfers import client_run_hash
from .models import ContractModel, Digest, Identifier, Positive


class RecoveryExpectation(ContractModel):
    expected_owner_id: Identifier
    expected_revision: Positive
    expected_record_hash: Digest
    target_state: Literal["queued", "running"]


class ActivateClientRun(RecoveryExpectation):
    state: ClientStateBinding | None = None

    @model_validator(mode="after")
    def target(self) -> Self:
        if (self.target_state == "running") != (self.state is not None):
            raise ValueError("running recovery requires a state; queued recovery has none")
        return self

    def require_original(self, before: ClientRunRecord, transfer_id: str) -> None:
        if (
            before.owner_id != self.expected_owner_id
            or before.revision != self.expected_revision
            or client_run_hash(before) != self.expected_record_hash
            or before.ownership is None
            or before.ownership.recovery_transfer_id != transfer_id
            or before.snapshot["state"] not in {"queued", "running", "unconfirmed"}
        ):
            raise ValueError("recovery differs from the original held record")
        head = before.execution_state
        if self.target_state == "queued":
            if before.snapshot["state"] != "queued" or before.snapshot.get("operations") or head:
                raise ValueError("only an unstarted run can recover initialization")
            return
        binding = self.state
        assert binding is not None
        raw = before.snapshot.get("operations", [])
        assert isinstance(raw, list)
        if (
            before.definition is None
            or binding.definition_hash != before.definition.content_hash
            or binding.stream_id != transfer_id
            or binding.sequence != (1 if head is None else head.sequence + 1)
            or binding.parent_hash != (None if head is None else head.content_hash)
            or binding.parent_stream_id != (None if head is None else head.stream_id)
            or binding.operations_hash
            != operations_hash([ClientOperation.model_validate(x) for x in raw])
        ):
            raise ValueError("recovery state differs from original parent or journal")


class PrepareClientRecovery(RecoveryExpectation):
    execution_state: ClientExecutionState | None = None

    def payload(self) -> bytes | None:
        return (
            None
            if self.execution_state is None
            else rfc8785.dumps(self.execution_state.model_dump(mode="json"))
        )

    def intent(self) -> ActivateClientRun:
        payload = self.payload()
        return ActivateClientRun(
            **self.model_dump(include=set(RecoveryExpectation.model_fields)),
            state=None
            if self.execution_state is None or payload is None
            else self.execution_state.binding(payload),
        )


class ClientRunRecoveryResult(ContractModel):
    run_id: UUID
    transfer_id: Identifier
    request: ActivateClientRun
    previous: ClientRunRecord
    record: ClientRunRecord

    @model_validator(mode="after")
    def exact_activation(self) -> Self:
        before, after = self.previous, self.record
        self.request.require_original(before, self.transfer_id)
        if before.run_id != self.run_id:
            raise ValueError("recovery run identity differs")
        assert before.ownership is not None
        expected = before.model_copy(
            update={
                "revision": before.revision + 1,
                "updated_at": after.updated_at,
                "snapshot": {**before.snapshot, "state": self.request.target_state},
                "ownership": before.ownership.model_copy(update={"recovery_transfer_id": None}),
                "execution_state": self.request.state,
            }
        )
        if after != expected:
            raise ValueError("recovery changed original evidence or ownership")
        return self


class ClientRunRecoveryLookup(ContractModel):
    run_id: UUID
    transfer_id: Identifier
    state: Literal["unconfirmed", "committed"]
    result: ClientRunRecoveryResult | None = None

    @model_validator(mode="after")
    def coherent(self) -> Self:
        if (self.state == "committed") != (self.result is not None):
            raise ValueError("only committed activation has a result")
        if self.result and (
            self.result.run_id != self.run_id or self.result.transfer_id != self.transfer_id
        ):
            raise ValueError("recovery lookup identity differs")
        return self
