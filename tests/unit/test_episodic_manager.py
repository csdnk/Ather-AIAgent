from datetime import UTC, datetime, timedelta

import pytest

from aether_agent_memory.context.models import ContextRequest
from aether_agent_memory.core.enums import MemoryState, MemoryType
from aether_agent_memory.core.memory import Memory
from aether_agent_memory.episodic.manager import MockEpisodicMemoryManager
from aether_agent_memory.mocks.embedding import MockEmbeddingClient


@pytest.mark.unit
async def test_write_generates_embedding() -> None:
    embedder = MockEmbeddingClient(dim=16)
    mgr = MockEpisodicMemoryManager(embedder=embedder)
    m = Memory(type=MemoryType.EPISODIC, session_id="s1", agent_id="a1", content="hello")
    await mgr.write(m)
    assert m.embedding is not None
    assert len(m.embedding) == 16


@pytest.mark.unit
async def test_write_preserves_existing_embedding() -> None:
    embedder = MockEmbeddingClient(dim=16)
    mgr = MockEpisodicMemoryManager(embedder=embedder)
    m = Memory(
        type=MemoryType.EPISODIC,
        session_id="s1",
        agent_id="a1",
        content="hello",
        embedding=[0.1] * 16,
    )
    await mgr.write(m)
    assert m.embedding == [0.1] * 16


@pytest.mark.unit
async def test_recall_cosine_with_decay() -> None:
    embedder = MockEmbeddingClient(dim=16)
    mgr = MockEpisodicMemoryManager(embedder=embedder, half_life_hours=168.0)
    m = Memory(type=MemoryType.EPISODIC, session_id="s1", agent_id="a1", content="hello")
    await mgr.write(m)
    req = ContextRequest(session_id="s1", agent_id="a1", query="hello")
    recalled = await mgr.recall(req)
    assert len(recalled) == 1
    assert recalled[0].score > 0.0


@pytest.mark.unit
async def test_recall_ranks_relevant_higher() -> None:
    embedder = MockEmbeddingClient(dim=16)
    mgr = MockEpisodicMemoryManager(embedder=embedder)
    await mgr.write(
        Memory(type=MemoryType.EPISODIC, session_id="s1", agent_id="a1", content="cats")
    )
    await mgr.write(
        Memory(type=MemoryType.EPISODIC, session_id="s1", agent_id="a1", content="dogs")
    )
    req = ContextRequest(session_id="s1", agent_id="a1", query="cats")
    recalled = await mgr.recall(req)
    assert recalled[0].memory.content == "cats"


@pytest.mark.unit
async def test_recall_decay_lowers_old_memory_score() -> None:
    embedder = MockEmbeddingClient(dim=16)
    mgr = MockEpisodicMemoryManager(embedder=embedder, half_life_hours=24.0)
    old = Memory(
        type=MemoryType.EPISODIC,
        session_id="s1",
        agent_id="a1",
        content="cats",
        created_at=datetime.now(UTC) - timedelta(hours=48),
    )
    recent = Memory(
        type=MemoryType.EPISODIC,
        session_id="s1",
        agent_id="a1",
        content="cats",
        created_at=datetime.now(UTC),
    )
    await mgr.write(old)
    await mgr.write(recent)
    req = ContextRequest(session_id="s1", agent_id="a1", query="cats")
    recalled = await mgr.recall(req)
    assert recalled[0].memory.id == recent.id
    assert recalled[0].score > recalled[1].score


@pytest.mark.unit
async def test_recall_cross_session_and_excludes_expired() -> None:
    embedder = MockEmbeddingClient(dim=16)
    mgr = MockEpisodicMemoryManager(embedder=embedder)
    await mgr.write(Memory(type=MemoryType.EPISODIC, session_id="s1", agent_id="a1", content="a"))
    await mgr.write(
        Memory(
            type=MemoryType.EPISODIC,
            session_id="s1",
            agent_id="a1",
            content="b",
            state=MemoryState.EXPIRED,
        )
    )
    await mgr.write(Memory(type=MemoryType.EPISODIC, session_id="s2", agent_id="a1", content="a"))
    req = ContextRequest(session_id="s3", agent_id="a1", query="a")
    recalled = await mgr.recall(req)
    assert all(r.memory.agent_id == "a1" for r in recalled)
    assert all(r.memory.state != MemoryState.EXPIRED for r in recalled)
    sessions = {r.memory.session_id for r in recalled}
    assert "s2" in sessions
    assert "s1" in sessions


@pytest.mark.unit
async def test_recall_includes_archived() -> None:
    embedder = MockEmbeddingClient(dim=16)
    mgr = MockEpisodicMemoryManager(embedder=embedder)
    m = Memory(
        type=MemoryType.EPISODIC,
        session_id="s1",
        agent_id="a1",
        content="archived",
        state=MemoryState.ARCHIVED,
    )
    await mgr.write(m)
    req = ContextRequest(session_id="s1", agent_id="a1", query="archived")
    recalled = await mgr.recall(req)
    assert len(recalled) == 1


@pytest.mark.unit
async def test_state_transition_active_to_archived() -> None:
    embedder = MockEmbeddingClient(dim=16)
    mgr = MockEpisodicMemoryManager(embedder=embedder)
    m = Memory(type=MemoryType.EPISODIC, session_id="s1", agent_id="a1", content="x")
    await mgr.write(m)
    updated = await mgr.update_state(m.id, MemoryState.ARCHIVED)
    assert updated.state == MemoryState.ARCHIVED
