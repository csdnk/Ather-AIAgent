"""Immutable transfer intent/result; a held record does not authorize business admission."""

from hashlib import sha256
from typing import Literal, Self
from uuid import UUID

import rfc8785
from pydantic import model_validator

from .client_ownership import ClientOwnerEpoch, ClientOwnership
from .client_runs import ClientRunRecord
from .models import ContractModel, Digest, Identifier, Positive


def client_run_hash(record: ClientRunRecord) -> str:
    return sha256(rfc8785.dumps(record.model_dump(mode="json"))).hexdigest()


class TransferClientRun(ContractModel):
    expected_owner_id: Identifier
    expected_revision: Positive
    expected_record_hash: Digest
    new_owner_id: Identifier


class ClientRunTransferResult(ContractModel):
    run_id: UUID
    transfer_id: Identifier
    request: TransferClientRun
    previous: ClientRunRecord
    record: ClientRunRecord

    @model_validator(mode="after")
    def exact_transfer(self) -> Self:
        before, after, spec = self.previous, self.record, self.request
        if (
            before.ownership is None
            or before.run_id != self.run_id
            or spec.expected_owner_id != before.owner_id
            or spec.expected_revision != before.revision
            or spec.expected_record_hash != client_run_hash(before)
            or spec.new_owner_id == before.owner_id
        ):
            raise ValueError("transfer differs from original owner and record")
        ownership = ClientOwnership(
            epochs=(
                *before.ownership.epochs,
                ClientOwnerEpoch(
                    owner_id=spec.new_owner_id,
                    first_revision=before.revision + 1,
                    transfer_id=self.transfer_id,
                ),
            ),
            recovery_transfer_id=self.transfer_id,
        )
        expected = before.model_copy(
            update={
                "owner_id": spec.new_owner_id,
                "revision": before.revision + 1,
                "updated_at": after.updated_at,
                "ownership": ownership,
            }
        )
        if after != expected:
            raise ValueError("transfer changed original snapshot, state or run binding")
        return self


class ClientRunTransferLookup(ContractModel):
    run_id: UUID
    transfer_id: Identifier
    state: Literal["unconfirmed", "committed"]
    result: ClientRunTransferResult | None = None

    @model_validator(mode="after")
    def coherent(self) -> Self:
        if (self.state == "committed") != (self.result is not None):
            raise ValueError("only committed transfers have an original result")
        if self.result and (
            self.result.run_id != self.run_id or self.result.transfer_id != self.transfer_id
        ):
            raise ValueError("transfer lookup identity differs")
        return self
