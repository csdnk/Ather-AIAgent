"""Bounded, signed pagination of published generation candidates from Milvus."""

from aether_agent_memory.recall.contracts.foundation import (
    ChunkHit,
    ChunkSearchRequest,
    ChunkSearchResult,
)
from aether_agent_memory.recall.contracts.models import VectorSearchRequest
from aether_agent_memory.runtime.contracts.models import ErrorCode, PageRequest, TrustedContext
from aether_agent_memory.runtime.foundation.common import FoundationError, fingerprint

from .vector_search import MilvusVectorSearch


class MilvusGenerationSearch:
    def __init__(self, vectors: MilvusVectorSearch, max_hits: int = 1000) -> None:
        if not 1 <= max_hits <= 10000:
            raise ValueError("Milvus candidate bound must be in [1,10000]")
        self.vectors, self.max_hits = vectors, max_hits

    async def search(self, ctx: TrustedContext, request: ChunkSearchRequest) -> ChunkSearchResult:
        if request.model_space.metric != "inner_product" and not (
            request.model_space.metric == "cosine" and request.model_space.normalization == "unit"
        ):
            raise FoundationError(
                ErrorCode.CONTRACT_VIOLATION,
                "Milvus IP requires inner product or unit cosine space",
            )
        result = await self.vectors.search(
            ctx,
            VectorSearchRequest(
                selection=request.selection,
                memory_source=request.memory_source,
                vector=request.vector,
                model_space=request.model_space.model_space,
                limit=self.max_hits,
                deadline_at=request.deadline_at,
            ),
        )
        hits = [
            ChunkHit.model_validate(
                {**c.target.model_dump(mode="json"), "rank": c.rank, "score": c.score}
            )
            for c in result.candidates
            if c.target.generation is not None
        ]
        hits.sort(key=lambda h: (-h.score, h.memory.model_dump_json(), h.vector_id))
        values = [
            (f"{i:012d}", h.model_copy(update={"rank": i + 1}).model_dump(mode="json"))
            for i, h in enumerate(hits)
        ]
        with self.vectors.uow.transaction() as tx:
            self.vectors.identity.revalidate(tx, ctx)
            binding = [
                ctx.principal.model_dump(mode="json"),
                request.model_dump(mode="json", exclude={"cursor", "limit"}),
                fingerprint(values),
            ]
            selected, cursor = tx.page(
                values, binding, PageRequest(limit=request.limit, cursor=request.cursor)
            )
        bounded = len(result.candidates) >= self.max_hits
        return ChunkSearchResult(
            request=request,
            hits=tuple(ChunkHit.model_validate(h) for h in selected),
            coverage="partial" if bounded else result.coverage,
            next_cursor=cursor,
            reason_code="candidate_limit" if bounded else "ok",
        )
