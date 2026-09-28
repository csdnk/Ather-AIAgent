"""Published chunk mapping and bounded pagination; no live Milvus claim."""

import asyncio
from types import SimpleNamespace

import pytest
from test_flows import app as app
from test_generation_candidates import setup

from aether_agent_memory.recall.basic.milvus_generation import MilvusGenerationSearch
from aether_agent_memory.recall.contracts.foundation import ChunkSearchRequest
from aether_agent_memory.recall.contracts.models import VectorCandidate, VectorSearchResult
from aether_agent_memory.remember.contracts.models import ProjectionTarget


@pytest.mark.parametrize("metric,normalization", [("inner_product", "none"), ("cosine", "unit")])
def test_chunk_pages_keep_generation_and_report_candidate_cap(app, metric, normalization):
    ctx, search, _, _ = setup(app)
    space = search.spaces.resolve("test_space").model_copy(
        update={"metric": metric, "normalization": normalization}
    )
    with app.foundation.uow.transaction() as tx:
        rows = sorted(
            [r["hit"] for _, r in tx.rows("generation_vectors")], key=lambda r: -r["score"]
        )

    async def backend(ctx, request):
        candidates = []
        for row in rows[: request.limit]:
            target = ProjectionTarget.model_validate(
                {k: row[k] for k in ProjectionTarget.model_fields}
            )
            candidates.append(
                VectorCandidate(target=target, score=row["score"], rank=len(candidates) + 1)
            )
        return VectorSearchResult(candidates=tuple(candidates), coverage="complete")

    provider = MilvusGenerationSearch(
        SimpleNamespace(search=backend, uow=app.foundation.uow, identity=app.foundation.identity),
        max_hits=3,
    )
    request = ChunkSearchRequest(
        operation_id="search",
        selection={},
        model_space=space,
        vector=(1.0, 0.0),
        limit=2,
        deadline_at=ctx.deadline_at,
    )
    first = asyncio.run(provider.search(ctx, request))
    assert len(first.hits) == 2 and first.next_cursor and first.coverage == "partial"
    second = asyncio.run(
        provider.search(ctx, request.model_copy(update={"cursor": first.next_cursor}))
    )
    assert len(second.hits) == 1 and second.next_cursor is None
    assert len({h.vector_id for h in first.hits + second.hits}) == 3
    assert all(h.generation and h.body_hash for h in first.hits + second.hits)
