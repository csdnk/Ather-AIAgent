"""Bindings for exact caller request bytes, separate from Temporal command inputs."""

from typing import Literal
from uuid import UUID

from pydantic import Field

from .models import ContractModel, Digest, Identifier


class ClientInputBinding(ContractModel):
    run_id: UUID
    operation_id: Identifier
    request_hash: Digest
    binding_digest: Digest
    size_bytes: int = Field(ge=0)


class ClientInputReceipt(ClientInputBinding):
    state: Literal["ready"] = "ready"
