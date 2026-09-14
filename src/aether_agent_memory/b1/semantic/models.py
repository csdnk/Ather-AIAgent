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
    ContractModel,
    EmbeddingErrorCode,
    EmbeddingExecutionState,
    EmbeddingUsage,
    Hash,
    Identifier,
    PositiveInt,
    RecordHeader,
    Timestamp,
    UInt,
    hash_json,
)


class SemanticEmbeddingRequest(RecordHeader):
    """Recall data dictionary section 19: SemanticEmbeddingRequest."""

    schema_version: Literal["embedding-data-0.1"]
    embedding_request_id: Identifier
    caller_ref: Identifier
    caller_request_ref: Identifier
    trace_id: Identifier
    authorization_ref: Identifier
    usage: EmbeddingUsage
    input_ref: Identifier
    source_hash: Hash
    input_binding_digest: Hash
    input_binding_ref: Identifier
    model_binding: EmbeddingModelBinding
    deadline_at: Timestamp
    execution_policy_ref: Identifier
    reuse_digest: Hash

    @model_validator(mode="after")
    def bound_input(self) -> Self:
        if (
            self.source_hash.algorithm != "SHA-256"
            or self.source_hash.byte_encoding != "utf-8"
            or self.source_hash.range is not None
        ):
            raise ValueError("source_hash must cover the complete original UTF-8 input")
        expected = hash_json(
            self.model_dump(
                mode="json",
                include={
                    "tenant_id",
                    "usage",
                    "source_hash",
                    "input_binding_digest",
                    "model_binding",
                },
            )
        )
        if self.reuse_digest != expected:
            raise ValueError("reuse_digest does not match the fixed input/model binding")
        if self.deadline_at <= self.created_at:
            raise ValueError("embedding deadline must follow creation")
        return self


class EmbeddingModelBinding(ContractModel):
    """Recall data dictionary section 19: EmbeddingModelBinding."""

    model_id: Identifier
    model_version: Identifier
    dimension: PositiveInt
    dtype: Identifier
    embedding_schema_version: Identifier
    preprocessing_version: Identifier
    retrieval_space_ref: Identifier
    model_contract_ref: Identifier


class SemanticEmbeddingResult(RecordHeader):
    """Recall data dictionary section 20: SemanticEmbeddingResult."""

    schema_version: Literal["embedding-data-0.1"]
    embedding_result_id: Identifier
    request_ref: Identifier
    usage: EmbeddingUsage
    model_binding: EmbeddingModelBinding
    source_hash: Hash
    input_binding_digest: Hash
    vector_ref: Identifier
    vector_hash: Hash
    validation_evidence_ref: Identifier
    validated_at: Timestamp


class SemanticEmbeddingExecution(RecordHeader):
    """Recall data dictionary section 25: SemanticEmbeddingExecution."""

    schema_version: Literal["embedding-data-0.1"]
    embedding_request_id: Identifier
    request_ref: Identifier
    caller_ref: Identifier
    caller_request_ref: Identifier
    state: EmbeddingExecutionState
    attempts_reserved: UInt
    deadline_at: Timestamp
    execution_policy_ref: Identifier
    result_ref: Identifier | None
    error_code: EmbeddingErrorCode | None
    lease_owner: Identifier | None
    lease_until: Timestamp | None
    lease_token: Identifier | None
    state_version: UInt

    @model_validator(mode="after")
    def state_consistency(self) -> Self:
        if (self.state == "succeeded") != (self.result_ref is not None):
            raise ValueError("only successful executions have a result reference")
        if (self.state == "failed") != (self.error_code is not None):
            raise ValueError("only failed executions have a final error")
        leases = (self.lease_owner, self.lease_until, self.lease_token)
        if any(v is not None for v in leases) and not all(v is not None for v in leases):
            raise ValueError("lease owner, deadline and token must be bound together")
        if self.state == "running" and (self.lease_token is None or self.attempts_reserved == 0):
            raise ValueError("running compute requires a durable lease and attempt reservation")
        return self


# Resolve forward references after all nested value types are defined.
EmbeddingModelBinding.model_rebuild()
SemanticEmbeddingExecution.model_rebuild()
SemanticEmbeddingRequest.model_rebuild()
SemanticEmbeddingResult.model_rebuild()
