"""Immutable caller definition metadata; payloads remain in P2 objects."""

from typing import Literal
from uuid import UUID

from .models import ContractModel, Digest, Identifier, Positive


class ClientDefinitionBinding(ContractModel):
    format_id: Identifier
    content_hash: Digest
    size_bytes: Positive


class ClientDefinitionReceipt(ClientDefinitionBinding):
    run_id: UUID
    state: Literal["ready"] = "ready"
