import pytest

from aether_agent_memory.context.models import ContextRequest
from aether_agent_memory.core.enums import MemoryState, MemoryType
from aether_agent_memory.core.memory import Memory
from aether_agent_memory.mocks.embedding import MockEmbeddingClient
from aether_agent_memory.semantic.manager import MockSemanticMemoryManager


class _UnavailableVectorStore:
    def upsert_memory(self, memory: Memory) -> None:
        pytest.fail("fact writes must not call the vector provider")

    def search(self, **_: object) -> list[object]:
        raise ConnectionError("Milvus unavailable")


@pytest.mark.unit
async def test_write_persists_fact_with_pending_projection() -> None:
    embedder = MockEmbeddingClient(dim=16)
    mgr = MockSemanticMemoryManager(embedder=embedder)
    m = Memory(type=MemoryType.SEMANTIC, session_id="s1", agent_id="a1", content="fact")
    await mgr.write(m)
    assert m.embedding is None
    assert m.embedding_status == m.vector_projection_status == "pending"
    assert await mgr.get(m.id) is not None


@pytest.mark.unit
async def test_recall_cosine_no_decay() -> None:
    embedder = MockEmbeddingClient(dim=16)
    mgr = MockSemanticMemoryManager(embedder=embedder)
    await mgr.write(
        Memory(
            type=MemoryType.SEMANTIC,
            session_id="s1",
            agent_id="a1",
            content="earth is round",
            embedding=await embedder.embed_one("earth is round"),
            embedding_status="succeeded",
        )
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
        Memory(
            type=MemoryType.SEMANTIC,
            session_id="s1",
            agent_id="a1",
            content="photosynthesis",
            embedding=await embedder.embed_one("photosynthesis"),
            embedding_status="succeeded",
        )
    )
    await mgr.write(
        Memory(
            type=MemoryType.SEMANTIC,
            session_id="s1",
            agent_id="a1",
            content="gravity",
            embedding=await embedder.embed_one("gravity"),
            embedding_status="succeeded",
        )
    )
    req = ContextRequest(session_id="s1", agent_id="a1", query="gravity")
    recalled = await mgr.recall(req)
    assert recalled[0].memory.content == "gravity"
    assert recalled[0].score > recalled[1].score


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
        embedding=await embedder.embed_one("fact"),
        embedding_status="succeeded",
    )
    recent = Memory(
        type=MemoryType.SEMANTIC,
        session_id="s1",
        agent_id="a1",
        content="fact",
        created_at=datetime.now(UTC),
        embedding=await embedder.embed_one("fact"),
        embedding_status="succeeded",
    )
    await mgr.write(old)
    await mgr.write(recent)
    req = ContextRequest(session_id="s1", agent_id="a1", query="fact")
    recalled = await mgr.recall(req)
    assert recalled[0].score > 0.0
    assert recalled[0].score == pytest.approx(recalled[1].score)


@pytest.mark.unit
async def test_write_skips_milvus_and_recall_falls_back_when_milvus_is_unavailable() -> None:
    embedder = MockEmbeddingClient(dim=16)
    mgr = MockSemanticMemoryManager(embedder=embedder, vector_store=_UnavailableVectorStore())  # type: ignore[arg-type]
    memory = await mgr.write(
        Memory(
            type=MemoryType.SEMANTIC,
            session_id="s1",
            agent_id="a1",
            content="persistent fact",
            embedding=await embedder.embed_one("persistent fact"),
            embedding_status="succeeded",
        )
    )

    assert memory.vector_projection_status == "pending"
    assert "milvus_projection_error" not in memory.metadata
    assert (await mgr.get(memory.id)) is not None

    recalled = await mgr.recall(
        ContextRequest(session_id="s1", agent_id="a1", query="persistent fact")
    )
    assert [item.memory.id for item in recalled] == [memory.id]
    assert recalled[0].score > 0.0
