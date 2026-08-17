from datetime import UTC, datetime, timedelta

import pytest

from aether_agent_memory import (
    ContextRequest,
    Memory,
    MemorySignal,
    MemoryState,
    MemoryType,
    MockContextPackBuilder,
    MockEmbeddingClient,
    MockEpisodicMemoryManager,
    MockSemanticMemoryManager,
    MockSignalEmitter,
    MockWorkingMemoryManager,
    SignalType,
)


@pytest.mark.unit
async def test_end_to_end_memory_flow() -> None:
    embedder = MockEmbeddingClient(dim=32)
    working = MockWorkingMemoryManager(default_ttl=timedelta(seconds=3600))
    episodic = MockEpisodicMemoryManager(embedder=embedder, half_life_hours=168.0)
    semantic = MockSemanticMemoryManager(embedder=embedder)
    builder = MockContextPackBuilder(working=working, episodic=episodic, semantic=semantic)
    emitter = MockSignalEmitter()

    await working.write(
        Memory(
            type=MemoryType.WORKING,
            session_id="sess-1",
            agent_id="agent-1",
            content="user asked about weather forecasting",
        )
    )
    await episodic.write(
        Memory(
            type=MemoryType.EPISODIC,
            session_id="sess-1",
            agent_id="agent-1",
            content="discussed satellite cloud imagery last week",
        )
    )
    await semantic.write(
        Memory(
            type=MemoryType.SEMANTIC,
            session_id="sess-1",
            agent_id="agent-1",
            content="weather models use numerical prediction",
        )
    )

    request = ContextRequest(
        session_id="sess-1",
        agent_id="agent-1",
        query="weather forecasting",
        max_tokens=4096,
        max_candidates=10,
    )
    pack = await builder.build(request)

    assert len(pack.memories) == 3
    assert pack.total_tokens > 0
    assert pack.total_tokens <= pack.budget_tokens
    assert pack.assembled_text
    assert len(pack.recall_scores) == len(pack.memories)

    await emitter.emit(
        MemorySignal(
            memory_id=pack.memories[0].id,
            memory_type=pack.memories[0].type,
            session_id="sess-1",
            agent_id="agent-1",
            signal_type=SignalType.ACCESS,
            heat=0.9,
        )
    )
    assert len(emitter.signals) == 1
    assert emitter.signals[0].heat == 0.9


@pytest.mark.unit
async def test_end_to_end_ttl_and_state_lifecycle() -> None:
    embedder = MockEmbeddingClient(dim=16)
    working = MockWorkingMemoryManager(default_ttl=timedelta(seconds=60))
    episodic = MockEpisodicMemoryManager(embedder=embedder)
    builder = MockContextPackBuilder(
        working=working, episodic=episodic, semantic=MockSemanticMemoryManager(embedder=embedder)
    )

    await working.write(
        Memory(type=MemoryType.WORKING, session_id="s1", agent_id="a1", content="temp note")
    )
    await episodic.write(
        Memory(type=MemoryType.EPISODIC, session_id="s1", agent_id="a1", content="episodic note")
    )

    await episodic.update_state(
        next(m.id for m in await episodic.query(session_id="s1") if m.type == MemoryType.EPISODIC),
        MemoryState.ARCHIVED,
    )

    expired_mem = Memory(
        type=MemoryType.WORKING,
        session_id="s1",
        agent_id="a1",
        content="will expire",
        expires_at=datetime.now(UTC) - timedelta(hours=1),
    )
    await working.write(expired_mem)
    expired_count = await working.expire_stale()
    assert expired_count == 1

    pack = await builder.build(ContextRequest(session_id="s1", agent_id="a1", query="note"))
    assert all(m.state != MemoryState.EXPIRED for m in pack.memories)
