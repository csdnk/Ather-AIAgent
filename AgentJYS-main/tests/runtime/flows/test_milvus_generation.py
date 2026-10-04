"""Published chunk mapping and bounded pagination against the real Azure Milvus index."""

import asyncio

import pytest
from test_flows import app as app
from test_generation_candidates import setup

from aether_agent_memory.recall.basic.milvus_generation import MilvusGenerationSearch
from aether_agent_memory.recall.contracts.foundation import ChunkSearchRequest


@pytest.mark.parametrize("metric,normalization", [("inner_product", "none"), ("cosine", "unit")])
def test_chunk_pages_keep_generation_and_report_candidate_cap(app, metric, normalization):
    ctx, search, _, seed_request = setup(app)
    space = search.spaces.resolve("test_space").model_copy(
        update={"metric": metric, "normalization": normalization}
    )
    seeded = asyncio.run(search.search(ctx, seed_request))
    assert seeded.candidates
    provider = MilvusGenerationSearch(search.vectors.vectors, max_hits=3)
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
    assert first.reason_code == "candidate_limit"
    second = asyncio.run(
        provider.search(ctx, request.model_copy(update={"cursor": first.next_cursor}))
    )
    assert len(second.hits) == 1 and second.next_cursor is None
    assert len({h.vector_id for h in first.hits + second.hits}) == 3
    assert all(h.generation and h.body_hash for h in first.hits + second.hits)
    assert [h.rank for h in first.hits + second.hits] == [1, 2, 3]
