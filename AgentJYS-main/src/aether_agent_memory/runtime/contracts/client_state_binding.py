"""Immutable execution-state head metadata; the payload lives in P2."""

from typing import Annotated, Literal, Self

from pydantic import Field, StrictInt, model_validator

from .models import ContractModel, Digest, Identifier

StateSequence = Annotated[StrictInt, Field(ge=1, le=1024)]
STATE_MAX_BYTES = 262144


class ClientStateBinding(ContractModel):
    format_id: Literal["p4_state_v1"] = "p4_state_v1"
    sequence: StateSequence
    parent_hash: Digest | None
    content_hash: Digest
    size_bytes: int = Field(ge=1, le=STATE_MAX_BYTES)
    definition_hash: Digest
    operations_hash: Digest
    stream_id: Identifier | None = Field(default=None, exclude_if=lambda value: value is None)
    parent_stream_id: Identifier | None = Field(
        default=None, exclude_if=lambda value: value is None
    )

    @model_validator(mode="after")
    def parent(self) -> Self:
        if (self.sequence == 1) != (self.parent_hash is None):
            raise ValueError("only the first execution state has no parent")
        if self.sequence == 1 and self.parent_stream_id is not None:
            raise ValueError("the first execution state has no parent stream")
        return self
