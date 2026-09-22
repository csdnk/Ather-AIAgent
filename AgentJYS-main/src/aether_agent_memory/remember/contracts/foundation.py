"""B owns authoritative content, index publication and cross-flow reference changes."""

from __future__ import annotations

from hashlib import sha256
from typing import Literal, Self

from pydantic import Field, model_validator

from aether_agent_memory.runtime.contracts.foundation import ResourceLocation
from aether_agent_memory.runtime.contracts.models import (
    ContractModel,
    Count,
    Digest,
    Identifier,
    NonEmpty,
    Positive,
    RecordRef,
    Scope,
    Timestamp,
)

from .models import (
    CandidateFact,
    MemoryKind,
    MemoryRef,
    MemoryStatus,
    ProjectionState,
    SourceInput,
    SourceRef,
)


class SourceRecord(ContractModel):
    scope: Scope
    source: SourceRef
    origin: SourceInput
    revision: Positive
    original_location: ResourceLocation
    saved_at: Timestamp
    operation_id: Identifier
    processing_task_ids: tuple[Identifier, ...]

    @model_validator(mode="after")
    def source_binding(self) -> Self:
        if (
            self.original_location.kind != "source"
            or self.original_location.content_hash != self.source.content_hash
        ):
            raise ValueError("source record requires exact original content")
        return self


class DerivedArtifact(ContractModel):
    """Source-based processing output; does not replace authoritative Memory content."""

    artifact_id: Identifier
    scope: Scope
    source: SourceRef
    kind: Literal["parsed", "compressed", "summary", "extracted"]
    task_id: Identifier
    strategy_version: Identifier
    location: ResourceLocation
    quality: Literal["pending", "passed", "failed"]
    created_at: Timestamp

    @model_validator(mode="after")
    def artifact_location(self) -> Self:
        if self.location.kind != "artifact":
            raise ValueError("processing output requires an artifact location")
        return self


class ProcessingRecord(ContractModel):
    processing_id: Identifier
    scope: Scope
    source: SourceRef
    task_id: Identifier
    revision: Positive
    stage: Literal["parse", "compress", "summarize", "extract", "project", "finalize"]
    state: Literal["pending", "running", "completed", "failed", "unknown"]
    policy_version: Identifier
    artifact_ids: tuple[Identifier, ...]
    memories: tuple[MemoryRef, ...]
    updated_at: Timestamp
    reason_code: Identifier | None = None

    @model_validator(mode="after")
    def processing_result(self) -> Self:
        if any(m.scope != self.scope for m in self.memories):
            raise ValueError("processing outputs must belong to the source scope")
        if self.state == "completed" and self.stage != "finalize":
            raise ValueError("processing completion requires finalization")
        if self.state in {"failed", "unknown"} and self.reason_code is None:
            raise ValueError("processing failure/uncertainty requires an explanation")
        return self


class ExtractionDecision(ContractModel):
    decision_id: Identifier
    processing_id: Identifier
    candidate: CandidateFact
    outcome: Literal["create", "correct", "no_change", "reject"]
    target: MemoryRef | None = None
    expected_object_revision: Positive | None = None
    policy_version: Identifier
    reason_code: Identifier

    @model_validator(mode="after")
    def supported_fact(self) -> Self:
        if self.outcome in {"create", "correct"} and self.candidate.evidence_status != "supported":
            raise ValueError("unsupported candidate cannot become an authoritative fact")
        if self.outcome in {"correct", "no_change"}:
            if self.target is None or self.expected_object_revision is None:
                raise ValueError("existing fact decisions need an exact target and CAS revision")
        elif self.target is not None or self.expected_object_revision is not None:
            raise ValueError("create/reject does not mutate an existing memory")
        return self


class ChunkDescriptor(ContractModel):
    chunk_index: Count
    vector_id: Digest
    start_char: Count
    end_char: Positive
    input_hash: Digest
    verified: bool

    @model_validator(mode="after")
    def nonempty_range(self) -> Self:
        if self.end_char <= self.start_char:
            raise ValueError("chunk uses a nonempty half-open Unicode character range")
        return self


class ProjectionManifest(ContractModel):
    memory: MemoryRef
    generation: Identifier
    body_hash: Digest
    model_space: Identifier
    dimensions: Positive
    chunker_version: Identifier
    embedding_tokenizer: Identifier
    expected_chunk_count: Positive
    chunks: tuple[ChunkDescriptor, ...] = Field(min_length=1)
    state: Literal["building", "ready", "failed", "stale"]
    vector_location: ResourceLocation
    task_id: Identifier
    published_at: Timestamp | None = None

    @model_validator(mode="after")
    def publication_evidence(self) -> Self:
        if sorted(c.chunk_index for c in self.chunks) != list(range(self.expected_chunk_count)):
            raise ValueError("manifest must declare every expected chunk exactly once")
        if len({c.vector_id for c in self.chunks}) != len(self.chunks):
            raise ValueError("duplicate vector identity")
        if (
            self.vector_location.kind != "vector"
            or self.vector_location.generation != self.generation
            or self.vector_location.content_hash != self.body_hash
        ):
            raise ValueError("index address must bind the generation and source body hash")
        if self.state == "ready" and (
            not all(c.verified for c in self.chunks) or self.published_at is None
        ):
            raise ValueError("Ready requires all expected chunks verified and publication evidence")
        if self.state in {"building", "failed"} and self.published_at is not None:
            raise ValueError("unpublished builds cannot claim a publication timestamp")
        return self


class MemoryRecord(ContractModel):
    """Persisted authority; legacy MemorySnapshot remains the inline read DTO."""

    ref: MemoryRef
    revision: Positive
    relations_revision: Positive
    kind: MemoryKind
    status: MemoryStatus
    body_location: ResourceLocation
    body_chars: Positive
    sources: tuple[SourceRef, ...] = Field(min_length=1)
    projection_state: ProjectionState
    projection: ProjectionManifest | None = None
    cache_location: ResourceLocation | None = None
    expires_at: Timestamp | None = None
    created_at: Timestamp

    @model_validator(mode="after")
    def authoritative_bindings(self) -> Self:
        if self.body_location.kind != "body":
            raise ValueError("MemoryRecord requires a body address")
        if self.cache_location is not None and (
            self.cache_location.kind != "cache"
            or self.cache_location.content_hash != self.body_location.content_hash
            or self.cache_location.generation != self.body_location.generation
        ):
            raise ValueError("cache must bind the same body generation and hash")
        if self.expires_at is not None and self.expires_at <= self.created_at:
            raise ValueError("expiry must follow creation")
        if self.projection is not None and (
            self.projection.memory != self.ref
            or self.projection.body_hash != self.body_location.content_hash
            or self.projection.state != self.projection_state.value
            or any(c.end_char > self.body_chars for c in self.projection.chunks)
        ):
            raise ValueError("projection does not describe this exact Memory body")
        if self.projection_state == ProjectionState.READY and (
            self.projection is None or self.status != MemoryStatus.ACTIVE
        ):
            raise ValueError("Ready requires a published manifest on an active Memory")
        return self


class GuardStamp(ContractModel):
    memory: MemoryRef
    object_revision: Positive
    relations_revision: Positive
    authorization_epoch: Positive
    body_hash: Digest
    checked_at: Timestamp


class FullBodyReadResult(ContractModel):
    memory: MemoryRef
    outcome: Literal["read", "excluded", "missing", "unavailable", "stale"]
    content: NonEmpty | None = None
    sources: tuple[SourceRef, ...] = ()
    location: ResourceLocation | None = None
    guard: GuardStamp | None = None
    path: Literal["cache", "authority", "p2", "none"]
    reason_code: Identifier

    @model_validator(mode="after")
    def full_body_evidence(self) -> Self:
        if self.outcome == "read":
            if (
                self.content is None
                or not self.sources
                or self.location is None
                or self.guard is None
                or self.guard.memory != self.memory
                or self.path == "none"
            ):
                raise ValueError("read requires complete content, sources and exact qualification")
            expected_kind = "cache" if self.path == "cache" else "body"
            if (
                self.location.kind != expected_kind
                or sha256(self.content.encode("utf-8")).hexdigest() != self.guard.body_hash
                or self.location.content_hash != self.guard.body_hash
            ):
                raise ValueError("body read must match authoritative hash and typed location")
        elif self.content is not None or self.sources or self.location or self.guard:
            raise ValueError("unsuccessful read must not expose content or qualification")
        elif self.path != "none":
            raise ValueError("unsuccessful read has no successful read path")
        return self


class ReferenceHandoff(ContractModel):
    """C proposes; B atomically compares revision/location before accepting."""

    operation_id: Identifier
    action_id: Identifier
    memory: MemoryRef
    expected_object_revision: Positive
    old_location: ResourceLocation
    new_location: ResourceLocation
    read_proof_ref: RecordRef
    verified_at: Timestamp

    @model_validator(mode="after")
    def equivalent_resource(self) -> Self:
        if (
            self.old_location == self.new_location
            or self.old_location.kind not in {"body", "vector", "cache"}
            or self.old_location.kind != self.new_location.kind
            or self.old_location.content_hash != self.new_location.content_hash
            or self.old_location.generation != self.new_location.generation
        ):
            raise ValueError("handoff must move the same typed, versioned content")
        if self.read_proof_ref.scope != self.memory.scope:
            raise ValueError("handoff proof must belong to the Memory scope")
        return self


class ReferenceHandoffReceipt(ContractModel):
    request: ReferenceHandoff
    state: Literal["applied", "rejected", "unknown"]
    resulting_revision: Positive | None = None
    reason_code: Identifier

    @model_validator(mode="after")
    def conditional_revision(self) -> Self:
        if self.state == "applied":
            if self.resulting_revision != self.request.expected_object_revision + 1:
                raise ValueError("applied handoff requires the CAS successor revision")
        elif self.resulting_revision is not None:
            raise ValueError("unconfirmed handoff cannot claim a committed revision")
        return self


class ProtectionReference(ContractModel):
    """C owns active execution protection; B deletion asks C to release it."""

    protection_id: Identifier
    memory: MemoryRef
    location: ResourceLocation
    owner_task_id: Identifier
    owner_action_id: Identifier
    revision: Positive
    state: Literal["active", "released"]
    acquired_at: Timestamp
    expires_at: Timestamp
    released_at: Timestamp | None = None

    @model_validator(mode="after")
    def protection_lifecycle(self) -> Self:
        if self.expires_at <= self.acquired_at:
            raise ValueError("protection must have a reconciliation deadline")
        if (self.state == "released") != (self.released_at is not None):
            raise ValueError("released state requires a release timestamp")
        if self.released_at is not None and self.released_at < self.acquired_at:
            raise ValueError("release precedes acquisition")
        return self


class CleanupRequest(ContractModel):
    operation_id: Identifier
    memory: MemoryRef
    expected_object_revision: Positive
    blocked_at: Timestamp
    targets: tuple[ResourceLocation, ...] = Field(min_length=1)
    protection_ids: tuple[Identifier, ...]
    deadline_at: Timestamp

    @model_validator(mode="after")
    def exact_targets(self) -> Self:
        if len({x.model_dump_json() for x in self.targets}) != len(self.targets):
            raise ValueError("cleanup targets must be distinct")
        if len(set(self.protection_ids)) != len(self.protection_ids):
            raise ValueError("duplicate protection reference")
        if self.deadline_at <= self.blocked_at:
            raise ValueError("cleanup deadline must follow eligibility blocking")
        return self


class CleanupTargetResult(ContractModel):
    target: ResourceLocation
    outcome: Literal["absent", "deleted", "failed", "unknown", "protected"]
    evidence_ref: RecordRef | None = None
    reason_code: Identifier

    @model_validator(mode="after")
    def deletion_proof(self) -> Self:
        if self.outcome in {"absent", "deleted"} and self.evidence_ref is None:
            raise ValueError("cleanup success requires provider confirmation")
        return self


class CleanupReceipt(ContractModel):
    request: CleanupRequest
    state: Literal["completed", "partial_failure", "unknown"]
    released_protection_ids: tuple[Identifier, ...]
    results: tuple[CleanupTargetResult, ...] = Field(min_length=1)
    observed_at: Timestamp

    @model_validator(mode="after")
    def exact_cleanup_coverage(self) -> Self:
        targets = {x.model_dump_json() for x in self.request.targets}
        actual = [x.target.model_dump_json() for x in self.results]
        if len(set(actual)) != len(actual) or set(actual) != targets:
            raise ValueError("receipt must account for every requested target exactly once")
        released = set(self.released_protection_ids)
        if len(released) != len(self.released_protection_ids) or not released <= set(
            self.request.protection_ids
        ):
            raise ValueError("release receipt names duplicate or unrelated protection")
        complete = all(x.outcome in {"absent", "deleted"} for x in self.results)
        complete = complete and released == set(self.request.protection_ids)
        if (self.state == "completed") != complete:
            raise ValueError("completed requires every target and protection release confirmed")
        if any(x.outcome == "unknown" for x in self.results) and self.state != "unknown":
            raise ValueError("unknown provider effects must remain visible")
        if self.observed_at < self.request.blocked_at:
            raise ValueError("cleanup evidence precedes eligibility blocking")
        return self


class ChunkProjectionRequest(ContractModel):
    operation_id: Identifier
    manifest: ProjectionManifest
    chunk_index: Count
    vector: tuple[float, ...] = Field(min_length=1)
    deadline_at: Timestamp

    @model_validator(mode="after")
    def exact_chunk_projection(self) -> Self:
        if self.manifest.state != "building":
            raise ValueError("projection writes require an unpublished build generation")
        if self.chunk_index >= self.manifest.expected_chunk_count:
            raise ValueError("chunk must be declared in the build manifest")
        if len(self.vector) != self.manifest.dimensions:
            raise ValueError("projection vector does not match the model space dimensions")
        return self


class ChunkProjectionResult(ContractModel):
    request: ChunkProjectionRequest
    state: Literal["accepted", "verified", "failed", "unknown", "absent"]
    payload_matches: bool
    searchable: bool
    observed_at: Timestamp
    reason_code: Identifier

    @model_validator(mode="after")
    def generation_evidence(self) -> Self:
        if self.state == "verified" and not (self.payload_matches and self.searchable):
            raise ValueError("verified requires exact generation payload and search visibility")
        if self.state == "absent" and (self.payload_matches or self.searchable):
            raise ValueError("absent chunk cannot claim a matching searchable payload")
        return self
