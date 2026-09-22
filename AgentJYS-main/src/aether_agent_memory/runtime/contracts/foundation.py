"""Additive RF foundation contracts; no probe, recovery or persistence side effects."""

from __future__ import annotations

from typing import Literal, Self

from pydantic import Field, model_validator

from .models import (
    ContractModel,
    Count,
    Digest,
    EffectStatus,
    Flow,
    Identifier,
    NonEmpty,
    Permission,
    Positive,
    RecordRef,
    SpanId,
    Timestamp,
    TraceId,
)


class ResourceLocation(ContractModel):
    """An opaque provider key, never an arbitrary URL or a credential."""

    kind: Literal["source", "body", "artifact", "vector", "cache", "backup"]
    provider_id: Identifier
    provider_instance_id: Identifier
    namespace: Identifier
    object_key: NonEmpty
    generation: Identifier
    content_hash: Digest


class NodeLogRecord(ContractModel):
    """Normalized log contract; existing schema_version=1 logs need an adapter."""

    schema_version: Literal["p3/log/2"] = "p3/log/2"
    occurred_at: Timestamp
    service: Identifier
    instance: Identifier
    flow: Flow
    request_id: Identifier
    operation_id: Identifier
    trace_id: TraceId
    span_id: SpanId
    parent_span_id: SpanId | None = None
    task_id: Identifier | None = None
    event_id: Identifier | None = None
    node: Identifier
    phase: Literal["started", "returned", "failed", "cancelled", "committed", "rolled_back"]
    level: Literal["debug", "info", "warning", "error"]
    elapsed_ms: float | None = Field(default=None, ge=0)
    reason_code: Identifier | None = None
    input_count: Count | None = None
    output_count: Count | None = None
    # Deliberately no arbitrary payload, body, token, exception text or URL field.

    @model_validator(mode="after")
    def terminal_evidence(self) -> Self:
        if self.parent_span_id == self.span_id:
            raise ValueError("a span cannot parent itself")
        if self.phase != "started" and self.elapsed_ms is None:
            raise ValueError("finished nodes require elapsed time")
        if self.phase == "failed" and self.reason_code is None:
            raise ValueError("failed nodes require a bounded reason code")
        return self


class HealthObservation(ContractModel):
    name: Identifier
    category: Literal["dependency", "capability", "worker"]
    state: Literal["available", "unavailable", "degraded", "unknown", "disabled"]
    checked_at: Timestamp
    fresh_until: Timestamp
    elapsed_ms: float = Field(ge=0)
    reason_code: Identifier
    evidence_refs: tuple[RecordRef, ...] = ()

    @model_validator(mode="after")
    def freshness_window(self) -> Self:
        if self.fresh_until <= self.checked_at:
            raise ValueError("freshness expiry must follow the observation")
        return self


class RuntimeHealthSnapshot(ContractModel):
    schema_version: Literal["p3/health/2"] = "p3/health/2"
    checked_at: Timestamp
    config_version: Identifier
    liveness: Literal["alive", "unknown"]
    readiness: Literal["ready", "not_ready", "unknown"]
    required_capabilities: tuple[Identifier, ...] = Field(min_length=1)
    observations: tuple[HealthObservation, ...]
    production_acceptance: Literal[False] = False

    @model_validator(mode="after")
    def readiness_evidence(self) -> Self:
        keys = [(x.category, x.name) for x in self.observations]
        if len(set(keys)) != len(keys):
            raise ValueError("duplicate health observation")
        if len(set(self.required_capabilities)) != len(self.required_capabilities):
            raise ValueError("duplicate required capability")
        if any(x.checked_at > self.checked_at for x in self.observations):
            raise ValueError("future probe cannot support a report")
        capabilities = {x.name: x for x in self.observations if x.category == "capability"}
        if self.readiness == "ready" and (
            self.liveness != "alive"
            or any(
                name not in capabilities
                or capabilities[name].state != "available"
                or capabilities[name].fresh_until <= self.checked_at
                for name in self.required_capabilities
            )
        ):
            raise ValueError("ready requires fresh evidence for every required capability")
        return self


class SignalDefinition(ContractModel):
    signal_id: Identifier
    owner: Flow
    source: NonEmpty
    unit: Literal["count", "milliseconds", "bytes", "ratio", "state"]
    sample_interval_ms: Positive
    stale_after_ms: Positive
    label_names: tuple[Identifier, ...]
    max_label_sets: Positive
    missing_policy: Literal["unknown"] = "unknown"

    @model_validator(mode="after")
    def bounded_sampling(self) -> Self:
        if self.stale_after_ms < self.sample_interval_ms:
            raise ValueError("freshness must cover at least one sampling interval")
        if len(set(self.label_names)) != len(self.label_names):
            raise ValueError("duplicate label name")
        return self


class OperationDefinition(ContractModel):
    operation_kind: Identifier
    owner: Flow
    execution_class: Literal["inline", "io", "model", "maintenance"]
    input_model: NonEmpty
    output_model: NonEmpty
    permission: Permission
    side_effect: Literal["none", "transactional", "external"]
    idempotency_required: bool
    success_evidence: tuple[Identifier, ...] = Field(min_length=1)
    recovery: Literal["resume", "query_only", "manual"]
    signal_ids: tuple[Identifier, ...] = Field(min_length=1)
    validation_scenarios: tuple[Identifier, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def external_recovery(self) -> Self:
        if self.side_effect == "external" and (
            not self.idempotency_required or self.recovery == "resume"
        ):
            raise ValueError("external effects need idempotency and query/manual recovery")
        return self


class SignalObservation(ContractModel):
    signal: SignalDefinition
    observed_at: Timestamp
    state: Literal["known", "unknown", "stale"]
    value: float | Identifier | None = None
    labels: dict[Identifier, Identifier]
    evidence_refs: tuple[RecordRef, ...]

    @model_validator(mode="after")
    def honest_signal(self) -> Self:
        if set(self.labels) != set(self.signal.label_names):
            raise ValueError("signal labels must match the registered bounded dimensions")
        if (self.state == "known") != (self.value is not None):
            raise ValueError("unknown/stale signals cannot masquerade as current values")
        if self.state == "known" and not self.evidence_refs:
            raise ValueError("known measurements need source evidence")
        if self.state == "known":
            if (self.signal.unit == "state") != isinstance(self.value, str):
                raise ValueError("state signals require state codes; numeric units require numbers")
            if isinstance(self.value, float):
                if self.value < 0:
                    raise ValueError("count, duration, size and ratio signals cannot be negative")
                if self.signal.unit == "ratio" and self.value > 1:
                    raise ValueError("ratio signals must lie between zero and one")
                if self.signal.unit in {"count", "bytes"} and not self.value.is_integer():
                    raise ValueError("count and byte signals must be integral")
        return self


class WorkerHeartbeat(ContractModel):
    worker_id: Identifier
    instance_id: Identifier
    execution_class: Literal["io", "model", "maintenance"]
    state: Literal["polling", "running", "stopped"]
    last_seen: Timestamp
    fresh_until: Timestamp
    task_id: Identifier | None = None
    lease_token: Identifier | None = None

    @model_validator(mode="after")
    def leased_work(self) -> Self:
        if self.fresh_until <= self.last_seen:
            raise ValueError("heartbeat needs a freshness deadline")
        if self.state == "running":
            if self.task_id is None or self.lease_token is None:
                raise ValueError("running worker must identify its leased task")
        elif self.task_id is not None or self.lease_token is not None:
            raise ValueError("non-running heartbeat cannot claim a task lease")
        return self


class DispositionRule(ContractModel):
    rule_id: Identifier
    revision: Positive
    signal_id: Identifier
    comparator: Literal["gt", "ge", "lt", "le", "eq"]
    threshold: float | Identifier
    consecutive_samples: Positive
    operation_kind: Identifier
    max_attempts: Positive
    deadline_ms: Positive
    cooldown_ms: Positive
    verification_operation: Identifier
    escalation: Literal["attention_required"] = "attention_required"

    @model_validator(mode="after")
    def state_comparison(self) -> Self:
        if isinstance(self.threshold, str) and self.comparator != "eq":
            raise ValueError("state codes support equality comparison only")
        return self


class IncidentRecord(ContractModel):
    incident_id: Identifier
    rule_id: Identifier
    rule_revision: Positive
    subject: RecordRef
    revision: Positive
    state: Literal["open", "recovering", "verifying", "resolved", "attention_required"]
    operation_id: Identifier | None = None
    evidence_refs: tuple[RecordRef, ...] = Field(min_length=1)
    verification: Literal["pending", "passed", "failed", "unknown"]
    verification_refs: tuple[RecordRef, ...] = ()
    opened_at: Timestamp
    updated_at: Timestamp

    @model_validator(mode="after")
    def verified_resolution(self) -> Self:
        if self.updated_at < self.opened_at:
            raise ValueError("incident timestamps are reversed")
        if self.state == "resolved" and (
            self.verification != "passed" or not self.verification_refs
        ):
            raise ValueError("maintenance completion alone cannot resolve an incident")
        return self


class TaskWaitRecord(ContractModel):
    """Attached to the existing TaskRecord; this is not another task state machine."""

    task_id: Identifier
    expected_task_revision: Positive
    dependency_id: Identifier
    reason_code: Identifier
    wait_started_at: Timestamp
    next_check_at: Timestamp
    deadline_at: Timestamp
    original_operation_id: Identifier
    effect_status: EffectStatus
    resume_mode: Literal["resume", "query_only", "attention"]

    @model_validator(mode="after")
    def bounded_wait(self) -> Self:
        if not self.wait_started_at < self.next_check_at <= self.deadline_at:
            raise ValueError("dependency wait requires a bounded next check")
        if self.effect_status == EffectStatus.UNKNOWN and self.resume_mode == "resume":
            raise ValueError("unknown external effects cannot be blindly resubmitted")
        return self


class CheckpointRecord(ContractModel):
    task_id: Identifier
    expected_task_revision: Positive
    stage: Identifier
    input_hash: Digest
    output_refs: tuple[RecordRef, ...] = Field(min_length=1)
    committed_at: Timestamp
    config_version: Identifier


class ConfigurationSnapshot(ContractModel):
    version: Identifier
    config_hash: Digest
    deployment_id: Identifier
    provider_ids: tuple[Identifier, ...] = Field(min_length=1)
    policy_versions: tuple[Identifier, ...] = Field(min_length=1)
    secret_refs: tuple[Identifier, ...] = ()
    activated_at: Timestamp


class BackupManifest(ContractModel):
    backup_id: Identifier
    config_version: Identifier
    schema_version: Identifier
    consistency_watermark: Count
    location: ResourceLocation
    created_at: Timestamp
    restore_state: Literal["untested", "passed", "failed", "unknown"]
    restore_evidence: tuple[RecordRef, ...] = ()

    @model_validator(mode="after")
    def restore_proof(self) -> Self:
        if self.location.kind != "backup":
            raise ValueError("backup requires a backup location")
        if self.restore_state == "passed" and not self.restore_evidence:
            raise ValueError("a saved backup does not prove successful restoration")
        return self
