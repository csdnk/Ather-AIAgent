from datetime import timedelta
from pathlib import Path

import pytest

from aether_agent_memory.b2 import MemoryEvent, MemoryEventType, MemoryService
from aether_agent_memory.context.builder import MockContextPackBuilder
from aether_agent_memory.context.models import ContextRequest
from aether_agent_memory.core.enums import MemoryState, MemoryType, SourceType
from aether_agent_memory.episodic.manager import MockEpisodicMemoryManager
from aether_agent_memory.mocks.embedding import MockEmbeddingClient
from aether_agent_memory.persistence import SQLiteMemoryStore
from aether_agent_memory.semantic.manager import MockSemanticMemoryManager
from aether_agent_memory.signal.emitter import MockSignalEmitter
from aether_agent_memory.working.manager import MockWorkingMemoryManager


class _FailingSignalEmitter:
    async def emit(self, *_: object) -> None:
        raise ConnectionError("B3 signal service unavailable")


def _service() -> tuple[
    MemoryService,
    MockWorkingMemoryManager,
    MockEpisodicMemoryManager,
    MockSignalEmitter,
]:
    embedder = MockEmbeddingClient(dim=16)
    working = MockWorkingMemoryManager(default_ttl=timedelta(hours=1))
    episodic = MockEpisodicMemoryManager(embedder=embedder)
    semantic = MockSemanticMemoryManager(embedder=embedder)
    emitter = MockSignalEmitter()
    builder = MockContextPackBuilder(working=working, episodic=episodic, semantic=semantic)
    service = MemoryService(
        working=working,
        episodic=episodic,
        semantic=semantic,
        builder=builder,
        emitter=emitter,
    )
    return service, working, episodic, emitter


def _persistent_service(path: Path) -> tuple[MemoryService, SQLiteMemoryStore]:
    store = SQLiteMemoryStore(path)
    embedder = MockEmbeddingClient(dim=16)
    working = MockWorkingMemoryManager(
        default_ttl=timedelta(hours=1),
        store=store,
    )
    episodic = MockEpisodicMemoryManager(embedder=embedder, store=store)
    semantic = MockSemanticMemoryManager(embedder=embedder, store=store)
    builder = MockContextPackBuilder(working=working, episodic=episodic, semantic=semantic)
    return (
        MemoryService(
            working=working,
            episodic=episodic,
            semantic=semantic,
            builder=builder,
        ),
        store,
    )


@pytest.mark.unit
async def test_service_enforces_tenant_and_user_scope() -> None:
    service, _, _, _ = _service()
    for tenant, user, content in (
        ("tenant-a", "user-a", "alpha private context"),
        ("tenant-b", "user-b", "beta private context"),
    ):
        await service.ingest(
            MemoryEvent(
                event_type=MemoryEventType.AFTER_TURN,
                session_id="shared-session",
                agent_id="agent-1",
                tenant_id=tenant,
                user_id=user,
                content=content,
            )
        )

    pack = await service.before_inference(
        ContextRequest(
            session_id="shared-session",
            agent_id="agent-1",
            tenant_id="tenant-a",
            user_id="user-a",
            query="private context",
        )
    )
    assert [memory.content for memory in pack.memories] == ["alpha private context"]
    assert pack.memory_refs == [pack.memories[0].id]
    assert pack.budget_info["remaining_tokens"] >= 0


@pytest.mark.unit
async def test_service_archives_session_and_preserves_provenance() -> None:
    service, working, episodic, emitter = _service()
    stored = await service.ingest(
        MemoryEvent(
            event_type=MemoryEventType.TOOL_RESULT,
            session_id="s1",
            agent_id="a1",
            tenant_id="t1",
            user_id="u1",
            task_id="task-1",
            trace_id="trace-1",
            source_id="weather-tool",
            content="tool returned 58mm rainfall",
            source=SourceType.TOOL,
        )
    )
    archived = await service.archive_session(
        session_id="s1",
        agent_id="a1",
        tenant_id="t1",
        user_id="u1",
        trace_id="trace-archive",
    )

    assert len(archived) == 1
    assert archived[0].type == MemoryType.EPISODIC
    assert archived[0].metadata["archived_from"] == stored.id
    assert archived[0].source_id == "weather-tool"
    assert archived[0].trace_id == "trace-archive"
    assert (await working.get(stored.id)).state == MemoryState.ARCHIVED  # type: ignore[union-attr]
    assert len(await episodic.query(tenant_id="t1", user_id="u1")) == 1
    assert len(emitter.signals) == 2


@pytest.mark.unit
async def test_user_memory_is_written_to_semantic_tier() -> None:
    service, working, _, _ = _service()
    memory = await service.ingest(
        MemoryEvent(
            event_type=MemoryEventType.USER_MEMORY,
            session_id="s1",
            agent_id="a1",
            content="always answer using the flood manual",
        )
    )
    assert memory.type == MemoryType.SEMANTIC
    assert await working.get(memory.id) is None


@pytest.mark.unit
async def test_recall_access_stats_survive_sqlite_restart(tmp_path: Path) -> None:
    path = tmp_path / "b2.db"
    service, _ = _persistent_service(path)
    stored = await service.ingest(
        MemoryEvent(
            event_type=MemoryEventType.RAG_RESULT,
            session_id="s1",
            agent_id="a1",
            content="rainfall evidence",
            evidence_refs=["p2://rainfall/segment-1"],
        )
    )

    pack = await service.before_inference(
        ContextRequest(session_id="s1", agent_id="a1", query="rainfall")
    )
    reopened = SQLiteMemoryStore(path)
    persisted = await reopened.get(stored.id)

    assert pack.evidence_refs == ["p2://rainfall/segment-1"]
    assert pack.memories[0].access_count == 1
    assert pack.memories[0].last_accessed_at is not None
    assert persisted is not None
    assert persisted.access_count == 0
    assert persisted.last_accessed_at is None


@pytest.mark.unit
async def test_b3_signal_failure_does_not_rollback_b2_write() -> None:
    service, working, episodic, _ = _service()
    service._emitter = _FailingSignalEmitter()  # type: ignore[assignment]

    stored = await service.ingest(
        MemoryEvent(
            event_type=MemoryEventType.AFTER_TURN,
            session_id="s1",
            agent_id="a1",
            content="write must survive B3 failure",
        )
    )

    assert stored.type == MemoryType.WORKING
    assert await working.get(stored.id) is not None
    assert await episodic.get(stored.id) is None
