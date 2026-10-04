"""Caller-owned progress records; business effects remain P3 operations."""

import re
from hashlib import sha256
from typing import Literal, Self
from urllib.parse import parse_qsl, urlsplit
from uuid import UUID

import rfc8785
from pydantic import Field, JsonValue, field_validator, model_validator

from .client_definitions import ClientDefinitionBinding
from .client_ownership import ClientOwnership
from .client_state_binding import ClientStateBinding
from .models import ContractModel, Digest, Identifier, Positive, Timestamp

RunState = Literal["queued", "running", "passed", "failed", "blocked", "unconfirmed"]


class HttpRequestBinding(ContractModel):
    version: Literal[1] = 1
    target: str = Field(min_length=1, max_length=4096, pattern=r"^[!-~]+$")
    content_type: str = Field(max_length=128, pattern=r"^[ -~]*$")
    digest: Digest


def http_intent_digest(
    method: str, path: str, operation_id: str, body_hash: str, target: str, content_type: str
) -> str:
    return sha256(
        rfc8785.dumps(
            {
                "version": 1,
                "method": method,
                "route": path,
                "operation_id": operation_id,
                "body_hash": body_hash,
                "target": target,
                "content_type": content_type,
            }
        )
    ).hexdigest()


class ClientOperation(ContractModel):
    method: Literal["POST", "PUT"]
    path: str
    operation_id: Identifier
    request_hash: Digest
    binding: HttpRequestBinding | None = None  # Old records remain readable, never backfilled.
    phase: Literal["prepared", "observed"]
    status_code: int | None = Field(default=None, ge=100, le=599)
    job_id: Identifier | None = None

    @model_validator(mode="after")
    def consistent_receipt(self) -> Self:
        if self.phase == "prepared" and (self.status_code is not None or self.job_id is not None):
            raise ValueError("prepared operations cannot contain a response receipt")
        if self.phase == "observed" and self.status_code is None:
            raise ValueError("observed operations require an HTTP response status")
        if self.binding is not None:
            parsed = urlsplit(self.binding.target)
            if (
                parsed.scheme
                or parsed.netloc
                or "#" in self.binding.target
                or not self.path.startswith("/p3/")
            ):
                raise ValueError("operation target must be a relative P3 request target")
            parts = re.split(r"(\{[a-z_]+\})", self.path)
            template = "".join(
                "[A-Za-z0-9_-]+" if part.startswith("{") else re.escape(part) for part in parts
            )
            # A configured gateway prefix is part of the concrete target too.
            if not re.fullmatch(r"(?:/[A-Za-z0-9._~-]+)*" + template, parsed.path):
                raise ValueError("operation target differs from its route template")
            if any(
                key not in {"version", "start", "end"}
                for key, _ in parse_qsl(parsed.query, keep_blank_values=True, max_num_fields=8)
            ):
                raise ValueError("operation query contains unsupported or credential parameters")
            expected = http_intent_digest(
                self.method,
                self.path,
                self.operation_id,
                self.request_hash,
                self.binding.target,
                self.binding.content_type,
            )
            if expected != self.binding.digest:
                raise ValueError("operation request binding digest changed")
        return self

    @classmethod
    def prepare(
        cls,
        *,
        method: Literal["POST", "PUT"],
        path: str,
        operation_id: str,
        request_hash: str,
        target: str,
        content_type: str,
    ) -> Self:
        return cls(
            method=method,
            path=path,
            operation_id=operation_id,
            request_hash=request_hash,
            phase="prepared",
            binding=HttpRequestBinding(
                target=target,
                content_type=content_type,
                digest=http_intent_digest(
                    method, path, operation_id, request_hash, target, content_type
                ),
            ),
        )

    def same_request(self, other: "ClientOperation") -> bool:
        fields = {"phase", "status_code", "job_id"}
        return self.model_dump(exclude=fields) == other.model_dump(exclude=fields)


def bounded_snapshot(value: dict[str, JsonValue]) -> dict[str, JsonValue]:
    import json

    if len(json.dumps(value, ensure_ascii=False).encode()) > 262144:
        raise ValueError("caller snapshot exceeds 256 KiB")

    def inspect(item: JsonValue, depth: int = 0) -> None:
        if depth > 24:
            raise ValueError("caller snapshot nesting exceeds limit")
        if isinstance(item, dict):
            if any(
                key.lower() in {"credential", "authorization", "cookie", "headers", "token"}
                for key in item
            ):
                raise ValueError("credentials and HTTP headers cannot enter caller snapshots")
            for child in item.values():
                inspect(child, depth + 1)
        elif isinstance(item, list):
            for child in item:
                inspect(child, depth + 1)

    inspect(value)
    operations = value.get("operations", [])
    if not isinstance(operations, list) or len(operations) > 256:
        raise ValueError("caller operation journal must contain at most 256 operations")
    parsed = [ClientOperation.model_validate(item) for item in operations]
    if len({item.operation_id for item in parsed}) != len(parsed):
        raise ValueError("caller operation IDs must be unique")
    if value.get("state") not in {
        "queued",
        "running",
        "passed",
        "failed",
        "blocked",
        "unconfirmed",
    }:
        raise ValueError("invalid caller run state")
    return value


class RegisterClientRun(ContractModel):
    scenario_id: Identifier
    owner_id: Identifier
    snapshot: dict[str, JsonValue]
    definition: ClientDefinitionBinding | None = None
    scope_policy: Literal["p4_task_v1"] | None = None
    state_policy: Literal["p4_state_v1"] | None = None

    _snapshot = field_validator("snapshot")(bounded_snapshot)

    @model_validator(mode="after")
    def recovery_policy(self) -> Self:
        if self.state_policy and (self.scope_policy is None or self.definition is None):
            raise ValueError("execution state requires managed scope and original definition")
        return self


class CheckpointClientRun(ContractModel):
    owner_id: Identifier
    expected_revision: Positive
    snapshot: dict[str, JsonValue]

    _snapshot = field_validator("snapshot")(bounded_snapshot)


class ClientRunRecord(ContractModel):
    run_id: UUID
    scenario_id: Identifier
    scope_id: Identifier
    owner_id: Identifier
    auth_epoch: Positive
    revision: Positive
    updated_at: Timestamp
    snapshot: dict[str, JsonValue]
    definition: ClientDefinitionBinding | None = None
    scope_policy: Literal["p4_task_v1"] | None = None
    state_policy: Literal["p4_state_v1"] | None = None
    execution_state: ClientStateBinding | None = None
    ownership: ClientOwnership | None = None

    @property
    def recovery_held(self) -> bool:
        return self.ownership is not None and self.ownership.recovery_transfer_id is not None

    @property
    def state_stream_id(self) -> str | None:
        return self.ownership.epochs[-1].transfer_id if self.ownership is not None else None

    @model_validator(mode="after")
    def recovery_policy(self) -> Self:
        if self.ownership is not None and (
            self.state_policy is None
            or self.ownership.epochs[-1].owner_id != self.owner_id
            or self.ownership.epochs[-1].first_revision > self.revision
        ):
            raise ValueError("current owner differs from recorded ownership history")
        if self.state_policy and (self.scope_policy is None or self.definition is None):
            raise ValueError("execution state requires managed scope and original definition")
        if self.execution_state is not None and (
            self.state_policy is None
            or self.definition is None
            or self.execution_state.definition_hash != self.definition.content_hash
        ):
            raise ValueError("execution state differs from its original definition policy")
        return self


class ClientRunRegistration(ContractModel):
    created: bool
    record: ClientRunRecord
