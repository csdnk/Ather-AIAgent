"""Typed retrieval, compute and context contracts. Validators do not run retrieval."""

from __future__ import annotations

from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from aether_agent_memory.remember.contracts.models import ConflictGroup, MemoryRef, SourceRef
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


class RecallRequest(ContractModel):
    query: NonEmpty
    selection: ScopeSelector
    sources: Literal["auto", "working", "long_term", "both"] = "auto"
    token_budget: Positive = 1024


class Coverage(ContractModel):
    working: Literal["complete", "partial", "unavailable", "not_requested"]
    long_term: Literal["complete", "partial", "unavailable", "not_requested"]


class ContextItem(ContractModel):
    memory: MemoryRef
    content: NonEmpty
    sources: tuple[SourceRef, ...] = Field(min_length=1)
    representation: Literal["original", "compressed"]
    artifact_id: Identifier | None = None

    @model_validator(mode="after")
    def compressed_provenance(self) -> Self:
        if self.representation == "compressed" and self.artifact_id is None:
            raise ValueError("compressed content requires artifact provenance")
        return self


class ContextGroup(ContractModel):
    group_id: Identifier
    items: tuple[ContextItem, ...] = Field(min_length=1)
    conflict: ConflictGroup | None = None

    @model_validator(mode="after")
    def complete_conflict(self) -> Self:
        refs = {item.memory.model_dump_json() for item in self.items}
        if len(refs) != len(self.items):
            raise ValueError("duplicate memory version inside a group")
        if self.conflict is not None:
            if refs != {member.model_dump_json() for member in self.conflict.members}:
                raise ValueError("a conflict must be delivered as a complete group")
        elif len(self.items) != 1:
            raise ValueError("non-conflict groups contain one complete item")
        return self


class ContextPack(ContractModel):
    schema_version: Literal["p3/1"] = "p3/1"
    recall_id: Identifier
    scope: Scope
    outcome: Literal["available", "empty", "degraded"]
    selected_sources: tuple[Literal["working", "long_term"], ...] = Field(min_length=1)
    coverage: Coverage
    groups: tuple[ContextGroup, ...]
    rendered_context: str
    token_budget: Positive
    tokens_used: Count
    tokenizer_id: Identifier
    policy_version: Identifier
    degradation_reasons: tuple[NonEmpty, ...]
    committed_at: Timestamp

    @model_validator(mode="after")
    def honest_outcome(self) -> Self:
        selected = set(self.selected_sources)
        if len(selected) != len(self.selected_sources):
            raise ValueError("duplicate selected source")
        for name in ("working", "long_term"):
            value = getattr(self.coverage, name)
            if (name in selected) == (value == "not_requested"):
                raise ValueError("coverage must match the chosen sources")
        incomplete = any(getattr(self.coverage, name) != "complete" for name in selected)
        if self.tokens_used > self.token_budget:
            raise ValueError("rendered context exceeds budget")
        refs = [item.memory for group in self.groups for item in group.items]
        if any(ref.scope.tenant_id != self.scope.tenant_id for ref in refs):
            raise ValueError("context cannot contain another tenant's content")
        if len({ref.model_dump_json() for ref in refs}) != len(refs):
            raise ValueError("memory version appears in more than one group")
        if self.outcome == "empty":
            if self.groups or self.rendered_context or self.tokens_used:
                raise ValueError("empty must have zero content")
        elif not self.groups or not self.rendered_context or self.tokens_used == 0:
            raise ValueError("usable context requires content")
        if self.outcome == "degraded":
            if not self.degradation_reasons:
                raise ValueError("degraded requires an explanation")
        elif incomplete or self.degradation_reasons:
            raise ValueError("available/empty cannot hide missing evidence")
        return self


class RecallRecord(ContractModel):
    recall_id: Identifier
    scope: Scope
    state: Literal["accepted", "running", "completed", "failed"]
    stage: Literal[
        "validate", "select", "embed", "discover", "load", "rank", "rerank", "assemble", "finalize"
    ]
    revision: Positive
    deadline_at: Timestamp
    result_available: bool
    reason: NonEmpty | None = None

    @model_validator(mode="after")
    def completed_only(self) -> Self:
        if self.result_available and self.state != "completed":
            raise ValueError("unfinished recall cannot expose a result")
        return self


class EmbeddingRequest(ContractModel):
    operation_id: Identifier
    usage: Literal["query", "passage"]
    texts: tuple[NonEmpty, ...] = Field(min_length=1)
    model_space: Identifier
    deadline_at: Timestamp

    @model_validator(mode="after")
    def query_cardinality(self) -> Self:
        if self.usage == "query" and len(self.texts) != 1:
            raise ValueError("query embedding accepts one query")
        return self


class EmbeddingItem(ContractModel):
    index: Count
    input_hash: Digest
    vector: tuple[float, ...] = Field(min_length=1)


class EmbeddingResult(ContractModel):
    operation_id: Identifier
    usage: Literal["query", "passage"]
    model_space: Identifier
    dimensions: Positive
    items: tuple[EmbeddingItem, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def vector_shape(self) -> Self:
        if sorted(item.index for item in self.items) != list(range(len(self.items))):
            raise ValueError("indices must cover each input exactly once")
        if any(len(item.vector) != self.dimensions for item in self.items):
            raise ValueError("dimension mismatch")
        return self


class ProjectionTarget(ContractModel):
    memory: MemoryRef
    model_space: Identifier
    chunk_index: Count
    vector_id: Digest
    input_hash: Digest


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


class VectorSearchRequest(ContractModel):
    selection: ScopeSelector
    vector: tuple[float, ...] = Field(min_length=1)
    model_space: Identifier
    limit: Positive = 20
    deadline_at: Timestamp


class VectorCandidate(ContractModel):
    target: ProjectionTarget
    rank: Positive
    score: float | None = None


class VectorSearchResult(ContractModel):
    candidates: tuple[VectorCandidate, ...]
    coverage: Literal["complete", "partial", "unavailable"]
    reason: NonEmpty | None = None


class AccessObserved(ContractModel):
    recall_id: Identifier
    memory: MemoryRef
    stage: Literal["candidate", "read", "packed", "delivered"]
    outcome: Literal["succeeded", "failed"]
    access_key: Identifier
    representation: Literal["original", "compressed"]
    elapsed_ms: Annotated[float, Field(ge=0)]
