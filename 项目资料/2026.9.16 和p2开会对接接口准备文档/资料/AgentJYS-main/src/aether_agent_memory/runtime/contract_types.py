"""Strict value types shared by the new Recall and semantic compute contracts.

These types do not reinterpret the legacy core.Scope or Northbound v1 DTOs.
Nullable contract fields are required unless explicitly documented otherwise.
"""

from __future__ import annotations

import hashlib
import math
import struct
from datetime import UTC, datetime
from typing import Annotated, Literal, Self, cast

import rfc8785
from pydantic import (
    AfterValidator,
    AwareDatetime,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    JsonValue,
    PlainSerializer,
    model_validator,
)


def valid_text(value: str) -> str:
    value.encode("utf-8", errors="strict")
    return value


def identifier(value: str) -> str:
    if not valid_text(value).strip():
        raise ValueError("identifier must not be blank")
    return value


def finite_number(value: object) -> object:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("expected a finite number, not a boolean or numeric string")
    if not math.isfinite(value):
        raise ValueError("number must be finite")
    return value


def timestamp(value: datetime) -> datetime:
    value = value.astimezone(UTC)
    return value.replace(microsecond=value.microsecond // 1000 * 1000)


def utcnow() -> datetime:
    return timestamp(datetime.now(UTC))


Text = Annotated[str, Field(strict=True), AfterValidator(valid_text)]
Identifier = Annotated[str, Field(strict=True), AfterValidator(identifier)]
UInt = Annotated[int, Field(strict=True, ge=0, le=9007199254740991)]
PositiveInt = Annotated[int, Field(strict=True, gt=0, le=9007199254740991)]
Number = Annotated[float, BeforeValidator(finite_number)]
Boolean = Annotated[bool, Field(strict=True)]
Timestamp = Annotated[
    AwareDatetime,
    AfterValidator(timestamp),
    PlainSerializer(lambda dt: dt.isoformat(timespec="milliseconds").replace("+00:00", "Z")),
]
EmbeddingUsage = Literal["Query", "Passage"]
LogicalSource = Literal["working", "long_term"]
MemoryLabel = Literal["Working", "Episodic", "Semantic"]
RetrievalMode = Literal["working_only", "long_term_only", "combined"]
SourceEligibility = Literal["eligible", "no_current_scope", "not_authorized", "type_filtered"]
CoverageStatus = Literal["complete", "partial", "unavailable", "not_requested"]
Stage = Literal[
    "RUNNING_REQUEST_VALIDATION",
    "RUNNING_QUERY_EMBEDDING",
    "RUNNING_VECTOR_SEARCH",
    "RUNNING_CANDIDATE_VALIDATION",
    "RUNNING_CANONICAL_LOAD",
    "RUNNING_RANKING",
    "RUNNING_CONTEXT_ASSEMBLY",
    "RUNNING_TRACE_FINALIZATION",
]
TerminalState = Literal[
    "COMPLETE_AVAILABLE",
    "COMPLETE_EMPTY",
    "DEGRADED_AVAILABLE",
    "FAILED_UNAVAILABLE",
]
ExecutionState = Literal["CREATED", Stage, TerminalState]
CandidateDecision = Literal["accepted", "excluded", "unverifiable"]
EmbeddingExecutionState = Literal["queued", "running", "succeeded", "failed"]
EmbeddingErrorCode = Literal[
    "EMBEDDING_INPUT_INVALID",
    "EMBEDDING_BINDING_MISMATCH",
    "EMBEDDING_BUSY",
    "EMBEDDING_COMPUTE_FAILED",
    "EMBEDDING_DEADLINE_EXCEEDED",
]
ProjectionOperationKind = Literal["upsert", "delete"]
ProjectionCallKind = Literal["upsert", "delete", "query"]
ProjectionCoordinatorState = Literal[
    "idle",
    "dispatching",
    "checking",
    "settled",
    "attention_required",
]
ProjectionProviderState = Literal["ACCEPTED", "PENDING", "READY", "FAILED", "UNKNOWN"]
ProjectionRetryAdvice = Literal["none", "query_only", "safe_resubmit"]
ProjectionErrorCode = Literal[
    "PROJECTION_REQUEST_INVALID",
    "PROJECTION_SCOPE_DENIED",
    "PROJECTION_IDEMPOTENCY_CONFLICT",
    "PROJECTION_TARGET_RETIRED",
    "PROJECTION_BUSY",
    "PROJECTION_BINDING_MISMATCH",
    "PROJECTION_PROVIDER_FAILED",
    "PROJECTION_EFFECT_UNKNOWN",
    "PROJECTION_RECOVERY_EXHAUSTED",
    "PROJECTION_NOT_DURABLE",
    "PROJECTION_REBUILD_UNSUPPORTED",
]
P2OperationStatus = Literal[
    "accepted",
    "running",
    "completed",
    "rejected",
    "failed",
    "not_found",
    "unknown",
]
ReasonCode = Literal[
    "REQUEST_INVALID",
    "SCOPE_DENIED",
    "AUTHORITY_UNAVAILABLE",
    "ADMISSION_BUSY",
    "DEADLINE_EXCEEDED",
    "QUERY_EMBEDDING_FAILED",
    "EMBEDDING_CONTRACT_MISMATCH",
    "VECTOR_UNAVAILABLE",
    "VECTOR_PARTIAL",
    "CANDIDATE_REFERENCE_INVALID",
    "WORKING_UNAVAILABLE",
    "WORKING_PARTIAL",
    "MEMORY_READ_UNAVAILABLE",
    "MEMORY_INELIGIBLE",
    "MEMORY_VERSION_CHANGED",
    "PROJECTION_NOT_READY",
    "CONTENT_MAPPING_INVALID",
    "CONTENT_UNAVAILABLE",
    "CONTENT_INTEGRITY_FAILED",
    "CONTENT_LIMIT_EXCEEDED",
    "PREWARM_FALLBACK",
    "SEMANTIC_POLICY_UNAVAILABLE",
    "SEMANTIC_POLICY_FALLBACK",
    "CONFLICT_PRESENTED",
    "CONFLICT_UNSAFE",
    "BUDGET_TRUNCATED",
    "BUDGET_UNSATISFIABLE",
    "TRACE_INCOMPLETE",
    "TRACE_SAFETY_UNPROVEN",
    "FINALIZATION_NOT_DURABLE",
    "ACCESS_OUTBOX_PENDING",
    "IDEMPOTENCY_CONFLICT",
    "REPLAY_REVALIDATION_REQUIRED",
    "NO_TRUSTWORTHY_CONTEXT",
    "ASSEMBLY_FAILED",
    "INVARIANT_VIOLATION",
]
StageOutcome = Literal["started", "completed", "failed", "skipped", "interrupted"]
Sensitivity = Literal["restricted_metadata", "restricted_payload"]


class ContractModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        hide_input_in_errors=True,
        ser_json_bytes="base64",
        val_json_bytes="base64",
    )

    def replaced(self, **changes: object) -> Self:
        """Unlike model_copy(update=...), this validates the resulting record."""
        return type(self).model_validate({**self.model_dump(), **changes})


class ByteRange(ContractModel):
    start: UInt
    end: UInt

    @model_validator(mode="after")
    def nonempty(self) -> Self:
        if self.start >= self.end:
            raise ValueError("byte ranges are nonempty half-open intervals")
        return self

    def proto_inclusive(self) -> tuple[int, int]:
        return self.start, self.end - 1


class Hash(ContractModel):
    algorithm: Identifier
    value: Identifier
    byte_encoding: Identifier
    range: ByteRange | None

    @model_validator(mode="after")
    def digest_format(self) -> Self:
        if any(c not in "0123456789abcdef" for c in self.value):
            raise ValueError("digest must be lowercase hexadecimal")
        lengths = {"SHA-256": 64, "MD5": 32, "BLAKE3": 64}
        if self.algorithm in lengths and len(self.value) != lengths[self.algorithm]:
            raise ValueError("digest length does not match its algorithm")
        return self


class Scope(ContractModel):
    tenant_id: Identifier
    project_id: Identifier | None
    agent_id: Identifier | None
    session_id: Identifier | None
    task_id: Identifier | None


class ProjectionIdentity(ContractModel):
    memory_id: Identifier
    chunk_id: Identifier
    memory_version: Identifier
    model_version: Identifier
    projection_schema_version: Identifier


class RecordHeader(ContractModel):
    schema_version: Identifier
    tenant_id: Identifier
    created_at: Timestamp


class OnlineHeader(RecordHeader):
    recall_id: Identifier
    request_id: Identifier
    trace_id: Identifier


class MutableOnlineHeader(OnlineHeader):
    state_version: UInt


def hash_bytes(data: bytes, *, encoding: str = "raw", byte_range: ByteRange | None = None) -> Hash:
    return Hash(
        algorithm="SHA-256",
        value=hashlib.sha256(data).hexdigest(),
        byte_encoding=encoding,
        range=byte_range,
    )


def hash_json(value: object) -> Hash:
    """RFC 8785, not json.dumps(sort_keys=True), including safe integer checks."""
    # The serializer validates the JSON domain (including safe integer limits).
    return hash_bytes(rfc8785.dumps(cast(JsonValue, value)), encoding="jcs-utf8")


def hash_text(text: str) -> Hash:
    return hash_bytes(valid_text(text).encode("utf-8"), encoding="utf-8")


def vector_bytes(vector: list[float], dimension: int, dtype: str) -> bytes:
    if len(vector) != dimension or not vector:
        raise ValueError("vector dimension mismatch")
    for value in vector:
        finite_number(value)
    formats = {"float32": "f", "float64": "d"}
    if dtype not in formats:
        raise ValueError("unsupported vector encoding; register a compatible model contract")
    try:
        data = struct.pack("<" + formats[dtype] * dimension, *vector)
        values = struct.unpack("<" + formats[dtype] * dimension, data)
    except (OverflowError, struct.error) as exc:
        raise ValueError("vector cannot be represented by its dtype") from exc
    if not all(math.isfinite(v) for v in values) or not any(v != 0 for v in values):
        raise ValueError("vector must be finite and nonzero in its canonical encoding")
    return data
