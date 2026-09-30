"""Bounded source discovery; database hits are references, never trusted memory facts."""

import asyncio
import math
import time
from dataclasses import dataclass, field
from typing import Literal

from aether_agent_memory.recall.contracts.models import (
    EmbeddingRequest,
    RecallRequest,
    VectorSearchRequest,
)
from aether_agent_memory.recall.contracts.ports import EmbeddingPort, VectorSearchPort
from aether_agent_memory.remember.contracts.foundation import (
    CandidateQualificationResult,
    CandidateQualificationTarget,
    ProjectionReadiness,
)
from aether_agent_memory.remember.contracts.models import MemoryRef, MemorySnapshot, ProjectionState
from aether_agent_memory.remember.contracts.ports import MemoryReadPort
from aether_agent_memory.runtime.contracts.models import ErrorCode, TrustedContext
from aether_agent_memory.runtime.foundation.common import FoundationError
from aether_agent_memory.runtime.foundation.requests import matches, select_scope, text_hash
from aether_agent_memory.runtime.foundation.storage import SQLiteUnitOfWork
from aether_agent_memory.runtime.foundation.telemetry import observed

from .config import RecallSettings


@dataclass
class SourceResult:
    source: str
    candidates: list[MemorySnapshot] = field(default_factory=list)
    coverage: str = "complete"
    reads: dict[str, tuple[MemoryRef, float]] = field(default_factory=dict)
    rejected: int = 0
    reason: str | None = None
    proofs: dict[str, CandidateQualificationResult] = field(default_factory=dict)

    def restrict(self, coverage: str, reason: str | None) -> None:
        """Accumulate limitations without erasing an earlier dependency failure."""
        if coverage == "complete":
            return
        severity = {"complete": 0, "partial": 1, "unavailable": 2}
        if severity[coverage] > severity[self.coverage]:
            self.coverage = coverage
        reasons = self.reason.split(";") if self.reason else []
        reason = reason or coverage
        if reason not in reasons:
            reasons.append(reason)
        self.reason = ";".join(reasons)


@observed("recall.source")
class Sources:
    def __init__(
        self,
        uow: SQLiteUnitOfWork,
        memories: MemoryReadPort,
        embedding: EmbeddingPort,
        vectors: VectorSearchPort,
        model_space: str,
        settings: RecallSettings,
    ) -> None:
        self.uow, self.memories = uow, memories
        self.embedding, self.vectors = embedding, vectors
        self.model_space, self.settings = model_space, settings

    async def working(self, ctx: TrustedContext, request: RecallRequest) -> SourceResult:
        scope = select_scope(ctx, request.selection)
        if not (scope.session_id or scope.task_id):
            raise FoundationError(ErrorCode.INVALID_ARGUMENT, "Working requires task or session")
        readiness = await self.memories.projection_readiness(ctx, request.selection, "working")
        readiness = ProjectionReadiness.model_validate_json(readiness.model_dump_json())
        if readiness.source != "working":
            raise FoundationError(ErrorCode.CONTRACT_VIOLATION, "readiness source mismatch")
        result = await self.vector_source(ctx, request, ctx.operation_id, "working")
        if not readiness.complete:
            result.restrict(
                "partial" if result.candidates else "unavailable",
                "index_failed" if readiness.failed_count else "index_pending",
            )
        return result

    async def long_term(
        self,
        ctx: TrustedContext,
        request: RecallRequest,
        recall_id: str,
    ) -> SourceResult:
        return await self.vector_source(ctx, request, recall_id, "long_term")

    async def vector_source(
        self,
        ctx: TrustedContext,
        request: RecallRequest,
        recall_id: str,
        source: Literal["working", "long_term"],
    ) -> SourceResult:
        result = SourceResult(source)
        embedded = await self.embedding.embed(
            ctx,
            EmbeddingRequest(
                operation_id=recall_id,
                usage="query",
                texts=(request.query,),
                model_space=self.model_space,
                deadline_at=ctx.deadline_at,
            ),
        )
        if (
            embedded.operation_id != recall_id
            or embedded.usage != "query"
            or embedded.model_space != self.model_space
            or len(embedded.items) != 1
            or embedded.items[0].input_hash != text_hash(request.query)
            or not all(math.isfinite(v) for v in embedded.items[0].vector)
        ):
            raise FoundationError(ErrorCode.CONTRACT_VIOLATION, "query embedding binding mismatch")
        scope = select_scope(ctx, request.selection)
        limit = self.settings.candidate_limit
        while True:
            found = await self.vectors.search(
                ctx,
                VectorSearchRequest(
                    selection=request.selection,
                    memory_source=source,
                    vector=embedded.items[0].vector,
                    model_space=self.model_space,
                    limit=limit,
                    deadline_at=ctx.deadline_at,
                ),
            )
            candidates = sorted(found.candidates, key=lambda c: (c.rank, c.target.vector_id))
            if len(candidates) > limit:
                raise FoundationError(ErrorCode.CONTRACT_VIOLATION, "vector result exceeds limit")
            qualified = [
                c
                for c in candidates
                if c.target.model_space == self.model_space
                and c.target.memory_source == source
                and matches(c.target.memory.scope, scope)
            ]
            if source == "working" and any(
                c.target.generation is None or c.target.body_hash is None for c in qualified
            ):
                raise FoundationError(
                    ErrorCode.CONTRACT_VIOLATION,
                    "Working vector candidate requires a published generation binding",
                )
            generation_targets = tuple(
                CandidateQualificationTarget.model_validate(
                    c.target.model_dump(include=set(CandidateQualificationTarget.model_fields))
                )
                for c in qualified
                if c.target.generation is not None
            )
            allowed_vectors = set()
            if generation_targets:
                qualifier = getattr(self.memories, "qualify", None)
                if qualifier is None:
                    raise FoundationError(
                        ErrorCode.DEPENDENCY_UNAVAILABLE, "generation qualification unavailable"
                    )
                proofs = tuple(
                    CandidateQualificationResult.model_validate_json(p.model_dump_json())
                    for p in await qualifier(ctx, generation_targets, "recall")
                )
                if {p.target for p in proofs} != set(generation_targets) or len(proofs) != len(
                    generation_targets
                ):
                    raise FoundationError(
                        ErrorCode.CONTRACT_VIOLATION, "qualification response mismatch"
                    )
                allowed_vectors = {p.target.vector_id for p in proofs if p.decision == "allowed"}
                result.proofs.update(
                    {
                        p.target.memory.model_dump_json(): p
                        for p in proofs
                        if p.decision == "allowed"
                    }
                )
                if any(p.decision == "unverifiable" for p in proofs):
                    result.restrict("partial", "qualification_unverifiable")
            started = time.perf_counter()
            refs = {
                c.target.memory.model_dump_json(): c.target.memory
                for c in qualified
                if (source == "long_term" and c.target.generation is None)
                or c.target.vector_id in allowed_vectors
            }
            loaded = self.memories.load(ctx, tuple(refs.values()))
            elapsed = (time.perf_counter() - started) * 1000
            result.reads.update({m.ref.model_dump_json(): (m.ref, elapsed) for m in loaded.items})
            snapshots = {m.ref.model_dump_json(): m for m in loaded.items}
            accepted: dict[str, MemorySnapshot] = {}
            for candidate in qualified:
                key = candidate.target.memory.model_dump_json()
                item = snapshots.get(key)
                if (
                    item
                    and item.projection_state == ProjectionState.READY
                    and item.model_space == self.model_space
                    and ("working" if item.kind == "working" else "long_term") == source
                    and (
                        source == "long_term" and item.content_hash == candidate.target.input_hash
                        if candidate.target.generation is None
                        else candidate.target.vector_id in allowed_vectors
                        and item.content_hash == candidate.target.body_hash
                    )
                ):
                    accepted.setdefault(key, item)
            result.candidates = list(accepted.values())[: self.settings.candidate_limit]
            result.rejected = len(candidates) - len(accepted)
            result.restrict(found.coverage, found.reason)
            if len(result.candidates) >= self.settings.candidate_limit or len(candidates) < limit:
                break
            if limit >= self.settings.max_discovery:
                if result.rejected:
                    result.restrict("partial", "discovery_budget_exhausted")
                break
            limit = min(limit * 2, self.settings.max_discovery)
        return result

    async def discover(
        self,
        ctx: TrustedContext,
        request: RecallRequest,
        recall_id: str,
        source: str,
    ) -> SourceResult:
        try:
            async with asyncio.timeout(self.settings.source_timeout_seconds):
                if source == "working":
                    return await self.working(ctx, request)
                return await self.long_term(ctx, request, recall_id)
        except TimeoutError:
            return SourceResult(source, coverage="unavailable", reason="source_timeout")
        except FoundationError as error:
            if error.code != ErrorCode.DEPENDENCY_UNAVAILABLE:
                raise
            return SourceResult(source, coverage="unavailable", reason=error.code.value)
