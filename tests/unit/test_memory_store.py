from pathlib import Path

import pytest

from aether_agent_memory.context.models import ContextRequest
from aether_agent_memory.core.enums import MemoryType
from aether_agent_memory.core.memory import Memory
from aether_agent_memory.episodic.manager import MockEpisodicMemoryManager
from aether_agent_memory.mocks.embedding import MockEmbeddingClient
from aether_agent_memory.persistence import SQLiteMemoryStore
from aether_agent_memory.semantic.manager import MockSemanticMemoryManager
from aether_agent_memory.working.manager import MockWorkingMemoryManager


@pytest.mark.unit
async def test_sqlite_store_persists_across_instances(tmp_path: Path) -> None:
    path = tmp_path / "memory.db"
    first = SQLiteMemoryStore(path)
    memory = Memory(
        type=MemoryType.WORKING,
        session_id="s1",
        agent_id="a1",
        tenant_id="t1",
        user_id="u1",
        content="durable context",
    )
    await first.upsert(memory)

    second = SQLiteMemoryStore(path)
    restored = await second.get(memory.id)

    assert restored is not None
    assert restored.model_dump() == memory.model_dump()
    assert await second.delete(memory.id) is True
    assert await first.get(memory.id) is None


@pytest.mark.unit
async def test_three_managers_can_share_sqlite_store(tmp_path: Path) -> None:
    store = SQLiteMemoryStore(tmp_path / "shared.db")
    embedder = MockEmbeddingClient(dim=8)
    working = MockWorkingMemoryManager(store=store)
    episodic = MockEpisodicMemoryManager(embedder=embedder, store=store)
    semantic = MockSemanticMemoryManager(embedder=embedder, store=store)
    for manager, memory_type in (
        (working, MemoryType.WORKING),
        (episodic, MemoryType.EPISODIC),
        (semantic, MemoryType.SEMANTIC),
    ):
        await manager.write(
            Memory(
                type=memory_type,
                session_id="s1",
                agent_id="a1",
                content=memory_type.value,
            )
        )

    request = ContextRequest(session_id="s1", agent_id="a1", query="memory")
    assert [item.memory.type for item in await working.recall(request)] == [MemoryType.WORKING]
    assert [item.memory.type for item in await episodic.recall(request)] == [MemoryType.EPISODIC]
    assert [item.memory.type for item in await semantic.recall(request)] == [MemoryType.SEMANTIC]
