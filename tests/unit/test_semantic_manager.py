import pytest

from aether_agent_memory.context.models import ContextRequest
from aether_agent_memory.core.enums import MemoryState, MemoryType
from aether_agent_memory.core.memory import Memory
from aether_agent_memory.mocks.embedding import MockEmbeddingClient
from aether_agent_memory.semantic.manager import MockSemanticMemoryManager


class _UnavailableVectorStore:
    def upsert_memory(self, memory: Memory) -> None:
        raise ConnectionError("Milvus unavailable")

    def search(self, **_: object) -> list[object]:
        raise ConnectionError("Milvus unavailable")


@pytest.mark.unit
async def test_write_generates_embedding() -> None:
    embedder = MockEmbeddingClient(dim=16)
    mgr = MockSemanticMemoryManager(embedder=embedder)
    m = Memory(type=MemoryType.SEMANTIC, session_id="s1", agent_id="a1", content="fact")
    await mgr.write(m)
    assert m.embedding is not None
    assert len(m.embedding) == 16


@pytest.mark.unit
async def test_recall_cosine_no_decay() -> None:
    embedder = MockEmbeddingClient(dim=16)
    mgr = MockSemanticMemoryManager(embedder=embedder)
    await mgr.write(
        Memory(type=MemoryType.SEMANTIC, session_id="s1", agent_id="a1", content="earth is round")
    )
    req = ContextRequest(session_id="s1", agent_id="a1", query="earth is round")
    recalled = await mgr.recall(req)
    assert len(recalled) == 1
    assert recalled[0].score > 0.0


@pytest.mark.unit
async def test_recall_ranks_relevant_higher() -> None:
    embedder = MockEmbeddingClient(dim=16)
    mgr = MockSemanticMemoryManager(embedder=embedder)
    await mgr.write(
        Memory(type=MemoryType.SEMANTIC, session_id="s1", agent_id="a1", content="gravity")
    )
    await mgr.write(
        Memory(type=MemoryType.SEMANTIC, session_id="s1", agent_id="a1", content="photosynthesis")
    )
    req = ContextRequest(session_id="s1", agent_id="a1", query="gravity")
    recalled = await mgr.recall(req)
    assert recalled[0].memory.content == "gravity"


@pytest.mark.unit
async def test_recall_filters_by_agent() -> None:
    embedder = MockEmbeddingClient(dim=16)
    mgr = MockSemanticMemoryManager(embedder=embedder)
    await mgr.write(Memory(type=MemoryType.SEMANTIC, session_id="s1", agent_id="a1", content="a"))
    await mgr.write(Memory(type=MemoryType.SEMANTIC, session_id="s2", agent_id="a2", content="a"))
    req = ContextRequest(session_id="s1", agent_id="a1", query="a")
    recalled = await mgr.recall(req)
    assert all(r.memory.agent_id == "a1" for r in recalled)


@pytest.mark.unit
async def test_recall_excludes_expired() -> None:
    embedder = MockEmbeddingClient(dim=16)
    mgr = MockSemanticMemoryManager(embedder=embedder)
    await mgr.write(
        Memory(
            type=MemoryType.SEMANTIC,
            session_id="s1",
            agent_id="a1",
            content="x",
            state=MemoryState.EXPIRED,
        )
    )
    req = ContextRequest(session_id="s1", agent_id="a1", query="x")
    assert await mgr.recall(req) == []


@pytest.mark.unit
async def test_recall_does_not_decay_old_memories() -> None:
    from datetime import UTC, datetime, timedelta

    embedder = MockEmbeddingClient(dim=16)
    mgr = MockSemanticMemoryManager(embedder=embedder)
    old = Memory(
        type=MemoryType.SEMANTIC,
        session_id="s1",
        agent_id="a1",
        content="fact",
        created_at=datetime.now(UTC) - timedelta(days=365),
    )
    recent = Memory(
        type=MemoryType.SEMANTIC,
        session_id="s1",
        agent_id="a1",
        content="fact",
        created_at=datetime.now(UTC),
    )
    await mgr.write(old)
    await mgr.write(recent)
    req = ContextRequest(session_id="s1", agent_id="a1", query="fact")
    recalled = await mgr.recall(req)
    assert recalled[0].score == pytest.approx(recalled[1].score)


@pytest.mark.unit
async def test_milvus_failure_keeps_redis_primary_record_and_falls_back_for_recall() -> None:
    embedder = MockEmbeddingClient(dim=16)
    mgr = MockSemanticMemoryManager(embedder=embedder, vector_store=_UnavailableVectorStore())  # type: ignore[arg-type]
    memory = await mgr.write(
        Memory(type=MemoryType.SEMANTIC, session_id="s1", agent_id="a1", content="persistent fact")
    )

    assert memory.metadata["milvus_projection_status"] == "pending"
    assert "Milvus unavailable" in memory.metadata["milvus_projection_error"]
    assert (await mgr.get(memory.id)) is not None

    recalled = await mgr.recall(ContextRequest(session_id="s1", agent_id="a1", query="persistent fact"))
    assert [item.memory.id for item in recalled] == [memory.id]
