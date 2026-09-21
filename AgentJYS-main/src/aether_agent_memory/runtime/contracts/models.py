"""Shared wire contracts; domain facts are owned by the three flow packages.

Validation proves representation consistency, not authorization or persistence.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from hashlib import sha256
from typing import Annotated, Literal, Self

import rfc8785
from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    StrictInt,
    model_validator,
)

Identifier = Annotated[str, Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")]
NonEmpty = Annotated[str, Field(min_length=1)]
Positive = Annotated[StrictInt, Field(ge=1)]
Count = Annotated[StrictInt, Field(ge=0)]
Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


def valid_timestamp(value: str) -> str:
    datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%fZ")
    return value


def nonzero_trace(value: str) -> str:
    if not int(value, 16):
        raise ValueError("W3C trace/span IDs cannot be all zero")
    return value


Timestamp = Annotated[
    str,
    Field(
        pattern=r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$",
        json_schema_extra={"format": "date-time"},
    ),
    AfterValidator(valid_timestamp),
]
TraceId = Annotated[str, Field(pattern=r"^[0-9a-f]{32}$"), AfterValidator(nonzero_trace)]
SpanId = Annotated[str, Field(pattern=r"^[0-9a-f]{16}$"), AfterValidator(nonzero_trace)]


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)


class Flow(StrEnum):
    RUNTIME = "runtime"
    REMEMBER = "remember"
    RECALL = "recall"
    OPERATE = "operate"


class Scope(ContractModel):
    tenant_id: Identifier
    application_id: Identifier
    user_id: Identifier
    agent_id: Identifier
    session_id: Identifier | None = None
    task_id: Identifier | None = None


class ScopeSelector(ContractModel):
    """A requested restriction. Missing fields never grant access."""

    application_id: Identifier | None = None
    user_id: Identifier | None = None
    agent_id: Identifier | None = None
    session_id: Identifier | None = None
    task_id: Identifier | None = None


class Permission(StrEnum):
    READ = "memory:read"
    WRITE = "memory:write"
    CORRECT = "memory:correct"
    DELETE = "memory:delete"
    HISTORY = "memory:history"
    DIAGNOSE = "maintenance:diagnose"
    RECOVER = "maintenance:recover"
    CONFIGURE = "maintenance:configure"


class Principal(ContractModel):
    principal_id: Identifier
    home_scope: Scope
    permissions: tuple[Permission, ...]
    auth_epoch: Positive


class TrustedContext(ContractModel):
    """Only authentication adapters may construct this for application execution."""

    principal: Principal
    request_id: Identifier
    operation_id: Identifier
    trace_id: TraceId
    span_id: SpanId
    deadline_at: Timestamp


class RecordRef(ContractModel):
    owner: Flow
    object_type: Identifier
    object_id: Identifier
    scope: Scope
    version: Positive | None = None


class AuthorizationGrant(ContractModel):
    """Deployment-managed explicit same-tenant resource grants, not an RBAC role."""

    grant_id: Identifier
    grantee_id: Identifier
    grantee_tenant_id: Identifier
    resource: RecordRef
    permissions: tuple[Permission, ...] = Field(min_length=1)
    revision: Positive
    expires_at: Timestamp | None = None

    @model_validator(mode="after")
    def same_tenant(self) -> Self:
        if self.resource.scope.tenant_id != self.grantee_tenant_id:
            raise ValueError("cross-tenant sharing is not supported")
        return self


class ErrorCode(StrEnum):
    UNAUTHENTICATED = "UNAUTHENTICATED"
    FORBIDDEN = "FORBIDDEN"
    NOT_FOUND = "NOT_FOUND"
    INVALID_ARGUMENT = "INVALID_ARGUMENT"
    VERSION_CONFLICT = "VERSION_CONFLICT"
    IDEMPOTENCY_CONFLICT = "IDEMPOTENCY_CONFLICT"
    REQUEST_IN_PROGRESS = "REQUEST_IN_PROGRESS"
    RESULT_INVALIDATED = "RESULT_INVALIDATED"
    MEMORY_GONE = "MEMORY_GONE"
    CAPACITY_EXCEEDED = "CAPACITY_EXCEEDED"
    BUDGET_TOO_SMALL = "BUDGET_TOO_SMALL"
    DEPENDENCY_UNAVAILABLE = "DEPENDENCY_UNAVAILABLE"
    COMMIT_UNCONFIRMED = "COMMIT_UNCONFIRMED"
    DEADLINE_EXCEEDED = "DEADLINE_EXCEEDED"
    EXECUTION_INTERRUPTED = "EXECUTION_INTERRUPTED"
    CONTRACT_VIOLATION = "CONTRACT_VIOLATION"
    TASK_STILL_RUNNING = "TASK_STILL_RUNNING"


class ErrorResponse(ContractModel):
    code: ErrorCode
    message: NonEmpty
    retryable: bool
    request_id: Identifier
    operation_id: Identifier | None = None


class EventEnvelope(ContractModel):
    schema_version: Literal["p3/1"] = "p3/1"
    event_id: Identifier
    event_type: NonEmpty
    producer: Flow
    subject: RecordRef
    subject_revision: Positive
    occurred_at: Timestamp
    request_id: Identifier
    trace_id: TraceId
    causation_id: Identifier | None = None
    initiator_id: Identifier
    initiator_auth_epoch: Positive
    payload: dict[str, JsonValue]
    payload_hash: Digest

    @model_validator(mode="after")
    def canonical_payload(self) -> Self:
        if sha256(rfc8785.dumps(self.payload)).hexdigest() != self.payload_hash:
            raise ValueError("event payload does not match its canonical fingerprint")
        return self


class TaskState(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    RETRY_WAIT = "retry_wait"
    RECOVERY_WAIT = "recovery_wait"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    ATTENTION = "attention_required"


class EffectStatus(StrEnum):
    NOT_STARTED = "not_started"
    NO_EFFECT = "no_effect"
    UNKNOWN = "unknown"
    CONFIRMED = "confirmed"


class Lease(ContractModel):
    owner_id: Identifier
    token: Identifier
    until: Timestamp


class TaskSpec(ContractModel):
    task_id: Identifier
    owner_flow: Flow
    kind: NonEmpty
    subject: RecordRef
    input_ref: RecordRef
    idempotency_key: Identifier
    input_hash: Digest
    initiator_id: Identifier
    initiator_auth_epoch: Positive
    deadline_at: Timestamp
    max_attempts: Positive = 3


class TaskRecord(TaskSpec):
    state: TaskState
    revision: Positive
    attempt: Count
    query_attempt: Count = 0
    next_run_at: Timestamp | None = None
    lease: Lease | None = None
    effect_status: EffectStatus
    required_outputs: tuple[RecordRef, ...] = ()
    result_ref: RecordRef | None = None
    error_code: ErrorCode | None = None

    @model_validator(mode="after")
    def coherent_state(self) -> Self:
        if self.attempt > self.max_attempts:
            raise ValueError("attempt exceeds execution budget")
        if self.state == TaskState.RUNNING and self.lease is None:
            raise ValueError("running requires a lease")
        if self.state == TaskState.SUCCEEDED and self.result_ref is None:
            raise ValueError("success requires a result reference")
        if self.effect_status == EffectStatus.UNKNOWN and self.state in {
            TaskState.SUCCEEDED,
            TaskState.FAILED,
            TaskState.CANCELLED,
        }:
            raise ValueError("unknown effects must remain unresolved")
        return self


class AttemptRecord(ContractModel):
    task_id: Identifier
    attempt: Positive
    lease_token: Identifier
    request_id: Identifier
    trace_id: TraceId
    started_at: Timestamp
    finished_at: Timestamp | None = None
    effect_status: EffectStatus
    error_code: ErrorCode | None = None


class RecoveryAction(StrEnum):
    RESUME = "resume"
    QUERY_ONLY = "query_only"
    CANCEL = "cancel"
    FAIL = "fail"
    ATTENTION = "attention"


class RecoveryDecision(ContractModel):
    action: RecoveryAction
    effect_status: EffectStatus
    reason: NonEmpty
    evidence: tuple[RecordRef, ...] = Field(min_length=1)
    original_operation_id: Identifier | None = None

    @model_validator(mode="after")
    def no_blind_retry(self) -> Self:
        if self.effect_status == EffectStatus.UNKNOWN and self.action not in {
            RecoveryAction.QUERY_ONLY,
            RecoveryAction.ATTENTION,
        }:
            raise ValueError("unknown effects require query or attention")
        if self.action == RecoveryAction.QUERY_ONLY and self.original_operation_id is None:
            raise ValueError("query must identify the original operation")
        return self


class RunResult(ContractModel):
    outcome: Literal["committed", "retryable_no_effect", "uncertain", "obsolete", "failed"]
    effect_status: EffectStatus
    result_ref: RecordRef | None = None
    operation_id: Identifier | None = None
    reason: NonEmpty

    @model_validator(mode="after")
    def coherent_result(self) -> Self:
        if self.outcome == "committed" and self.result_ref is None:
            raise ValueError("committed requires durable result reference")
        if self.effect_status == EffectStatus.UNKNOWN and self.outcome != "uncertain":
            raise ValueError("unknown cannot be reported as a definitive result")
        if self.outcome == "retryable_no_effect" and self.effect_status not in {
            EffectStatus.NOT_STARTED,
            EffectStatus.NO_EFFECT,
        }:
            raise ValueError("retryable_no_effect cannot conceal a side effect")
        return self


class DeliveryRecord(ContractModel):
    event_id: Identifier
    consumer_id: Identifier
    state: Literal["pending", "sent", "retry_wait", "acknowledged", "attention_required"]
    attempt: Count
    revision: Positive
    next_run_at: Timestamp | None = None
    acknowledged_at: Timestamp | None = None


class RecoveryRequest(ContractModel):
    operation_id: Identifier
    task_id: Identifier
    expected_revision: Positive
    reason: NonEmpty


class OperationRecord(ContractModel):
    operation_id: Identifier
    subject: RecordRef
    phase: NonEmpty
    state: Literal["accepted", "running", "completed", "failed", "unknown"]
    task_ids: tuple[Identifier, ...] = ()
    result_refs: tuple[RecordRef, ...] = ()
    reason: NonEmpty | None = None


class MaintenanceRecord(ContractModel):
    record_id: Identifier
    actor_id: Identifier
    operation_id: Identifier
    subject: RecordRef
    reason: NonEmpty
    occurred_at: Timestamp
    phase: Literal["accepted", "completed", "failed", "unknown"]
    config_version: Identifier
    result_ref: RecordRef | None = None


class DiagnosticRecord(ContractModel):
    record_id: Identifier
    subject: RecordRef
    request_id: Identifier
    trace_id: TraceId
    stage: NonEmpty
    occurred_at: Timestamp
    related: tuple[RecordRef, ...] = ()
    reason_code: NonEmpty | None = None
    coverage: Literal["complete", "partial", "unknown"]


class CapabilityHealth(ContractModel):
    capability: Literal["save", "working_read", "long_term", "context", "scheduling"]
    state: Literal["available", "degraded", "unavailable", "unknown"]
    checked_at: Timestamp
    reason: NonEmpty | None = None


class HealthReport(ContractModel):
    capabilities: tuple[CapabilityHealth, ...]
    config_version: Identifier


class PageRequest(ContractModel):
    limit: Annotated[StrictInt, Field(ge=1, le=100)] = 50
    cursor: NonEmpty | None = None


class RecordPage(ContractModel):
    records: tuple[RecordRef, ...]
    next_cursor: NonEmpty | None = None


class RelatedRecords(ContractModel):
    subject: RecordRef
    records: tuple[DiagnosticRecord, ...]
    next_cursor: NonEmpty | None = None
