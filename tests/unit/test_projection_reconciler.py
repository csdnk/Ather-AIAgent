from __future__ import annotations

import pytest

from aether_agent_memory.adapters.projection_executor import ProviderProjectionExecutor
from aether_agent_memory.adapters.projection_queue import InMemoryProjectionQueue
from aether_agent_memory.application.projection import ProjectionWorkService
from aether_agent_memory.application.services import WriteMemoryUseCase
from aether_agent_memory.b1 import EmbeddingRecord, EmbeddingResult, ProcessingStatus
from aether_agent_memory.b2 import MemoryEvent, MemoryEventType
from aether_agent_memory.context_store.models import ContextLayer
from aether_agent_memory.core.enums import MemoryType
from aether_agent_memory.core.memory import Memory
from aether_agent_memory.core.scope import Scope
from aether_agent_memory.memory import (
    MemoryProjectionReconciler,
    ProjectionWorkItem,
    ProjectionWorkKind,
    ProjectionWorkStatus,
)
from aether_agent_memory.memory.formation.service import MemoryFormationService
from aether_agent_memory.persistence.memory_store import InMemoryMemoryStore
from aether_agent_memory.runtime.dependencies import RuntimeDependencies
from aether_agent_memory.runtime.request_context import RequestContext


@pytest.mark.unit
@pytest.mark.asyncio
async def test_reconciler_plans_missing_projections_across_agent_sessions() -> None:
    store = InMemoryMemoryStore()
    await store.upsert(
        Memory(
            id="old-semantic",
            type=MemoryType.SEMANTIC,
            tenant_id="t",
            user_id="u",
            agent_id="a",
            session_id="old-session",
            content="old",
        )
    )

    work = await MemoryProjectionReconciler(store).plan(
        Scope(tenant_id="t", user_id="u", agent_id="a", session_id="new-session")
    )

    assert {item.memory_id for item in work} == {"old-semantic"}
    assert {item.kind for item in work} == {
        ProjectionWorkKind.EMBEDDING,
        ProjectionWorkKind.VECTOR_INDEX,
    }
    assert {item.scope.session_id for item in work} == {"old-session"}


@pytest.mark.unit
@pytest.mark.asyncio
async def test_projection_queue_is_revision_aware() -> None:
    queue = InMemoryProjectionQueue()
    item = ProjectionWorkItem(
        memory_id="m1",
        revision=2,
        kind=ProjectionWorkKind.EMBEDDING,
        scope=Scope(tenant_id="t", user_id="u", agent_id="a", session_id="s"),
    )
    first = await queue.enqueue(item)
    duplicate = await queue.enqueue(item.model_copy(update={"work_id": "other"}))
    claimed = await queue.claim(first.work_id)
    assert claimed is not None
    failed = await queue.fail(first.work_id, "backend unavailable", claim_token=claimed.claim_token)
    retried = await queue.retry(first.work_id)
    reclaimed = await queue.claim(first.work_id)

    assert duplicate.work_id == first.work_id
    assert claimed is not None and claimed.attempts == 1
    assert failed is not None and failed.status == ProjectionWorkStatus.FAILED
    assert retried is not None and retried.status == ProjectionWorkStatus.PENDING
    assert reclaimed is not None and reclaimed.attempts == 2


@pytest.mark.unit
@pytest.mark.asyncio
async def test_projection_worker_supersedes_old_revision_without_retrying() -> None:
    store = InMemoryMemoryStore()
    await store.upsert(
        Memory(
            id="memory-newer",
            type=MemoryType.SEMANTIC,
            tenant_id="t",
            user_id="u",
            agent_id="a",
            session_id="s",
            content="new authoritative content",
            revision=2,
        )
    )
    queue = InMemoryProjectionQueue()
    item = ProjectionWorkItem(
        memory_id="memory-newer",
        revision=1,
        kind=ProjectionWorkKind.EMBEDDING,
        scope=Scope(tenant_id="t", user_id="u", agent_id="a", session_id="s"),
    )
    await queue.enqueue(item)

    class _NeverCalledEmbedding:
        async def embed(self, request, context):
            raise AssertionError("superseded work must not call the provider")

    report = await ProjectionWorkService(
        MemoryProjectionReconciler(store),
        queue,
        ProviderProjectionExecutor(
            memory_store=store,
            embedding=_NeverCalledEmbedding(),
            vector_index=object(),
        ),
    ).drain()

    assert report.superseded == 1
    assert report.failed == 0
    assert report.retried == 0
    assert await queue.pending() == []


@pytest.mark.unit
@pytest.mark.asyncio
async def test_projection_worker_completes_success_and_records_failure() -> None:
    store = InMemoryMemoryStore()
    await store.upsert(
        Memory(
            id="memory-worker",
            type=MemoryType.SEMANTIC,
            tenant_id="t",
            user_id="u",
            agent_id="a",
            session_id="s",
            content="worker input",
        )
    )
    queue = InMemoryProjectionQueue()
    reconciler = MemoryProjectionReconciler(store)
    planned = await reconciler.plan(Scope(tenant_id="t", user_id="u", agent_id="a"))
    for item in planned:
        await queue.enqueue(item)

    class _Executor:
        async def execute(self, item):
            if item.kind == ProjectionWorkKind.VECTOR_INDEX:
                raise RuntimeError("index unavailable")

    report = await ProjectionWorkService(
        reconciler,
        queue,
        _Executor(),
    ).drain(limit=10)

    assert report.requested == len(planned)
    assert report.claimed == len(planned)
    assert report.succeeded == 1
    assert report.failed == 1
    assert report.retried == 1
    assert "index unavailable" in next(iter(report.errors.values()))


@pytest.mark.unit
@pytest.mark.asyncio
async def test_memory_write_automatically_enqueues_missing_projections() -> None:
    queue = InMemoryProjectionQueue()
    store = InMemoryMemoryStore()

    class _MemoryWriter:
        async def write_memory(self, event, context):
            memory = Memory(
                id="memory-auto-enqueue",
                type=MemoryType.SEMANTIC,
                tenant_id=event.tenant_id or context.tenant_id or "t",
                user_id=event.user_id or context.user_id or "u",
                agent_id=event.agent_id,
                session_id=event.session_id,
                content=event.content,
            )
            await store.upsert(memory)
            return memory

    dependencies = RuntimeDependencies(
        embedding=object(),
        memory_events=_MemoryWriter(),
        context_builder=object(),
        memory_store=store,
        projection_queue=queue,
        projection_reconciler=MemoryProjectionReconciler(store),
    )
    use_case = WriteMemoryUseCase(
        formation=MemoryFormationService(memory_events=dependencies.memory_events),
        dependencies=dependencies,
    )

    await use_case.execute(
        MemoryEvent(
            event_type=MemoryEventType.USER_MEMORY,
            session_id="s",
            agent_id="a",
            tenant_id="t",
            user_id="u",
            content="new memory",
        ),
        RequestContext.from_values(
            tenant_id="t",
            user_id="u",
            agent_id="a",
            session_id="s",
        ),
    )

    pending = await queue.pending()
    assert {item.kind for item in pending} == {
        ProjectionWorkKind.EMBEDDING,
        ProjectionWorkKind.VECTOR_INDEX,
    }
    assert {item.memory_id for item in pending} == {"memory-auto-enqueue"}


@pytest.mark.unit
@pytest.mark.asyncio
async def test_provider_projection_executor_completes_embedding_and_index_work() -> None:
    store = InMemoryMemoryStore()
    memory = Memory(
        id="memory-provider-executor",
        type=MemoryType.SEMANTIC,
        tenant_id="t",
        user_id="u",
        agent_id="a",
        session_id="s",
        content="provider-neutral projection",
    )
    await store.upsert(memory)
    embedded_requests = []
    indexed_memories = []
    indexed_context = []

    class _Embedding:
        async def embed(self, request, context):
            embedded_requests.append((request, context))
            return EmbeddingResult(
                request_id=context.request_id,
                trace_id=context.trace_id,
                source_id=memory.id,
                status=ProcessingStatus.SUCCESS,
                records=[
                    EmbeddingRecord(
                        request_id=context.request_id,
                        trace_id=context.trace_id,
                        source_id=memory.id,
                        chunk_id=memory.id,
                        chunk_text=memory.content,
                        vector=[0.25, 0.75],
                        embedding_model="test-model",
                    )
                ],
                latency_ms=0.1,
            )

    class _VectorIndex:
        async def upsert_memory(self, item, context):
            indexed_memories.append((item, context))

    class _ContextSemanticIndex:
        async def index(self, item, content):
            indexed_context.append((item, content))

        async def remove(self, uri):
            return None

        async def search(self, query):
            raise AssertionError("search is not part of projection execution")

    scope = Scope(tenant_id="t", user_id="u", agent_id="a", session_id="s")
    queue = InMemoryProjectionQueue()
    planned = await MemoryProjectionReconciler(store).plan(scope)
    for item in planned:
        await queue.enqueue(item)

    service = ProjectionWorkService(
        MemoryProjectionReconciler(store),
        queue,
        ProviderProjectionExecutor(
            memory_store=store,
            embedding=_Embedding(),
            vector_index=_VectorIndex(),
            context_semantic_index=_ContextSemanticIndex(),
        ),
    )
    report = await service.drain(limit=10)
    saved = await store.get(memory.id)

    assert report.requested == 2
    assert report.claimed == 2
    assert report.succeeded == 2
    assert report.failed == 0
    assert saved is not None
    assert saved.embedding == [0.25, 0.75]
    assert saved.embedding_status == "succeeded"
    assert saved.vector_projection_status == "succeeded"
    assert len(embedded_requests) == 1
    assert len(indexed_memories) == 1
    assert indexed_memories[0][0].embedding == [0.25, 0.75]
    assert {content.layer for _, content in indexed_context} == {
        ContextLayer.ABSTRACT,
        ContextLayer.OVERVIEW,
    }
    assert {str(item.uri) for item, _ in indexed_context} == {
        "aether://tenants/t/users/u/agents/a/memories/semantic/memory-provider-executor"
    }
