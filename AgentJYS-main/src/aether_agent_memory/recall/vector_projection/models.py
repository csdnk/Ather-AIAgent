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

from aether_agent_memory.runtime.contract_types import (
    Boolean,
    ByteRange,
    ContractModel,
    Hash,
    Identifier,
    ProjectionCallKind,
    ProjectionCoordinatorState,
    ProjectionErrorCode,
    ProjectionIdentity,
    ProjectionOperationKind,
    ProjectionProviderState,
    ProjectionRetryAdvice,
    RecordHeader,
    Scope,
    Timestamp,
    UInt,
    hash_json,
)


class VectorProjectionRequest(RecordHeader):
    """Recall data dictionary section 21: VectorProjectionRequest."""

    schema_version: Literal["projection-data-0.2"]
    projection_request_id: Identifier
    caller_ref: Identifier
    caller_request_ref: Identifier
    trace_id: Identifier
    authorization_ref: Identifier
    owner_evidence_ref: Identifier
    operation_kind: ProjectionOperationKind
    build_intent: Literal["initial_or_retry", "rebuild"] | None
    identity: ProjectionIdentity
    provider_ref: Identifier
    retrieval_space_ref: Identifier
    payload: ProjectionPayload | None
    wait_deadline_at: Timestamp
    execution_policy_ref: Identifier
    request_fingerprint: Hash

    @model_validator(mode="after")
    def action_binding(self) -> Self:
        if self.build_intent == "rebuild":
            raise ValueError("PROJECTION_REBUILD_UNSUPPORTED")
        if self.operation_kind == "upsert":
            if self.payload is None or self.build_intent != "initial_or_retry":
                raise ValueError("upsert needs an approved payload and build intent")
            if self.payload.metadata.scope.tenant_id != self.tenant_id:
                raise ValueError("projection metadata crosses tenant boundary")
        elif self.payload is not None or self.build_intent is not None:
            raise ValueError("delete must not carry a vector payload or build intent")
        return self


class ProjectionPayload(ContractModel):
    """Recall data dictionary section 21: ProjectionPayload."""

    embedding_result_ref: Identifier
    representation_id: Identifier
    content_ref: Identifier
    content_version: Identifier
    approved_range: ByteRange
    content_evidence_ref: Identifier
    metadata: ProjectionMetadata
    metadata_hash: Hash

    @model_validator(mode="after")
    def bound_metadata(self) -> Self:
        view = self.metadata.model_dump(mode="json", exclude={"owner_metadata_evidence_ref"})
        if self.metadata_hash != hash_json(view):
            raise ValueError("metadata_hash covers exactly scope, memory_type and occurred_at")
        return self


class ProjectionMetadata(ContractModel):
    """Recall data dictionary section 21: ProjectionMetadata."""

    scope: Scope
    memory_type: Literal["Episodic", "Semantic"]
    occurred_at: Timestamp | None
    owner_metadata_evidence_ref: Identifier


class ProjectionTargetBinding(RecordHeader):
    """Recall data dictionary section 22: ProjectionTargetBinding."""

    schema_version: Literal["projection-data-0.2"]
    target_binding_id: Identifier
    target_digest: Hash
    identity: ProjectionIdentity
    provider_ref: Identifier
    retrieval_space_ref: Identifier
    upsert_operation_id: Identifier | None
    delete_operation_id: Identifier | None
    retired: Boolean
    retired_at: Timestamp | None
    retirement_evidence_ref: Identifier | None
    state_version: UInt


class VectorProjectionOperation(RecordHeader):
    """Recall data dictionary section 23: VectorProjectionOperation."""

    schema_version: Literal["projection-data-0.2"]
    operation_id: Identifier
    operation_key: Hash
    request_ref: Identifier
    target_ref: Identifier
    request_fingerprint: Hash
    provider_idempotency_key: Identifier
    provider_operation_ref: Identifier | None
    coordinator_state: ProjectionCoordinatorState
    execution_policy_ref: Identifier
    accepted_at: Timestamp
    reconcile_until: Timestamp
    mutation_attempts_reserved: UInt
    query_attempts_reserved: UInt
    pending_call: ProjectionCallIntent | None
    next_attempt_at: Timestamp | None
    latest_result_ref: Identifier | None
    latest_result_version: UInt
    attention_reason: ProjectionErrorCode | None
    lease_owner: Identifier | None
    lease_until: Timestamp | None
    lease_token: Identifier | None
    state_version: UInt


class ProjectionCallIntent(ContractModel):
    """Recall data dictionary section 23: ProjectionCallIntent."""

    call_id: Identifier
    call_kind: ProjectionCallKind
    reserved_at: Timestamp
    lease_token: Identifier


class ProviderResult(RecordHeader):
    """Recall data dictionary section 24: ProviderResult."""

    schema_version: Literal["projection-data-0.2"]
    result_id: Identifier
    operation_id: Identifier
    observation_version: UInt
    operation_kind: ProjectionOperationKind
    identity: ProjectionIdentity
    provider_ref: Identifier
    retrieval_space_ref: Identifier
    state: ProjectionProviderState
    raw_status: Identifier | None
    provider_operation_ref: Identifier | None
    physical_target_ref: Identifier | None
    object_present: Boolean | None
    index_queryable: Boolean | None
    binding_evidence_ref: Identifier | None
    completion_evidence_ref: Identifier | None
    delete_confirmed: Boolean | None
    late_write_barrier_confirmed: Boolean | None
    error_code: ProjectionErrorCode | None
    retry_advice: ProjectionRetryAdvice
    safe_resubmit_evidence_ref: Identifier | None
    evidence_refs: list[Identifier]
    observed_at: Timestamp
    result_digest: Hash

    @model_validator(mode="after")
    def mechanism_facts(self) -> Self:
        if self.observation_version == 0:
            raise ValueError("observation versions start at one")
        if self.state == "READY":
            if (
                not self.completion_evidence_ref
                or not self.evidence_refs
                or self.error_code is not None
                or self.retry_advice != "none"
            ):
                raise ValueError("READY requires completion evidence and no mechanism error")
            if self.operation_kind == "upsert":
                if (
                    self.object_present is not True
                    or self.index_queryable is not True
                    or not self.binding_evidence_ref
                ):
                    raise ValueError("upsert READY requires an accurately bound, queryable object")
            elif self.delete_confirmed is not True or self.late_write_barrier_confirmed is not True:
                raise ValueError("delete READY requires deletion and late-write barrier evidence")
        if self.operation_kind == "upsert" and self.delete_confirmed is not None:
            raise ValueError("delete confirmation is not applicable to upsert")
        if self.state in ("FAILED", "UNKNOWN") and self.error_code is None:
            raise ValueError("FAILED/UNKNOWN requires a mechanism error code")
        if (self.retry_advice == "safe_resubmit") != (self.safe_resubmit_evidence_ref is not None):
            raise ValueError("safe resubmit requires independent evidence")
        if self.result_digest != hash_json(self.model_dump(mode="json", exclude={"result_digest"})):
            raise ValueError("result_digest mismatch")
        return self


# Resolve forward references after all nested value types are defined.
ProjectionCallIntent.model_rebuild()
ProjectionMetadata.model_rebuild()
ProjectionPayload.model_rebuild()
ProjectionTargetBinding.model_rebuild()
ProviderResult.model_rebuild()
VectorProjectionOperation.model_rebuild()
VectorProjectionRequest.model_rebuild()
