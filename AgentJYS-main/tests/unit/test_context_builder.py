import asyncio

import pytest

from aether_agent_memory.context.builder import MockContextPackBuilder
from aether_agent_memory.context.models import ContextRequest
from aether_agent_memory.core.enums import MemoryType
from aether_agent_memory.core.memory import Memory
from aether_agent_memory.episodic.manager import MockEpisodicMemoryManager
from aether_agent_memory.mocks.embedding import MockEmbeddingClient
from aether_agent_memory.semantic.manager import MockSemanticMemoryManager
from aether_agent_memory.working.manager import MockWorkingMemoryManager


def _make_builder() -> MockContextPackBuilder:
    embedder = MockEmbeddingClient(dim=16)
    return MockContextPackBuilder(
        working=MockWorkingMemoryManager(),
        episodic=MockEpisodicMemoryManager(embedder=embedder),
        semantic=MockSemanticMemoryManager(embedder=embedder),
    )


@pytest.mark.unit
async def test_build_merges_three_tiers() -> None:
    builder = _make_builder()
    await builder._managers[MemoryType.WORKING].write(
        Memory(type=MemoryType.WORKING, session_id="s1", agent_id="a1", content="working note")
    )
    await builder._managers[MemoryType.EPISODIC].write(
        Memory(type=MemoryType.EPISODIC, session_id="s1", agent_id="a1", content="episodic note")
    )
    await builder._managers[MemoryType.SEMANTIC].write(
        Memory(type=MemoryType.SEMANTIC, session_id="s1", agent_id="a1", content="semantic fact")
    )
    req = ContextRequest(session_id="s1", agent_id="a1", query="note")
    pack = await builder.build(req)
    assert len(pack.memories) == 3
    types = {m.type for m in pack.memories}
    assert types == {MemoryType.WORKING, MemoryType.EPISODIC, MemoryType.SEMANTIC}


@pytest.mark.unit
async def test_build_respects_max_candidates() -> None:
    builder = _make_builder()
    for i in range(10):
        await builder._managers[MemoryType.WORKING].write(
            Memory(type=MemoryType.WORKING, session_id="s1", agent_id="a1", content=f"item {i}")
        )
    req = ContextRequest(
        session_id="s1",
        agent_id="a1",
        query="item",
        max_candidates=3,
        max_tokens=10000,
    )
    pack = await builder.build(req)
    assert len(pack.memories) <= 3


@pytest.mark.unit
async def test_build_respects_token_budget() -> None:
    builder = _make_builder()
    await builder._managers[MemoryType.WORKING].write(
        Memory(
            type=MemoryType.WORKING,
            session_id="s1",
            agent_id="a1",
            content="x" * 200,
        )
    )
    await builder._managers[MemoryType.WORKING].write(
        Memory(
            type=MemoryType.WORKING,
            session_id="s1",
            agent_id="a1",
            content="y" * 200,
        )
    )
    req = ContextRequest(
        session_id="s1",
        agent_id="a1",
        query="x",
        max_candidates=10,
        max_tokens=60,
    )
    pack = await builder.build(req)
    assert pack.total_tokens <= 60
    assert pack.memories  # at least one


@pytest.mark.unit
async def test_build_assembles_numbered_text() -> None:
    builder = _make_builder()
    await builder._managers[MemoryType.WORKING].write(
        Memory(type=MemoryType.WORKING, session_id="s1", agent_id="a1", content="first")
    )
    req = ContextRequest(session_id="s1", agent_id="a1", query="first")
    pack = await builder.build(req)
    assert "[1]" in pack.assembled_text
    assert "first" in pack.assembled_text


@pytest.mark.unit
async def test_build_recall_scores_populated() -> None:
    builder = _make_builder()
    await builder._managers[MemoryType.WORKING].write(
        Memory(type=MemoryType.WORKING, session_id="s1", agent_id="a1", content="c")
    )
    req = ContextRequest(session_id="s1", agent_id="a1", query="c")
    pack = await builder.build(req)
    for m in pack.memories:
        assert m.id in pack.recall_scores


@pytest.mark.unit
async def test_build_subset_of_tiers() -> None:
    builder = _make_builder()
    await builder._managers[MemoryType.WORKING].write(
        Memory(type=MemoryType.WORKING, session_id="s1", agent_id="a1", content="w")
    )
    await builder._managers[MemoryType.SEMANTIC].write(
        Memory(type=MemoryType.SEMANTIC, session_id="s1", agent_id="a1", content="s")
    )
    req = ContextRequest(
        session_id="s1",
        agent_id="a1",
        query="w",
        memory_types=[MemoryType.WORKING],
    )
    pack = await builder.build(req)
    assert all(m.type == MemoryType.WORKING for m in pack.memories)


@pytest.mark.unit
async def test_build_returns_partial_context_when_one_source_fails() -> None:
    builder = _make_builder()
    await builder._managers[MemoryType.WORKING].write(
        Memory(type=MemoryType.WORKING, session_id="s1", agent_id="a1", content="available")
    )

    async def unavailable(_: ContextRequest):
        raise RuntimeError("Milvus unavailable")

    builder._managers[MemoryType.SEMANTIC].recall = unavailable  # type: ignore[method-assign]
    pack = await builder.build(ContextRequest(session_id="s1", agent_id="a1", query="available"))

    assert pack.status == "degraded"
    assert not pack.complete
    assert "semantic" in pack.missing_sources
    assert "Milvus unavailable" in pack.degradation_reasons["semantic"]
    assert [memory.content for memory in pack.memories] == ["available"]


@pytest.mark.unit
async def test_build_returns_partial_context_when_one_source_times_out() -> None:
    builder = _make_builder()
    await builder._managers[MemoryType.WORKING].write(
        Memory(type=MemoryType.WORKING, session_id="s1", agent_id="a1", content="available")
    )

    async def slow_recall(_: ContextRequest):
        await asyncio.sleep(0.05)
        return []

    builder._managers[MemoryType.SEMANTIC].recall = slow_recall  # type: ignore[method-assign]
    pack = await builder.build(
        ContextRequest(session_id="s1", agent_id="a1", query="available", deadline_ms=10)
    )

    assert pack.status == "degraded"
    assert not pack.complete
    assert pack.degradation_reasons["semantic"] == "recall deadline exceeded"
    assert pack.source_latency_ms["semantic"] < 40
    assert [memory.content for memory in pack.memories] == ["available"]
