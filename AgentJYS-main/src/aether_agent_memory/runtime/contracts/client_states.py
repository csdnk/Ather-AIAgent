"""Versioned caller state envelope bound to an original operation journal."""

import json
from hashlib import sha256
from typing import Literal, Self
from uuid import UUID

import rfc8785
from pydantic import Field, JsonValue, field_validator, model_validator

from .client_runs import ClientOperation
from .client_state_binding import ClientStateBinding, StateSequence
from .models import ContractModel, Digest, Identifier


def operations_hash(operations: list[ClientOperation]) -> str:
    return sha256(rfc8785.dumps([item.model_dump(mode="json") for item in operations])).hexdigest()


class ClientExecutionState(ContractModel):
    format_id: Literal["p4_state_v1"]
    run_id: UUID
    scenario_id: Identifier
    scope_id: Identifier
    definition_hash: Digest
    sequence: StateSequence
    parent_hash: Digest | None
    operations: list[ClientOperation] = Field(max_length=256)
    data: dict[str, JsonValue]
    stream_id: Identifier | None = Field(default=None, exclude_if=lambda value: value is None)
    parent_stream_id: Identifier | None = Field(
        default=None, exclude_if=lambda value: value is None
    )

    @field_validator("data")
    @classmethod
    def bounded_data(cls, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        def inspect(item: JsonValue, depth: int = 0) -> None:
            if depth > 24:
                raise ValueError("execution state nesting exceeds limit")
            if isinstance(item, dict):
                if any(
                    key.lower() in {"credential", "authorization", "cookie", "headers", "token"}
                    for key in item
                ):
                    raise ValueError("credentials cannot enter execution states")
                for child in item.values():
                    inspect(child, depth + 1)
            elif isinstance(item, list):
                for child in item:
                    inspect(child, depth + 1)

        inspect(value)
        return value

    @model_validator(mode="after")
    def consistent(self) -> Self:
        if (self.sequence == 1) != (self.parent_hash is None):
            raise ValueError("only the first execution state has no parent")
        if self.sequence == 1 and self.parent_stream_id is not None:
            raise ValueError("the first execution state has no parent stream")
        if len({item.operation_id for item in self.operations}) != len(self.operations):
            raise ValueError("execution-state operation IDs must be unique")
        return self

    @classmethod
    def from_bytes(cls, payload: bytes) -> Self:
        def unique(pairs: list[tuple[str, JsonValue]]) -> dict[str, JsonValue]:
            value = dict(pairs)
            if len(value) != len(pairs):
                raise ValueError("execution state has duplicate JSON keys")
            return value

        return cls.model_validate(json.loads(payload, object_pairs_hook=unique))

    def binding(self, payload: bytes) -> ClientStateBinding:
        return ClientStateBinding(
            sequence=self.sequence,
            parent_hash=self.parent_hash,
            content_hash=sha256(payload).hexdigest(),
            size_bytes=len(payload),
            definition_hash=self.definition_hash,
            operations_hash=operations_hash(self.operations),
            stream_id=self.stream_id,
            parent_stream_id=self.parent_stream_id,
        )
