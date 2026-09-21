"""Fact persistence must not call or wait for derived embedding providers."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from aether_agent_memory.adapters.memory_service import LegacyMemoryEventAdapter
from aether_agent_memory.adapters.projection_executor import ProviderProjectionExecutor
from aether_agent_memory.adapters.projection_queue import InMemoryProjectionQueue
from aether_agent_memory.adapters.session_store import InMemorySessionStore
from aether_agent_memory.application.projection import ProjectionWorkService
from aether_agent_memory.application.services import WriteMemoryUseCase
from aether_agent_memory.application.session import ConsolidateSessionUseCase
from aether_agent_memory.b1 import EmbeddingRecord, EmbeddingResult, ProcessingStatus
from aether_agent_memory.b2 import MemoryEvent, MemoryEventType, MemoryService
from aether_agent_memory.b2.memory_consolidation import MemoryConsolidator
from aether_agent_memory.context import MockContextPackBuilder
from aether_agent_memory.core.enums import MemoryState, MemoryType
from aether_agent_memory.core.memory import Memory
from aether_agent_memory.episodic.manager import MockEpisodicMemoryManager
from aether_agent_memory.memory.formation import MemoryFormationService
from aether_agent_memory.memory.projection import MemoryProjectionReconciler, ProjectionWorkKind
from aether_agent_memory.persistence import InMemoryMemoryStore, SQLiteMemoryStore
from aether_agent_memory.runtime.dependencies import RuntimeDependencies
from aether_agent_memory.runtime.request_context import RequestContext
from aether_agent_memory.semantic.manager import MockSemanticMemoryManager
from aether_agent_memory.session.models import SessionMessage
from aether_agent_memory.session.service import SessionService
from aether_agent_memory.working.manager import MockWorkingMemoryManager


class RecoverableEmbedding:
    available = False
    calls = 0

    async def embed_one(self, text):
        self.calls += 1
        if not self.available:
            raise ConnectionError("B1 unavailable")
        return [0.25, 0.75]

    async def embed(self, request, context):
        vector = await self.embed_one(request.text)
        return EmbeddingResult(
            request_id=context.request_id,
            trace_id=context.trace_id,
            source_id=request.source_id,
            status=ProcessingStatus.SUCCESS,
            records=[
                EmbeddingRecord(
                    request_id=context.request_id,
                    trace_id=context.trace_id,
                    source_id=request.source_id,
                    chunk_id=request.memory_id,
                    chunk_text=request.text,
                    vector=vector,
                    embedding_model="recovery-test",
                )
            ],
            latency_ms=0.1,
        )


class RecordingVectorIndex:
    def __init__(self):
        self.memories = []

    async def upsert_memory(self, memory, context):
        self.memories.append(memory)


def _context():
    return RequestContext.from_values(
        tenant_id="tenant",
        user_id="user",
        agent_id="agent",
        session_id="session",
        request_id="request",
        trace_id="trace",
    )


def _memory(memory_type=MemoryType.SEMANTIC, **overrides):
    return Memory(
        type=memory_type,
        tenant_id="tenant",
        user_id="user",
        agent_id="agent",
        session_id="session",
        content="Remember the deployment decision.",
        **overrides,
    )


def _writer(store, embedding, queue):
    working = MockWorkingMemoryManager(store=store)
    episodic = MockEpisodicMemoryManager(embedder=embedding, store=store)
    semantic = MockSemanticMemoryManager(embedder=embedding, store=store)
    builder = MockContextPackBuilder(working=working, episodic=episodic, semantic=semantic)
    service = MemoryService(
        working=working,
        episodic=episodic,
        semantic=semantic,
        builder=builder,
    )
    events = LegacyMemoryEventAdapter(SimpleNamespace(ingest_memory=service.ingest))
    deps = RuntimeDependencies(
        embedding=embedding,
        memory_events=events,
        context_builder=None,
        memory_store=store,
        projection_queue=queue,
        projection_reconciler=MemoryProjectionReconciler(store),
    )
    return WriteMemoryUseCase(
        formation=MemoryFormationService(memory_events=events),
        dependencies=deps,
    ), service


@pytest.mark.parametrize("memory_type", [MemoryType.SEMANTIC, MemoryType.EPISODIC])
async def test_direct_fact_survives_b1_outage_and_recovers_via_existing_worker(
    tmp_path,
    memory_type,
):
    path = tmp_path / "facts.db"
    store = SQLiteMemoryStore(path)
    embedding = RecoverableEmbedding()
    manager_type = (
        MockSemanticMemoryManager
        if memory_type == MemoryType.SEMANTIC
        else MockEpisodicMemoryManager
    )
    manager = manager_type(embedder=embedding, store=store)
    memory = await manager.write(
        _memory(
            memory_type,
            metadata={"embedding_status": "pending", "vector_projection_status": "pending"},
        )
    )

    reopened = SQLiteMemoryStore(path)
    fact = await reopened.get(memory.id)
    assert fact is not None
    assert fact.content == memory.content
    assert embedding.calls == 0
    assert fact.embedding is None
    assert fact.embedding_status == "pending"
    assert fact.vector_projection_status == "pending"

    queue = InMemoryProjectionQueue()
    reconciler = MemoryProjectionReconciler(reopened)
    index = RecordingVectorIndex()
    worker = ProjectionWorkService(
        reconciler,
        queue,
        ProviderProjectionExecutor(memory_store=reopened, embedding=embedding, vector_index=index),
    )
    work = await worker.reconcile(_context().scope)
    assert {item.kind for item in work} == {
        ProjectionWorkKind.EMBEDDING,
        ProjectionWorkKind.VECTOR_INDEX,
    }
    failed = await worker.drain()
    assert failed.failed > 0
    assert embedding.calls > 0
    fact = await reopened.get(memory.id)
    assert fact is not None
    assert fact.embedding_status != "succeeded"
    assert fact.vector_projection_status != "succeeded"

    embedding.available = True
    recovered = await worker.drain()
    assert recovered.succeeded == 2
    fact = await reopened.get(memory.id)
    assert fact is not None
    assert fact.embedding == [0.25, 0.75]
    assert fact.embedding_status == "succeeded"
    assert fact.vector_projection_status == "succeeded"
    assert fact.metadata["embedding_status"] == "succeeded"
    assert fact.metadata["vector_projection_status"] == "succeeded"
    assert len(index.memories) == 1
    assert await reconciler.plan(_context().scope) == []


@pytest.mark.parametrize("event_type", [MemoryEventType.USER_MEMORY, MemoryEventType.AFTER_TURN])
async def test_event_fact_exists_before_projection_enqueue(event_type):
    store = InMemoryMemoryStore()

    class FactCheckingQueue(InMemoryProjectionQueue):
        async def enqueue(self, item):
            assert await store.get(item.memory_id) is not None
            return await super().enqueue(item)

    queue = FactCheckingQueue()
    embedding = RecoverableEmbedding()
    writer, _ = _writer(store, embedding, queue)
    memory = await writer.execute(
        MemoryEvent(
            event_type=event_type,
            tenant_id="tenant",
            user_id="user",
            agent_id="agent",
            session_id="session",
            content="Persist before B1.",
        ),
        _context(),
    )
    assert await store.get(memory.id) is not None
    assert embedding.calls == 0
    assert memory.embedding_status == "pending"
    assert {work.memory_id for work in await queue.pending()} == {memory.id}


async def test_session_extraction_forms_fact_while_b1_is_unavailable():
    store = InMemoryMemoryStore()
    queue = InMemoryProjectionQueue()
    embedding = RecoverableEmbedding()
    writer, _ = _writer(store, embedding, queue)
    sessions = SessionService(InMemorySessionStore())
    context = _context()
    await sessions.append(context.scope, SessionMessage(role="user", content="Keep this fact."))
    commit = await sessions.commit(context.scope, keep_recent_count=0)
    result = await ConsolidateSessionUseCase(sessions=sessions, write_memory=writer).execute(
        context,
        archive_id=commit.archive_id,
    )
    fact = await store.get(result.memory_id)
    assert result.status == "succeeded"
    assert fact is not None
    assert fact.embedding_status == "pending"
    assert fact.vector_projection_status == "pending"
    assert embedding.calls == 0
    assert {work.memory_id for work in await queue.pending()} == {fact.id}


async def test_archive_session_does_not_require_embedding():
    store = InMemoryMemoryStore()
    embedding = RecoverableEmbedding()
    writer, service = _writer(store, embedding, InMemoryProjectionQueue())
    memory = await writer.execute(
        MemoryEvent(
            event_type=MemoryEventType.AFTER_TURN,
            tenant_id="tenant",
            user_id="user",
            agent_id="agent",
            session_id="session",
            content="Archive this observation.",
        ),
        _context(),
    )
    archived = await service.archive_session(
        tenant_id="tenant",
        user_id="user",
        agent_id="agent",
        session_id="session",
    )
    assert len(archived) == 1
    assert (await store.get(memory.id)).state == MemoryState.ARCHIVED
    assert (await store.get(archived[0].id)).embedding_status == "pending"
    work = await MemoryProjectionReconciler(store).plan_for(archived[0])
    assert {item.kind for item in work} == {
        ProjectionWorkKind.EMBEDDING,
        ProjectionWorkKind.VECTOR_INDEX,
    }
    assert embedding.calls == 0


async def test_consolidation_and_merge_preserve_facts_without_embedding():
    store = InMemoryMemoryStore()
    embedding = RecoverableEmbedding()
    manager = MockSemanticMemoryManager(embedder=embedding, store=store)
    consolidator = MemoryConsolidator()
    first = await consolidator.consolidate(_memory(), manager=manager)
    duplicate = await consolidator.consolidate(_memory(), manager=manager)
    assert duplicate.status == "merged_duplicate"
    assert duplicate.canonical_memory.id == first.canonical_memory.id
    fact = await store.get(duplicate.canonical_memory.id)
    assert fact is not None and fact.facts
    assert fact.embedding_status == "pending"
    assert embedding.calls == 0
    assert await MemoryProjectionReconciler(store).plan_for(fact)


async def test_consolidation_saves_source_before_extractor_failure():
    store = InMemoryMemoryStore()
    manager = MockSemanticMemoryManager(embedder=RecoverableEmbedding(), store=store)
    memory = _memory()

    class FailingExtractor:
        def extract(self, source):
            raise ConnectionError("extractor unavailable")

    with pytest.raises(ConnectionError, match="extractor unavailable"):
        await MemoryConsolidator(extractor=FailingExtractor()).consolidate(memory, manager=manager)
    assert await store.get(memory.id) is not None


async def test_semantic_write_does_not_call_vector_provider():
    class ForbiddenVectorStore:
        def upsert_memory(self, memory):
            pytest.fail("vector provider must run outside the fact write")

    manager = MockSemanticMemoryManager(
        embedder=RecoverableEmbedding(),
        vector_store=ForbiddenVectorStore(),
    )
    memory = await manager.write(_memory(embedding=[0.25, 0.75], embedding_status="succeeded"))
    assert memory.vector_projection_status == "pending"
    assert await manager.get(memory.id) is not None


async def test_archive_new_memory_id_does_not_inherit_ready_vector_projection():
    store = InMemoryMemoryStore()
    _, service = _writer(store, RecoverableEmbedding(), InMemoryProjectionQueue())
    source = _memory(
        MemoryType.WORKING,
        embedding=[0.25, 0.75],
        embedding_status="succeeded",
        vector_projection_status="succeeded",
        metadata={"vector_projection_status": "succeeded"},
    )
    await store.upsert(source)
    archived = await service.archive_session(
        tenant_id="tenant",
        user_id="user",
        agent_id="agent",
        session_id="session",
    )
    assert archived[0].id != source.id
    assert archived[0].embedding_status == "succeeded"
    assert archived[0].vector_projection_status == "pending"
    assert archived[0].metadata["vector_projection_status"] == "pending"
    assert {
        work.kind for work in await MemoryProjectionReconciler(store).plan_for(archived[0])
    } == {
        ProjectionWorkKind.VECTOR_INDEX,
    }


async def test_direct_compression_preserves_source_when_compressor_fails():
    store = InMemoryMemoryStore()
    embedding = RecoverableEmbedding()
    working = MockWorkingMemoryManager(store=store)
    episodic = MockEpisodicMemoryManager(embedder=embedding, store=store)
    semantic = MockSemanticMemoryManager(embedder=embedding, store=store)

    class FailingCompressor:
        async def compress_and_store(self, *args, **kwargs):
            raise ConnectionError("compressor unavailable")

    service = MemoryService(
        working=working,
        episodic=episodic,
        semantic=semantic,
        builder=MockContextPackBuilder(working=working, episodic=episodic, semantic=semantic),
        compressor=FailingCompressor(),
        compression_store=SimpleNamespace(),
    )
    memory = _memory()
    with pytest.raises(ConnectionError, match="compressor unavailable"):
        await service.compress_memory(memory)
    fact = await store.get(memory.id)
    assert fact is not None and fact.content == memory.content
    assert fact.compression_status == "pending"
    assert {work.kind for work in await MemoryProjectionReconciler(store).plan_for(fact)} == {
        ProjectionWorkKind.EMBEDDING,
        ProjectionWorkKind.VECTOR_INDEX,
        ProjectionWorkKind.SUMMARY,
    }


async def test_event_fact_survives_lost_projection_enqueue():
    store = InMemoryMemoryStore()

    class UnavailableQueue(InMemoryProjectionQueue):
        async def enqueue(self, item):
            raise ConnectionError("queue unavailable")

    writer, _ = _writer(store, RecoverableEmbedding(), UnavailableQueue())
    memory = await writer.execute(
        MemoryEvent(
            event_type=MemoryEventType.USER_MEMORY,
            tenant_id="tenant",
            user_id="user",
            agent_id="agent",
            session_id="session",
            content="Keep despite queue outage.",
        ),
        _context(),
    )
    assert await store.get(memory.id) is not None
    assert len(await MemoryProjectionReconciler(store).plan(_context().scope)) == 2
