"""Memory-level retrieval and whole-body assembly contracts for A.1a--A.1d."""

from __future__ import annotations

from typing import Literal, Self

from pydantic import Field, model_validator

from aether_agent_memory.remember.contracts.foundation import (
    ChunkProjectionRequest as ChunkProjectionRequest,
)
from aether_agent_memory.remember.contracts.foundation import (
    ChunkProjectionResult as ChunkProjectionResult,
)
from aether_agent_memory.remember.contracts.foundation import (
    FullBodyReadResult,
    GuardStamp,
    ProjectionManifest,
)
from aether_agent_memory.remember.contracts.models import ConflictGroup, MemoryRef
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


class EmbeddingSpace(ContractModel):
    model_space: Identifier
    model_id: NonEmpty
    model_revision: NonEmpty
    dimensions: Positive
    tokenizer_id: Identifier
    query_prefix: str
    passage_prefix: str
    normalization: Literal["unit", "none"]
    metric: Literal["cosine", "inner_product", "l2"]
    max_input_tokens: Positive


class MemorySearchRequest(ContractModel):
    """B extraction and A recall share this search; it does not build ContextPack."""

    operation_id: Identifier
    purpose: Literal["recall", "extraction"]
    query: NonEmpty
    selection: ScopeSelector
    model_space: Identifier
    memory_top_k: Positive
    chunk_page_size: Positive
    max_chunk_hits: Positive
    max_rounds: Positive
    deadline_at: Timestamp

    @model_validator(mode="after")
    def bounded_search(self) -> Self:
        if self.chunk_page_size > self.max_chunk_hits:
            raise ValueError("one search page exceeds the total chunk budget")
        return self


class ChunkHit(ContractModel):
    memory: MemoryRef
    generation: Identifier
    body_hash: Digest
    model_space: Identifier
    chunk_index: Count
    vector_id: Digest
    input_hash: Digest
    rank: Positive
    score: float
    score_semantics: Literal["higher_is_better"] = "higher_is_better"


class ChunkSearchRequest(ContractModel):
    operation_id: Identifier
    selection: ScopeSelector
    model_space: EmbeddingSpace
    vector: tuple[float, ...] = Field(min_length=1)
    limit: Positive
    cursor: NonEmpty | None = None
    deadline_at: Timestamp

    @model_validator(mode="after")
    def query_dimensions(self) -> Self:
        if len(self.vector) != self.model_space.dimensions:
            raise ValueError("query embedding dimensions do not match the indexed model space")
        return self


class ChunkSearchResult(ContractModel):
    request: ChunkSearchRequest
    hits: tuple[ChunkHit, ...]
    coverage: Literal["complete", "partial", "unavailable"]
    next_cursor: NonEmpty | None = None
    reason_code: Identifier

    @model_validator(mode="after")
    def exact_search_page(self) -> Self:
        if len(self.hits) > self.request.limit:
            raise ValueError("chunk page exceeds requested limit")
        if self.coverage == "unavailable" and self.hits:
            raise ValueError("unavailable vector search cannot return hits")
        if any(h.model_space != self.request.model_space.model_space for h in self.hits):
            raise ValueError("vector search returned a different model space")
        for name, value in self.request.selection.model_dump(exclude_none=True).items():
            if any(getattr(h.memory.scope, name) != value for h in self.hits):
                raise ValueError("chunk hit lies outside the requested restriction")
        return self


class MemoryCandidate(ContractModel):
    memory: MemoryRef
    manifest: ProjectionManifest
    guard: GuardStamp
    hits: tuple[ChunkHit, ...] = Field(min_length=1)
    best_score: float
    rank: Positive

    @model_validator(mode="after")
    def qualified_best_chunk(self) -> Self:
        manifest = self.manifest
        if (
            manifest.state != "ready"
            or manifest.memory != self.memory
            or self.guard.memory != self.memory
            or self.guard.body_hash != manifest.body_hash
        ):
            raise ValueError("candidate must bind an eligible exact version and published index")
        chunks = {c.chunk_index: c for c in manifest.chunks}
        if len({h.chunk_index for h in self.hits}) != len(self.hits):
            raise ValueError("duplicate hit chunk")
        for hit in self.hits:
            chunk = chunks.get(hit.chunk_index)
            if (
                hit.memory != self.memory
                or hit.generation != manifest.generation
                or hit.body_hash != manifest.body_hash
                or hit.model_space != manifest.model_space
                or chunk is None
                or hit.vector_id != chunk.vector_id
                or hit.input_hash != chunk.input_hash
            ):
                raise ValueError("hit does not belong to the verified Memory generation")
        if self.best_score != max(h.score for h in self.hits):
            raise ValueError("memory score is the best eligible chunk, never a sum")
        return self


class MemorySearchResult(ContractModel):
    request: MemorySearchRequest
    scope: Scope
    candidates: tuple[MemoryCandidate, ...]
    examined_chunk_hits: Count
    rounds_used: Count
    stop_reason: Literal["top_k", "exhausted", "hit_limit", "round_limit", "deadline", "dependency"]
    coverage: Literal["complete", "partial", "unavailable"]

    @model_validator(mode="after")
    def memory_cardinality(self) -> Self:
        req = self.request
        if len(self.candidates) > req.memory_top_k:
            raise ValueError("Top K counts unique Memory IDs")
        ids = [(x.memory.scope.model_dump_json(), x.memory.memory_id) for x in self.candidates]
        if len(set(ids)) != len(ids):
            raise ValueError("versions/chunks of one Memory cannot occupy several Top K slots")
        if [c.rank for c in self.candidates] != list(range(1, len(self.candidates) + 1)):
            raise ValueError("candidate ranks must be contiguous in result order")
        scores = [c.best_score for c in self.candidates]
        if scores != sorted(scores, reverse=True):
            raise ValueError("candidate scores must be ordered from best to worst")
        if self.examined_chunk_hits and not self.rounds_used:
            raise ValueError("examined hits require at least one search round")
        if any(
            c.memory.scope.tenant_id != self.scope.tenant_id
            or c.manifest.model_space != req.model_space
            for c in self.candidates
        ):
            raise ValueError("candidate tenant/model space mismatch")
        for name, value in req.selection.model_dump(exclude_none=True).items():
            if any(getattr(c.memory.scope, name) != value for c in self.candidates):
                raise ValueError("candidate lies outside the requested restriction")
        if (
            self.examined_chunk_hits > req.max_chunk_hits
            or self.rounds_used > req.max_rounds
            or self.examined_chunk_hits < sum(len(c.hits) for c in self.candidates)
        ):
            raise ValueError("invalid search work accounting")
        if self.stop_reason == "top_k" and len(self.candidates) != req.memory_top_k:
            raise ValueError("top_k stop requires K qualified candidates")
        if self.coverage == "complete" and self.stop_reason not in {"top_k", "exhausted"}:
            raise ValueError("bounded/dependency stop cannot claim exhaustive coverage")
        if self.coverage == "unavailable" and self.candidates:
            raise ValueError("unavailable search cannot return qualified candidates")
        return self


class RecallPlanRequest(ContractModel):
    """Explicit new workflow request; does not silently change legacy RecallRequest."""

    recall_id: Identifier
    query: NonEmpty
    selection: ScopeSelector
    sources: tuple[Literal["working", "long_term"], ...] = Field(min_length=1)
    token_budget: Positive
    context_tokenizer: Identifier
    policy_version: Identifier
    deadline_at: Timestamp
    long_term_search: MemorySearchRequest | None = None

    @model_validator(mode="after")
    def source_routing(self) -> Self:
        if len(set(self.sources)) != len(self.sources):
            raise ValueError("duplicate source")
        if ("long_term" in self.sources) != (self.long_term_search is not None):
            raise ValueError("Working-only bypasses embedding/vector search")
        search = self.long_term_search
        if search is not None and (
            search.query != self.query
            or search.selection != self.selection
            or search.purpose != "recall"
            or search.deadline_at > self.deadline_at
        ):
            raise ValueError("search must bind the same recall inputs and bounded deadline")
        return self


class RankingEvidence(ContractModel):
    memory: MemoryRef
    source: Literal["working", "long_term"]
    source_rank: Positive
    rrf_k: Positive
    rrf_contribution: float = Field(gt=0)

    @model_validator(mode="after")
    def memory_rrf(self) -> Self:
        if abs(self.rrf_contribution - 1 / (self.rrf_k + self.source_rank)) > 1e-12:
            raise ValueError("RRF uses a memory source rank")
        return self


class ContextPackUnit(ContractModel):
    group_id: Identifier
    bodies: tuple[FullBodyReadResult, ...] = Field(min_length=1)
    primary_memories: tuple[MemoryRef, ...]
    conflict: ConflictGroup | None = None
    rank: Positive

    @model_validator(mode="after")
    def complete_group(self) -> Self:
        if any(b.outcome != "read" for b in self.bodies):
            raise ValueError("pack units contain verified full bodies only")
        refs = {b.memory.model_dump_json() for b in self.bodies}
        if len(refs) != len(self.bodies):
            raise ValueError("duplicate Memory body")
        if len({b.memory.scope.tenant_id for b in self.bodies}) != 1:
            raise ValueError("conflict groups cannot span tenants")
        primary = {m.model_dump_json() for m in self.primary_memories}
        if len(primary) != len(self.primary_memories) or not primary <= refs:
            raise ValueError("primary membership must be a unique subset of the complete unit")
        if self.conflict is not None:
            if self.group_id != self.conflict.group_id or refs != {
                m.model_dump_json() for m in self.conflict.members
            }:
                raise ValueError("mandatory conflict group must be complete")
        elif len(self.bodies) != 1:
            raise ValueError("a non-conflict pack unit contains one whole memory")
        return self


class ContextAssemblyPlan(ContractModel):
    request: RecallPlanRequest
    scope: Scope
    units: tuple[ContextPackUnit, ...]
    skipped_group_ids: tuple[Identifier, ...]
    rendered_context: str
    tokens_used: Count
    rank_evidence: tuple[RankingEvidence, ...]
    degradation_reasons: tuple[Identifier, ...]

    @model_validator(mode="after")
    def whole_pack_budget(self) -> Self:
        if self.tokens_used > self.request.token_budget:
            raise ValueError("actual rendered whole-pack token count exceeds budget")
        if bool(self.units) != bool(self.rendered_context) or bool(self.units) != bool(
            self.tokens_used
        ):
            raise ValueError("empty plans have empty rendering and zero tokens")
        ids = [u.group_id for u in self.units]
        if len(set(ids)) != len(ids) or set(ids) & set(self.skipped_group_ids):
            raise ValueError("pack group cannot be duplicated or both admitted and skipped")
        refs = [b.memory for u in self.units for b in u.bodies]
        if any(
            b.content is None or b.content not in self.rendered_context
            for u in self.units
            for b in u.bodies
        ):
            raise ValueError("rendering must preserve every admitted full body verbatim")
        keys = [(r.scope.model_dump_json(), r.memory_id) for r in refs]
        if len(set(keys)) != len(keys):
            raise ValueError("a memory appears once, even across versions/groups")
        if any(r.scope.tenant_id != self.scope.tenant_id for r in refs):
            raise ValueError("context cannot cross tenants")
        for name, value in self.request.selection.model_dump(exclude_none=True).items():
            if any(getattr(r.scope, name) != value for r in refs):
                raise ValueError("context lies outside the requested restriction")
        primary_count = sum(len(u.primary_memories) for u in self.units)
        search = self.request.long_term_search
        if primary_count > (search.memory_top_k if search else 0):
            raise ValueError("only long-term primary memories occupy K slots")
        evidence_keys = [(e.memory.model_dump_json(), e.source) for e in self.rank_evidence]
        if len(set(evidence_keys)) != len(evidence_keys):
            raise ValueError("duplicate memory/source RRF contribution")
        allowed = {r.model_dump_json() for r in refs}
        if any(
            e.memory.model_dump_json() not in allowed or e.source not in self.request.sources
            for e in self.rank_evidence
        ):
            raise ValueError("ranking evidence must describe admitted memories/selected sources")
        if self.request.sources == ("working",) and self.rank_evidence:
            raise ValueError("Working-only does not run RRF")
        return self


class ContextCommitRequest(ContractModel):
    """Submit to one transaction: revalidate stamps, persist result and outbox together."""

    operation_id: Identifier
    expected_recall_revision: Positive
    plan: ContextAssemblyPlan
    final_guards: tuple[GuardStamp, ...]

    @model_validator(mode="after")
    def all_members_revalidated(self) -> Self:
        original = {b.memory.model_dump_json(): b.guard for u in self.plan.units for b in u.bodies}
        guards = {g.memory.model_dump_json(): g for g in self.final_guards}
        if len(guards) != len(self.final_guards) or set(guards) != set(original):
            raise ValueError("final guard must cover every admitted Memory exactly once")
        for key, guard in guards.items():
            before = original[key]
            if before is None or (
                guard.object_revision != before.object_revision
                or guard.relations_revision != before.relations_revision
                or guard.authorization_epoch != before.authorization_epoch
                or guard.body_hash != before.body_hash
                or guard.checked_at < before.checked_at
                or guard.checked_at > self.plan.request.deadline_at
            ):
                raise ValueError("changed qualification requires rebuilding the plan")
        return self
