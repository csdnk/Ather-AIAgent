"""Remember is the owner of facts, provenance, lifecycle and recall eligibility."""

from __future__ import annotations

from enum import StrEnum
from typing import Literal, Self

from pydantic import Field, model_validator

from aether_agent_memory.runtime.contracts.models import (
    ContractModel,
    Count,
    Digest,
    Identifier,
    NonEmpty,
    Positive,
    Scope,
    ScopeSelector,
    Timestamp,
)


class MemoryKind(StrEnum):
    WORKING = "working"
    EPISODIC = "episodic"
    SEMANTIC = "semantic"


class MemoryStatus(StrEnum):
    ACTIVE = "active"
    ARCHIVED = "archived"
    SUPERSEDED = "superseded"
    EXPIRED = "expired"
    DELETED = "deleted"


class RetentionRequest(ContractModel):
    expected_version: Positive
    expected_object_revision: Positive
    enabled: bool = True
    completed: bool = False
    archive_after_idle_hours: float = Field(default=168, ge=24, le=87600, allow_inf_nan=False)
    delete_after_archive_hours: float | None = Field(default=None, ge=24, le=87600)
    delete_below_strength: float = Field(default=0.05, gt=0, lt=0.2)
    legal_hold: bool = False
    expires_at: Timestamp | None = None
    reason: NonEmpty


class ReflectionRequest(ContractModel):
    selection: ScopeSelector
    expected_revision: Count = 0
    enabled: bool = True
    min_episodes: int = Field(default=3, ge=2, le=32)
    importance_threshold: float = Field(default=0.8, ge=0.8, le=1)
    min_reinforcements: int = Field(default=3, ge=1)
    period_hours: float = Field(default=24, ge=1, le=720)
    max_episodes: int = Field(default=8, ge=2, le=32)
    reason: NonEmpty

    @model_validator(mode="after")
    def bounded_batch(self) -> Self:
        if self.max_episodes < self.min_episodes:
            raise ValueError("max_episodes must cover min_episodes")
        return self


class ProjectionState(StrEnum):
    NOT_REQUIRED = "not_required"
    PENDING = "pending"
    BUILDING = "building"
    READY = "ready"
    FAILED = "failed"
    STALE = "stale"


class MemoryRef(ContractModel):
    scope: Scope
    memory_id: Identifier
    version: Positive


class SourceRef(ContractModel):
    source_id: Identifier
    source_version: Positive
    content_hash: Digest
    locator: NonEmpty


class SourceInput(ContractModel):
    kind: Literal["conversation", "tool_result", "task_state", "text", "document"]
    external_id: Identifier
    external_version: NonEmpty
    occurred_at: Timestamp


class TextInput(ContractModel):
    kind: Literal["text"]
    text: NonEmpty


class DocumentInput(ContractModel):
    """An allowlisted provider reference, never an unrestricted URL fetch."""

    kind: Literal["document"]
    provider_id: Identifier
    document_id: Identifier
    document_version: NonEmpty
    expected_hash: Digest


class SourceAcquisition(ContractModel):
    document: DocumentInput
    original_storage_ref: NonEmpty
    original_bytes: Positive
    verified_hash: Digest
    acquired_at: Timestamp

    @model_validator(mode="after")
    def exact_document(self) -> Self:
        if self.verified_hash != self.document.expected_hash:
            raise ValueError("acquisition must match the authorized document version")
        return self


class DocumentContent(ContractModel):
    document: DocumentInput
    original_storage_ref: NonEmpty
    media_type: NonEmpty
    extracted_text: NonEmpty
    extracted_hash: Digest
    parser_version: Identifier


class RememberRequest(ContractModel):
    source: SourceInput
    selection: ScopeSelector
    content: TextInput | DocumentInput = Field(discriminator="kind")
    trigger: Literal["remember", "observe", "task_complete", "review"] = "remember"
    task_context: str = Field(default="", max_length=4096)
    importance_category: Literal[
        "observation", "event", "fact", "decision", "explicit_constraint"
    ] = "observation"


class RememberReceipt(ContractModel):
    operation_id: Identifier
    saved: bool
    source: SourceRef
    memories: tuple[MemoryRef, ...]
    task_ids: tuple[Identifier, ...]
    phase: Literal["saved", "processing", "ready"]

    @model_validator(mode="after")
    def confirmed_save(self) -> Self:
        if not self.saved:
            raise ValueError("successful receipt requires confirmed durable save")
        return self


class MemorySnapshot(ContractModel):
    ref: MemoryRef
    revision: Positive
    object_revision: Positive
    kind: MemoryKind
    status: MemoryStatus
    content: NonEmpty
    content_hash: Digest
    sources: tuple[SourceRef, ...] = Field(min_length=1)
    projection_state: ProjectionState
    model_space: Identifier | None = None
    expires_at: Timestamp | None = None
    supersedes: MemoryRef | None = None
    created_at: Timestamp
    importance: float = Field(default=0.2, ge=0, le=1)
    importance_reason: Identifier = "ordinary_observation"
    importance_policy_version: Identifier = "remember_v2"

    @model_validator(mode="after")
    def projection_binding(self) -> Self:
        if self.projection_state == ProjectionState.READY and (
            self.model_space is None or self.status != MemoryStatus.ACTIVE
        ):
            raise ValueError("ready requires model space and an active version")
        if self.supersedes is not None and (
            self.supersedes.scope != self.ref.scope
            or self.supersedes.memory_id != self.ref.memory_id
            or self.supersedes.version >= self.ref.version
        ):
            raise ValueError("supersedes must identify an earlier version of this object")
        return self


class CorrectionRequest(ContractModel):
    expected_version: Positive
    content: NonEmpty
    source: SourceInput
    reason: NonEmpty
    expected_object_revision: Positive | None = None


class LifecycleRequest(ContractModel):
    expected_version: Positive
    target: Literal["archived", "active"]
    reason: NonEmpty
    expected_object_revision: Positive | None = None


class DeleteRequest(ContractModel):
    expected_revision: Positive
    reason: NonEmpty


class DeleteReceipt(ContractModel):
    operation_id: Identifier
    blocked: Literal[True]
    cleanup_state: Literal["pending", "running", "completed", "partial_failure", "unknown"]
    task_ids: tuple[Identifier, ...]
    remaining_targets: tuple[NonEmpty, ...]

    @model_validator(mode="after")
    def cleanup_evidence(self) -> Self:
        if self.cleanup_state == "completed" and self.remaining_targets:
            raise ValueError("completed cleanup cannot have remaining targets")
        return self


class ArtifactRecord(ContractModel):
    artifact_id: Identifier
    memory: MemoryRef
    representation: Literal["original", "compressed"]
    source_hash: Digest
    output_hash: Digest
    original_bytes: Positive
    stored_bytes: Positive
    quality: Literal["pending", "passed", "failed"]
    quality_policy: Identifier
    content_ref: NonEmpty
    content: NonEmpty
    sources: tuple[SourceRef, ...] = Field(min_length=1)


class FactEvidence(ContractModel):
    source: SourceRef
    start_char: Count
    end_char: Positive
    quote: NonEmpty

    @model_validator(mode="after")
    def exact_length(self) -> Self:
        if self.end_char - self.start_char != len(self.quote):
            raise ValueError("evidence must identify an exact Unicode slice")
        return self


class CandidateFact(ContractModel):
    text: NonEmpty
    sources: tuple[SourceRef, ...] = Field(min_length=1)
    evidence_status: Literal["candidate", "supported", "insufficient"]
    kind: Literal["episodic", "semantic"] = "episodic"
    evidence: tuple[FactEvidence, ...] = ()
    event_key: Identifier | None = None
    fact_key: Identifier | None = None
    importance_category: Literal["event", "fact", "decision", "explicit_constraint"] = "event"


class ExtractionRequest(ContractModel):
    source: SourceRef
    text: NonEmpty
    existing: tuple[MemorySnapshot, ...]
    policy_version: Identifier


class ExtractionResult(ContractModel):
    candidates: tuple[CandidateFact, ...]
    model_id: NonEmpty
    policy_version: Identifier


class EligibilityResult(ContractModel):
    ref: MemoryRef
    decision: Literal["allowed", "excluded", "unverifiable"]
    reason: NonEmpty
    checked_revision: Positive | None = None


class EligibilityBatch(ContractModel):
    items: tuple[EligibilityResult, ...]
    authorization_epoch: Positive


class ConflictGroup(ContractModel):
    group_id: Identifier
    members: tuple[MemoryRef, ...] = Field(min_length=2)
    explanation: NonEmpty
    state: Literal["unresolved"] = "unresolved"

    @model_validator(mode="after")
    def distinct_members(self) -> Self:
        if len({m.model_dump_json() for m in self.members}) != len(self.members):
            raise ValueError("conflict members must be distinct")
        if len({m.scope.tenant_id for m in self.members}) != 1:
            raise ValueError("conflicts cannot span tenants")
        return self


class MemoryReadBatch(ContractModel):
    items: tuple[MemorySnapshot, ...]
    eligibility: EligibilityBatch
    conflicts: tuple[ConflictGroup, ...]
    artifacts: tuple[ArtifactRecord, ...] = ()
    next_cursor: NonEmpty | None = None


class StorageChanged(ContractModel):
    memory: MemoryRef
    object_revision: Positive
    change: Literal[
        "saved",
        "corrected",
        "archived",
        "activated",
        "expired",
        "deleted",
        "processing",
        "projection_ready",
        "projection_stale",
        "artifact_ready",
    ]
    status: MemoryStatus
    projection_state: ProjectionState
    content_hash: Digest
    source_count: Count
    content_bytes: Count = 0
    content_ref: NonEmpty | None = None
    importance: float = Field(default=0.2, ge=0, le=1)
    importance_reason: Identifier = "ordinary_observation"
    importance_policy_version: Identifier = "remember_v2"
    memory_kind: MemoryKind = MemoryKind.WORKING
    previous_state: MemoryStatus | None = None
    expires_at: Timestamp | None = None
    change_seq: Positive = 1
    semantic_revision: Positive = 1
    relations_revision: Positive = 1
    space_commit_seq: Count = 0
    memory_space_id: Identifier = "legacy_scope"
    signal_type: Identifier = "legacy_changed"
    deleted: bool = False
    reason_code: Identifier = "legacy_change"
    importance_basis: tuple[Identifier, ...] = ()
    requires_operate_evaluation: bool = True


class ProjectionTarget(ContractModel):
    memory: MemoryRef
    model_space: Identifier
    chunk_index: Count
    vector_id: Digest
    input_hash: Digest
    generation: Identifier | None = None
    body_hash: Digest | None = None
    memory_source: Literal["working", "long_term"] = "long_term"


class ProjectionRequest(ContractModel):
    operation_id: Identifier
    target: ProjectionTarget
    vector: tuple[float, ...] = Field(min_length=1)
    deadline_at: Timestamp


class ProjectionResult(ContractModel):
    operation_id: Identifier
    target: ProjectionTarget
    state: Literal["accepted", "pending", "verified", "failed", "unknown", "absent"]
    payload_matches: bool
    searchable: bool
    observed_at: Timestamp

    @model_validator(mode="after")
    def verified_evidence(self) -> Self:
        if self.state == "verified" and not (self.payload_matches and self.searchable):
            raise ValueError("verified requires exact payload and query visibility")
        return self
