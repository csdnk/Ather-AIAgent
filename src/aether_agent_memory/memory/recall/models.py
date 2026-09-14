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
    CandidateDecision,
    ContractModel,
    ExecutionState,
    Hash,
    Identifier,
    LogicalSource,
    MutableOnlineHeader,
    OnlineHeader,
    PositiveInt,
    ReasonCode,
    RecordHeader,
    RetrievalMode,
    Scope,
    Sensitivity,
    SourceEligibility,
    Stage,
    TerminalState,
    Text,
    Timestamp,
    UInt,
    hash_json,
)
from aether_agent_memory.runtime.recall_values import (
    CandidateExclusion,
    RetrievalConstraints,
    SourceCoverage,
)


class RecallRequest(OnlineHeader):
    """Recall data dictionary section 1: RecallRequest."""

    schema_version: Literal["recall-request-0.2"]
    request_ref: Identifier
    query: Text
    retrieval_mode: RetrievalMode
    source_selection: SourceSelection
    scope: Scope
    principal_ref: Identifier
    authorization_ref: Identifier
    idempotency_key: Identifier
    request_fingerprint: Hash
    deadline_at: Timestamp
    token_budget: UInt
    tokenizer_id: Identifier
    tokenizer_version: Identifier
    template_version: Identifier
    retrieval_constraints: RetrievalConstraints
    retrieval_space_ref: Identifier | None
    policy_version: Identifier

    @model_validator(mode="after")
    def bound_request(self) -> Self:
        sources = self.retrieval_constraints.allowed_sources
        expected = {
            "working_only": ["working"],
            "long_term_only": ["long_term"],
            "combined": ["working", "long_term"],
        }[self.retrieval_mode]
        selected = []
        if self.source_selection.working_eligibility == "eligible":
            selected.append("working")
        if self.source_selection.long_term_eligibility == "eligible":
            selected.append("long_term")
        if sources != expected or sources != selected or self.tenant_id != self.scope.tenant_id:
            raise ValueError("scope, source selection and retrieval mode disagree")
        if self.token_budget == 0 or not self.query.strip():
            raise ValueError("query and token budget must be nonempty")
        if (self.retrieval_mode == "working_only") != (self.retrieval_space_ref is None):
            raise ValueError("long-term recall requires its fixed retrieval space")
        if "working" in sources and not (self.scope.session_id or self.scope.task_id):
            raise ValueError("Working needs an authorized current session or task")
        fields = {
            "query",
            "scope",
            "retrieval_mode",
            "source_selection",
            "retrieval_constraints",
            "token_budget",
            "tokenizer_id",
            "tokenizer_version",
            "template_version",
            "retrieval_space_ref",
            "policy_version",
        }
        if self.request_fingerprint != hash_json(self.model_dump(mode="json", include=fields)):
            raise ValueError("request_fingerprint does not match request semantics")
        return self


class SourceSelection(ContractModel):
    """Recall data dictionary section 1: SourceSelection."""

    strategy: Literal["scope_union_v1"]
    working_eligibility: SourceEligibility
    long_term_eligibility: Literal["eligible", "not_authorized", "type_filtered"]


class RecallRequestIndex(RecordHeader):
    """Recall data dictionary section 2: RecallRequestIndex."""

    schema_version: Literal["recall-data-0.2"]
    principal_ref: Identifier
    scope_digest: Hash
    idempotency_key: Identifier
    request_fingerprint: Hash
    recall_id: Identifier
    request_ref: Identifier


class RecallExecution(MutableOnlineHeader):
    """Recall data dictionary section 3: RecallExecution."""

    schema_version: Literal["recall-data-0.2"]
    state: ExecutionState
    request_ref: Identifier
    policy_version: Identifier
    execution_deadline_at: Timestamp
    checkpoint_refs: dict[Stage, Identifier]
    lease_owner: Identifier | None
    lease_until: Timestamp | None
    lease_token: Identifier | None
    source_coverage: SourceCoverage
    excluded_candidates: list[CandidateExclusion]
    degradation_reasons: list[RecallReason]
    confirmed_empty: Boolean
    fatal_reason: ReasonCode | None
    result_ref: Identifier | None
    result_digest: Hash | None
    final_validation_at: Timestamp | None
    completed_at: Timestamp | None
    last_event_sequence: UInt
    source_result_refs: dict[LogicalSource, Identifier]
    query_embedding_call: EmbeddingCallBinding | None
    read_ledger: RecallReadLedger
    final_validation: FinalValidationBatch | None
    finalization: RecallFinalization
    retention: RecallRetention


class RecallReason(ContractModel):
    """Recall data dictionary section 3: RecallReason."""

    reason: ReasonCode
    affected_ref: Identifier | None


class EmbeddingCallBinding(ContractModel):
    """Recall data dictionary section 3: EmbeddingCallBinding."""

    caller_ref: Identifier
    caller_request_ref: Identifier
    embedding_request_ref: Identifier | None


class RecallReadLedger(ContractModel):
    """Recall data dictionary section 3: RecallReadLedger."""

    attempts: list[RecallReadAttempt]
    bytes_charged: UInt
    bytes_reserved: UInt
    admission_cursors: dict[Stage, UInt]

    @model_validator(mode="after")
    def accounting(self) -> Self:
        if len({a.attempt_id for a in self.attempts}) != len(self.attempts):
            raise ValueError("duplicate read attempt")
        if self.bytes_reserved != sum(
            a.reserved_bytes for a in self.attempts if a.state == "reserved"
        ):
            raise ValueError("reserved byte total does not match attempts")
        if self.bytes_charged != sum(
            a.charged_bytes for a in self.attempts if a.state != "reserved"
        ):
            raise ValueError("charged byte total does not match attempts")
        return self


class RecallReadAttempt(ContractModel):
    """Recall data dictionary section 3: RecallReadAttempt."""

    attempt_id: Identifier
    call_key: Hash
    stage: Stage
    capability: Literal[
        "authorization_read",
        "query_embedding_attach",
        "working_read",
        "vector_search",
        "memory_read",
        "canonical_read",
        "prewarm_read",
        "final_revalidate",
    ]
    logical_target: Identifier
    input_digest: Hash
    attempt_no: UInt
    state: Literal["reserved", "settled", "uncertain"]
    reserved_bytes: UInt
    received_bytes: UInt | None
    charged_bytes: UInt
    reserved_at: Timestamp
    call_deadline_at: Timestamp
    closed_at: Timestamp | None
    output_ref: Identifier | None

    @model_validator(mode="after")
    def accounting(self) -> Self:
        if self.attempt_no == 0:
            raise ValueError("attempt numbers start at one")
        if self.state == "reserved":
            valid = (
                self.received_bytes is None and self.charged_bytes == 0 and self.closed_at is None
            )
        elif self.state == "settled":
            valid = (
                self.received_bytes is not None
                and self.received_bytes <= self.reserved_bytes
                and self.charged_bytes == self.received_bytes
                and self.closed_at is not None
            )
        else:
            valid = (
                self.received_bytes is None
                and self.charged_bytes == self.reserved_bytes
                and self.closed_at is not None
            )
        if not valid:
            raise ValueError("invalid read reservation or settlement")
        return self


class FinalValidationBatch(ContractModel):
    """Recall data dictionary section 3: FinalValidationBatch."""

    candidate_ids: list[Identifier]
    items: list[FinalValidationItem]
    request_authorization_evidence_ref: Identifier | None
    request_authorization_valid_until: Timestamp | None
    started_at: Timestamp
    completed_at: Timestamp | None


class FinalValidationItem(ContractModel):
    """Recall data dictionary section 3: FinalValidationItem."""

    candidate_id: Identifier
    decision: CandidateDecision
    evidence_ref: Identifier | None
    validated_at: Timestamp | None
    valid_until: Timestamp | None
    reason_codes: list[ReasonCode]


class RecallFinalization(ContractModel):
    """Recall data dictionary section 3: RecallFinalization."""

    commit_id: Identifier
    generation: UInt
    phase: Literal["idle", "prepared", "commit_unknown", "committed", "attention_required"]
    draft_ref: Identifier | None
    draft_digest: Hash | None
    proposed_state: TerminalState | None
    publish_before: Timestamp | None
    recovery_deadline_at: Timestamp
    attempts_reserved: UInt
    last_call: FinalizationCallIntent | None
    recovery_not_before: Timestamp | None
    commit_evidence_ref: Identifier | None


class FinalizationCallIntent(ContractModel):
    """Recall data dictionary section 3: FinalizationCallIntent."""

    sequence: UInt
    kind: Literal["commit_draft", "query_commit", "fence_and_finalize_failure"]
    generation: UInt
    reserved_at: Timestamp
    deadline_at: Timestamp


class RecallRetention(ContractModel):
    """Recall data dictionary section 3: RecallRetention."""

    payload_retain_until: Timestamp | None
    metadata_retain_until: Timestamp
    payload_state: Literal["retained", "cleared"]
    payload_cleared_at: Timestamp | None


class RecallCheckpoint(OnlineHeader):
    """Recall data dictionary section 4: RecallCheckpoint."""

    schema_version: Literal["recall-data-0.2"]
    checkpoint_ref: Identifier
    stage: Stage
    input_digest: Hash
    output_ref: Identifier
    output_digest: Hash
    completed_at: Timestamp
    source_schema_versions: dict[Identifier, Identifier]
    policy_version: Identifier
    attempt: UInt
    sensitivity: Sensitivity


class QueryEmbeddingResult(OnlineHeader):
    """Recall data dictionary section 5: QueryEmbeddingResult."""

    schema_version: Literal["recall-data-0.2"]
    embedding_result_ref: Identifier
    vector_ref: Identifier
    usage: Literal["Query"]
    model_id: Identifier
    model_version: Identifier
    dimension: PositiveInt
    dtype: Identifier
    embedding_schema_version: Identifier
    source_hash: Hash
    retrieval_space_ref: Identifier
    external_contract_ref: Identifier
    source_evidence_ref: Identifier
    validated_at: Timestamp


# Resolve forward references after all nested value types are defined.
EmbeddingCallBinding.model_rebuild()
FinalValidationBatch.model_rebuild()
FinalValidationItem.model_rebuild()
FinalizationCallIntent.model_rebuild()
QueryEmbeddingResult.model_rebuild()
RecallCheckpoint.model_rebuild()
RecallExecution.model_rebuild()
RecallFinalization.model_rebuild()
RecallReadAttempt.model_rebuild()
RecallReadLedger.model_rebuild()
RecallReason.model_rebuild()
RecallRequest.model_rebuild()
RecallRequestIndex.model_rebuild()
RecallRetention.model_rebuild()
SourceSelection.model_rebuild()
