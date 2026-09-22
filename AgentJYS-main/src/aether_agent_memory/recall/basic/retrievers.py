"""Bounded source discovery; database hits are references, never trusted memory facts."""

import asyncio
import math
import time
from dataclasses import dataclass, field

from aether_agent_memory.recall.contracts.models import (
    EmbeddingRequest,
    RecallRequest,
    VectorSearchRequest,
)
from aether_agent_memory.recall.contracts.ports import EmbeddingPort, VectorSearchPort
from aether_agent_memory.remember.contracts.models import MemoryRef, MemorySnapshot, ProjectionState
from aether_agent_memory.remember.contracts.ports import MemoryReadPort
from aether_agent_memory.runtime.contracts.models import ErrorCode, PageRequest, TrustedContext
from aether_agent_memory.runtime.foundation.common import FoundationError
from aether_agent_memory.runtime.foundation.requests import matches, select_scope, text_hash
from aether_agent_memory.runtime.foundation.storage import SQLiteUnitOfWork
from aether_agent_memory.runtime.foundation.telemetry import observed

from .adapters import LexicalEmbedding
from .config import RecallSettings


@dataclass
class SourceResult:
    source: str
    candidates: list[MemorySnapshot] = field(default_factory=list)
    coverage: str = "complete"
    reads: dict[str, tuple[MemoryRef, float]] = field(default_factory=dict)
    rejected: int = 0
    reason: str | None = None


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
        result = SourceResult("working")
        scope = select_scope(ctx, request.selection)
        cursor = None
        items: list[MemorySnapshot] = []
        for _ in range(10):
            start = time.perf_counter()
            batch = self.memories.working(
                ctx, request.selection, PageRequest(limit=100, cursor=cursor)
            )
            elapsed = (time.perf_counter() - start) * 1000
            for item in batch.items:
                if not matches(item.ref.scope, scope):
                    raise FoundationError(ErrorCode.CONTRACT_VIOLATION, "Working scope mismatch")
                items.append(item)
                result.reads[item.ref.model_dump_json()] = (item.ref, elapsed)
            cursor = batch.next_cursor
            if cursor is None:
                break
            await asyncio.sleep(0)  # Honor cancellation between bounded pages.
        if cursor:
            result.coverage, result.reason = "partial", "working_page_budget_exhausted"
        query = LexicalEmbedding.features(request.query)
        scored = [
            (
                sum(
                    a * b for a, b in zip(query, LexicalEmbedding.features(m.content), strict=True)
                ),
                m,
            )
            for m in items
        ]
        scored.sort(key=lambda pair: (-pair[0], pair[1].ref.model_dump_json()))
        result.candidates = [m for score, m in scored[: self.settings.candidate_limit] if score > 0]
        return result

    async def long_term(
        self,
        ctx: TrustedContext,
        request: RecallRequest,
        recall_id: str,
    ) -> SourceResult:
        result = SourceResult("long_term")
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
                and matches(c.target.memory.scope, scope)
            ]
            started = time.perf_counter()
            loaded = self.memories.load(ctx, tuple(c.target.memory for c in qualified))
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
                    and item.content_hash == candidate.target.input_hash
                ):
                    accepted.setdefault(key, item)
            result.candidates = list(accepted.values())[: self.settings.candidate_limit]
            result.rejected = len(candidates) - len(accepted)
            result.coverage, result.reason = found.coverage, found.reason
            if len(result.candidates) >= self.settings.candidate_limit or len(candidates) < limit:
                break
            if limit >= self.settings.max_discovery:
                if result.rejected:
                    result.coverage, result.reason = "partial", "discovery_budget_exhausted"
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
