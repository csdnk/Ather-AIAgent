"""Commit evidence for synchronous mutations; task completion is separate."""

from hashlib import sha256
from typing import Literal, Self

import rfc8785
from pydantic import Field, JsonValue, model_validator

from .http_evidence import HttpRequestEvidence
from .models import ContractModel, Digest, Identifier, RecordRef, Timestamp

MutationKind = Literal[
    "remember.consolidate",
    "remember.distill",
    "remember.reprocess",
    "remember.reindex",
    "remember.lifecycle",
    "remember.retention",
    "remember.reflection",
    "remember.delete",
    "source.delete",
    "source.revoke",
]


class MutationReceipt(ContractModel):
    operation_id: Identifier
    kind: MutationKind
    targets: tuple[RecordRef, ...] = Field(min_length=1)
    intent_hash: Digest
    result_hash: Digest
    result_basis: Literal["response", "metadata_without_content"] = "response"
    task_ids: tuple[Identifier, ...] = ()
    committed_at: Timestamp
    http_request: HttpRequestEvidence | None = None

    @model_validator(mode="after")
    def result_representation(self) -> Self:
        expected = "metadata_without_content" if self.kind == "remember.lifecycle" else "response"
        if self.result_basis != expected:
            raise ValueError("mutation result hash basis does not match its kind")
        if self.http_request is not None and not self.http_request.matches_kind(self.kind):
            raise ValueError("HTTP evidence differs from its original mutation")
        return self


class MutationLookup(ContractModel):
    operation_id: Identifier
    kind: MutationKind
    state: Literal["committed", "unconfirmed"]
    receipt: MutationReceipt | None = None

    @model_validator(mode="after")
    def coherent(self) -> Self:
        if (self.state == "committed") != (self.receipt is not None):
            raise ValueError("only committed mutations have receipts")
        if self.receipt and (
            self.receipt.operation_id != self.operation_id or self.receipt.kind != self.kind
        ):
            raise ValueError("mutation receipt binding changed")
        return self


class MutationResult(ContractModel):
    """The exact committed representation, with its original hash basis."""

    receipt: MutationReceipt
    response: dict[str, JsonValue]

    @model_validator(mode="after")
    def original_response(self) -> Self:
        if sha256(rfc8785.dumps(self.response)).hexdigest() != self.receipt.result_hash:
            raise ValueError("original mutation result hash differs")
        if self.receipt.result_basis == "metadata_without_content" and "content" in self.response:
            raise ValueError("lifecycle metadata result cannot contain a body")
        return self
