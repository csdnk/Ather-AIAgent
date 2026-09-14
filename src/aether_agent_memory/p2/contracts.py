"""First-batch document contracts. Fields are required, including nullable fields.

Source: Recall data dictionary and P2 interface requirements, 2026-09-07.
No external provider wire compatibility is implied by these logical models.
"""

from __future__ import annotations

from typing import (
    Literal,
    Self,
)

from pydantic import model_validator

from aether_agent_memory.b1.semantic.models import (
    EmbeddingModelBinding,
)
from aether_agent_memory.runtime.contract_types import (
    Boolean,
    ByteRange,
    ContractModel,
    Hash,
    Identifier,
    Number,
    P2OperationStatus,
    PositiveInt,
    ProjectionIdentity,
    Scope,
    Text,
    Timestamp,
    UInt,
    hash_bytes,
    hash_json,
    vector_bytes,
)


class P2SearchInput(ContractModel):
    """P2 interface: P2SearchInput."""

    query_vector: list[Number]
    usage: Literal["Query"]
    model_binding: EmbeddingModelBinding
    retrieval_space_ref: Identifier
    scope: Scope
    memory_types: list[Literal["Episodic", "Semantic"]]
    occurred_after: Timestamp | None
    occurred_before: Timestamp | None
    top_k: PositiveInt

    @model_validator(mode="after")
    def search_binding(self) -> Self:
        vector_bytes(self.query_vector, self.model_binding.dimension, self.model_binding.dtype)
        if self.retrieval_space_ref != self.model_binding.retrieval_space_ref:
            raise ValueError("query vector and retrieval space disagree")
        if not self.memory_types or len(set(self.memory_types)) != len(self.memory_types):
            raise ValueError("long-term memory types must be nonempty and unique")
        if (
            self.occurred_after is not None
            and self.occurred_before is not None
            and self.occurred_after > self.occurred_before
        ):
            raise ValueError("reversed time interval")
        return self


class P2SearchResult(ContractModel):
    """P2 interface: P2SearchResult."""

    retrieval_space_ref: Identifier
    requested_k: UInt
    completion: Literal["complete", "partial", "failed"]
    hits: list[P2SearchHit]
    partial_reason: Identifier | None
    ranking_contract_ref: Identifier
    query_binding_evidence: list[P2Evidence]
    completion_evidence: list[P2Evidence]

    @model_validator(mode="after")
    def bounded_completion(self) -> Self:
        if self.requested_k == 0 or len(self.hits) > self.requested_k:
            raise ValueError("response exceeds its requested TopK bound")
        if self.completion == "partial" and self.partial_reason is None:
            raise ValueError("partial search requires a reason even with zero hits")
        if self.completion == "complete" and (
            self.partial_reason is not None or not self.completion_evidence
        ):
            raise ValueError("complete search requires completion evidence")
        return self


class P2SearchHit(ContractModel):
    """P2 interface: P2SearchHit."""

    projection_ref: Identifier
    provider_rank: PositiveInt
    raw_score: Number | None
    model_version: Identifier
    projection_schema_version: Identifier
    identity: ProjectionIdentity | None
    source_evidence: list[P2Evidence]


class P2ContentTarget(ContractModel):
    """P2 interface: P2ContentTarget."""

    content_ref: Identifier
    content_version: Identifier
    representation_id: Identifier
    representation_type: Identifier
    bucket: Identifier
    key: Identifier


class P2ContentMetadataInput(ContractModel):
    """P2 interface: P2ContentMetadataInput."""

    content: P2ContentTarget
    approved_range: ByteRange
    version_condition_ref: Identifier | None


class P2ContentMetadataResult(ContractModel):
    """P2 interface: P2ContentMetadataResult."""

    status: Literal["ok", "not_found", "version_mismatch", "failed"]
    meta: P2ContentMetadata | None
    evidence: list[P2Evidence]


class P2ContentMetadata(ContractModel):
    """P2 interface: P2ContentMetadata."""

    content_ref: Identifier
    content_version: Identifier
    representation_id: Identifier
    object_size_bytes: UInt
    content_encoding: Identifier
    etag: Text | None
    checksum: Hash | None
    version_evidence: list[P2Evidence]


class P2CachedReadInput(ContractModel):
    """P2 interface: P2CachedReadInput."""

    content_read: P2ContentReadInput
    cache_read_ref: Identifier
    fallback_owner: Literal["caller", "provider"]


class P2CacheTrace(ContractModel):
    """P2 interface: P2CacheTrace."""

    cache_read_ref: Identifier | None
    cache_outcome: Literal["hit", "miss", "invalid", "unavailable", "unknown"]
    fallback_performed: Boolean | None
    final_read_path: Literal["prewarm", "canonical", "unknown"]
    diagnostic_code: Identifier | None
    evidence: list[P2Evidence]


class P2ContentReadInput(ContractModel):
    """P2 interface: P2ContentReadInput."""

    content: P2ContentTarget
    approved_range: ByteRange
    expected_hash: Hash
    version_condition_ref: Identifier | None
    max_response_bytes: UInt

    @model_validator(mode="after")
    def bounded_read(self) -> Self:
        if self.max_response_bytes < self.approved_range.end - self.approved_range.start:
            raise ValueError("response limit cannot truncate the approved fragment")
        return self


class P2ContentReadResult(ContractModel):
    """P2 interface: P2ContentReadResult."""

    status: Literal["ok", "not_found", "version_mismatch", "failed"]
    content: P2ContentTarget
    data: bytes | None
    returned_range: ByteRange | None
    returned_bytes: UInt
    meta: P2ContentMetadata | None
    read_path: Literal["canonical", "prewarm", "unknown"]
    placement: P2ReadPlacement | None
    cache_trace: P2CacheTrace | None
    evidence: list[P2Evidence]

    @model_validator(mode="after")
    def returned_content(self) -> Self:
        if self.data is not None and len(self.data) != self.returned_bytes:
            raise ValueError("returned byte count mismatch")
        if self.status == "ok":
            if (
                self.data is None
                or self.meta is None
                or self.returned_range is None
                or not self.evidence
            ):
                raise ValueError("successful reads require data, metadata, range and evidence")
            if self.returned_range.end - self.returned_range.start != self.returned_bytes:
                raise ValueError("returned range does not cover the complete response")
            if (
                self.meta.content_ref != self.content.content_ref
                or self.meta.content_version != self.content.content_version
                or self.meta.representation_id != self.content.representation_id
            ):
                raise ValueError("read metadata is bound to another content target")
        return self


class P2ReadPlacement(ContractModel):
    """P2 interface: P2ReadPlacement."""

    actual_provider: Identifier | None
    observed_tier: Identifier | None
    placement_generation: Identifier | None
    observed_at: Timestamp | None
    producing_action_id: Identifier | None
    evidence: list[P2Evidence]


class P2CallContext(ContractModel):
    """P2 interface: P2CallContext."""

    request_ref: Identifier
    trace_id: Identifier
    tenant_id: Identifier
    provider_ref: Identifier
    contract_ref: Identifier
    deadline_at: Timestamp


class P2ResponseContext(ContractModel):
    """P2 interface: P2ResponseContext."""

    request_ref: Identifier
    provider_ref: Identifier
    contract_ref: Identifier
    provider_request_ref: Identifier | None
    observed_at: Timestamp | None
    error: P2Error | None


class P2Evidence(ContractModel):
    """P2 interface: P2Evidence."""

    kind: Identifier
    contract_ref: Identifier
    provider_evidence_ref: Identifier | None
    observed_at: Timestamp | None


class P2Error(ContractModel):
    """P2 interface: P2Error."""

    raw_code: Identifier
    category: Identifier
    message: Text
    retryable: Boolean | None
    retry_after_ms: UInt | None
    effect: Literal["no_effect", "may_have_effect", "unknown", "not_applicable"]
    evidence: list[P2Evidence]


class P2ProjectionTarget(ContractModel):
    """P2 interface: P2ProjectionTarget."""

    tenant_id: Identifier
    provider_ref: Identifier
    retrieval_space_ref: Identifier
    identity: ProjectionIdentity
    physical_target_ref: Identifier | None


class P2ProjectionPayload(ContractModel):
    """P2 interface: P2ProjectionPayload."""

    usage: Literal["Passage"]
    vector: list[Number]
    model_binding: EmbeddingModelBinding
    source_hash: Hash
    input_binding_digest: Hash
    vector_hash: Hash
    representation_id: Identifier
    content_ref: Identifier
    content_version: Identifier
    approved_range: ByteRange
    metadata: P2ProjectionMetadata
    metadata_hash: Hash

    @model_validator(mode="after")
    def payload_binding(self) -> Self:
        data = vector_bytes(self.vector, self.model_binding.dimension, self.model_binding.dtype)
        if hash_bytes(data) != self.vector_hash:
            raise ValueError("vector_hash does not match actual encoded vector")
        if hash_json(self.metadata.model_dump(mode="json")) != self.metadata_hash:
            raise ValueError("metadata_hash mismatch")
        return self


class P2ProjectionMetadata(ContractModel):
    """P2 interface: P2ProjectionMetadata."""

    scope: Scope
    memory_type: Literal["Episodic", "Semantic"]
    occurred_at: Timestamp | None


class P2UpsertInput(ContractModel):
    """P2 interface: P2UpsertInput."""

    target: P2ProjectionTarget
    provider_idempotency_key: Identifier
    request_fingerprint: Hash
    payload: P2ProjectionPayload
    precondition_ref: Identifier | None

    @model_validator(mode="after")
    def exact_target(self) -> Self:
        if (
            self.target.tenant_id != self.payload.metadata.scope.tenant_id
            or self.target.retrieval_space_ref != self.payload.model_binding.retrieval_space_ref
            or self.target.identity.model_version != self.payload.model_binding.model_version
        ):
            raise ValueError("payload and projection target binding mismatch")
        return self


class P2MutationResult(ContractModel):
    """P2 interface: P2MutationResult."""

    operation_kind: Literal["upsert", "delete"]
    target: P2ProjectionTarget
    provider_idempotency_key: Identifier
    request_fingerprint: Hash | None
    provider_operation_ref: Identifier | None
    operation_status: P2OperationStatus
    raw_status: Identifier | None
    target_state: P2TargetState | None
    resubmit_assessment: P2ResubmitAssessment | None
    evidence: list[P2Evidence]


class P2OperationQueryInput(ContractModel):
    """P2 interface: P2OperationQueryInput."""

    target: P2ProjectionTarget
    operation_kind: Literal["upsert", "delete"]
    provider_operation_ref: Identifier | None
    provider_idempotency_key: Identifier
    request_fingerprint: Hash


class P2ResubmitAssessment(ContractModel):
    """P2 interface: P2ResubmitAssessment."""

    no_effect_confirmed: Boolean | None
    no_late_effect_confirmed: Boolean | None
    same_key_resubmit_allowed: Boolean | None
    idempotency_valid_until: Timestamp | None
    evidence: list[P2Evidence]


class P2TargetQueryInput(ContractModel):
    """P2 interface: P2TargetQueryInput."""

    target: P2ProjectionTarget
    expected_request_fingerprint: Hash | None
    related_operation_ref: Identifier | None
    include_payload: Boolean


class P2TargetState(ContractModel):
    """P2 interface: P2TargetState."""

    target: P2ProjectionTarget
    object_present: Boolean | None
    stored_request_fingerprint: Hash | None
    stored_payload: P2ProjectionPayload | None
    durable: Boolean | None
    index_queryable: Boolean | None
    delete_confirmed: Boolean | None
    late_write_barrier_confirmed: Boolean | None
    binding_evidence: list[P2Evidence]
    completion_evidence: list[P2Evidence]
    observed_at: Timestamp | None


class P2DeleteInput(ContractModel):
    """P2 interface: P2DeleteInput."""

    target: P2ProjectionTarget
    provider_idempotency_key: Identifier
    request_fingerprint: Hash
    precondition_ref: Identifier | None
    related_upsert_keys: list[Identifier]


# Resolve forward references after all nested value types are defined.
P2CacheTrace.model_rebuild()
P2CachedReadInput.model_rebuild()
P2CallContext.model_rebuild()
P2ContentMetadata.model_rebuild()
P2ContentMetadataInput.model_rebuild()
P2ContentMetadataResult.model_rebuild()
P2ContentReadInput.model_rebuild()
P2ContentReadResult.model_rebuild()
P2ContentTarget.model_rebuild()
P2DeleteInput.model_rebuild()
P2Error.model_rebuild()
P2Evidence.model_rebuild()
P2MutationResult.model_rebuild()
P2OperationQueryInput.model_rebuild()
P2ProjectionMetadata.model_rebuild()
P2ProjectionPayload.model_rebuild()
P2ProjectionTarget.model_rebuild()
P2ReadPlacement.model_rebuild()
P2ResponseContext.model_rebuild()
P2ResubmitAssessment.model_rebuild()
P2SearchHit.model_rebuild()
P2SearchInput.model_rebuild()
P2SearchResult.model_rebuild()
P2TargetQueryInput.model_rebuild()
P2TargetState.model_rebuild()
P2UpsertInput.model_rebuild()
