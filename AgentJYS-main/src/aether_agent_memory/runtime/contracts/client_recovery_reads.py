"""Evidence for read-only recovery bytes; pending never means published."""

from dataclasses import dataclass
from typing import Literal, Self
from uuid import UUID

from pydantic import model_validator

from .client_definitions import ClientDefinitionBinding
from .client_inputs import ClientInputBinding
from .client_runs import ClientRunRecord
from .client_state_binding import ClientStateBinding
from .client_transfers import client_run_hash
from .models import ContractModel, Digest, Identifier, Positive

RecoveryObjectKind = Literal["definition", "input", "state"]


class ReadClientRecovery(ContractModel):
    expected_owner_id: Identifier
    expected_revision: Positive
    expected_record_hash: Digest

    def require_original(self, record: ClientRunRecord, transfer_id: str) -> None:
        if (
            record.owner_id != self.expected_owner_id
            or record.revision != self.expected_revision
            or client_run_hash(record) != self.expected_record_hash
            or record.ownership is None
            or record.ownership.recovery_transfer_id != transfer_id
        ):
            raise ValueError("recovery read differs from the original held record")


class ClientRecoveryObjectEvidence(ContractModel):
    run_id: UUID
    transfer_id: Identifier
    record_hash: Digest
    kind: RecoveryObjectKind
    state: Literal["pending", "ready"]
    object_revision: Positive
    binding: ClientDefinitionBinding | ClientInputBinding | ClientStateBinding

    @model_validator(mode="after")
    def binding_kind(self) -> Self:
        expected = {
            "definition": ClientDefinitionBinding,
            "input": ClientInputBinding,
            "state": ClientStateBinding,
        }[self.kind]
        if not isinstance(self.binding, expected):
            raise ValueError("recovery object kind differs from its binding")
        if isinstance(self.binding, ClientInputBinding) and self.binding.run_id != self.run_id:
            raise ValueError("recovery input belongs to another run")
        return self


@dataclass(frozen=True)
class RecoveredClientObject:
    evidence: ClientRecoveryObjectEvidence
    payload: bytes
