from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from aether_agent_memory.adapters.session_store import InMemorySessionStore
from aether_agent_memory.b1 import EmbeddingRequest, EmbeddingResult, ProcessingStatus
from aether_agent_memory.b2 import MemoryEvent, MemoryEventType
from aether_agent_memory.context import ContextPack, ContextRequest
from aether_agent_memory.core.enums import MemoryType, SourceType
from aether_agent_memory.core.memory import Memory
from aether_agent_memory.runtime import MemoryRuntime, RuntimeDependencies, RuntimeProfile
from aether_agent_memory.runtime.dtos import LongMemorySubmission, ObjectReference
from aether_agent_memory.runtime.errors import ScopeError
from aether_agent_memory.runtime.request_context import RequestContext
from aether_agent_memory.session import SessionMessage, SessionMessageRole


class _RecordingEmbedding:
    def __init__(self, *, fail: bool = False) -> None:
        self.calls: list[tuple[EmbeddingRequest, RequestContext]] = []
        self.fail = fail

    async def embed(self, request: EmbeddingRequest, context: RequestContext) -> EmbeddingResult:
        self.calls.append((request, context))
        if self.fail:
            raise RuntimeError("b1 failed")
        return EmbeddingResult(
            request_id=context.request_id,
            trace_id=context.trace_id,
            source_id=request.source_id or "source",
            status=ProcessingStatus.SUCCESS,
            latency_ms=0.0,
        )


class _RecordingMemoryEvents:
    def __init__(self) -> None:
        self.items: list[Memory] = []

    async def write_memory(self, event: MemoryEvent, context: RequestContext) -> Memory:
        memory = Memory(
            type=MemoryType.WORKING,
            session_id=event.session_id,
            agent_id=event.agent_id,
            user_id=event.user_id,
            tenant_id=event.tenant_id,
            request_id=context.request_id,
            trace_id=context.trace_id,
            source_id=event.source_id,
            content=event.content,
        )
        self.items.append(memory)
        return memory


class _ContextBuilder:
    async def build_context(self, request: ContextRequest, context: RequestContext) -> ContextPack:
        return ContextPack(
            request=request,
            memories=[],
            total_tokens=0,
            budget_tokens=request.max_tokens,
            recall_scores={},
            assembled_text="",
            built_at=datetime.now(UTC),
            trace_id=context.trace_id,
        )


class _TaskQueue:
    async def submit_long_memory(self, **kwargs: Any) -> LongMemorySubmission:
        context = kwargs["context"]
        return LongMemorySubmission(
            task_id="task-1",
            memory_id="memory-1",
            state="PENDING",
            request_id=context.request_id,
            trace_id=context.trace_id,
            source_id=kwargs["source_id"],
            object_id=kwargs["object_id"],
            content_ref=kwargs["content_ref"],
        )


class _ObjectStore:
    async def put_text(
        self,
        *,
        text: str,
        object_key: str,
        context: RequestContext,
    ) -> ObjectReference:
        return ObjectReference(
            p2_bucket="p3-memory",
            object_key=object_key,
            content_ref=f"p2://p3-memory/{object_key}",
        )


def _runtime(
    embedding: _RecordingEmbedding | None = None,
    memory_events: _RecordingMemoryEvents | None = None,
) -> tuple[MemoryRuntime, _RecordingEmbedding, _RecordingMemoryEvents]:
    resolved_embedding = embedding or _RecordingEmbedding()
    resolved_memory_events = memory_events or _RecordingMemoryEvents()
    runtime = MemoryRuntime(
        dependencies=RuntimeDependencies(
            embedding=resolved_embedding,
            memory_events=resolved_memory_events,
            context_builder=_ContextBuilder(),
            task_queue=_TaskQueue(),
            object_store=_ObjectStore(),
        ),
        profile=RuntimeProfile.LOCAL,
    )
    return runtime, resolved_embedding, resolved_memory_events


async def test_runtime_calls_b1_adapter_and_preserves_context() -> None:
    runtime, embedding, _ = _runtime()
    context = RequestContext.from_values(request_id="request-1", trace_id="trace-1")

    result = await runtime.embed(
        EmbeddingRequest(text="hello", source_type=SourceType.USER),
        context,
    )

    assert result.request_id == "request-1"
    assert result.trace_id == "trace-1"
    assert embedding.calls[0][1] is context


async def test_runtime_calls_existing_memory_implementation() -> None:
    runtime, _, memory_events = _runtime()
    context = RequestContext.from_values(
        request_id="request-2",
        trace_id="trace-2",
        tenant_id="tenant-1",
        user_id="user-1",
        agent_id="agent-1",
        session_id="session-1",
    )

    memory = await runtime.write_memory(
        MemoryEvent(
            event_type=MemoryEventType.USER_MEMORY,
            session_id="session-1",
            agent_id="agent-1",
            user_id="user-1",
            tenant_id="tenant-1",
            content="remember this",
        ),
        context,
    )

    assert memory.request_id == "request-2"
    assert memory.trace_id == "trace-2"
    assert memory_events.items[0].content == "remember this"


async def test_b1_failure_does_not_delete_memory_fact() -> None:
    runtime, _, memory_events = _runtime(embedding=_RecordingEmbedding(fail=True))
    context = RequestContext.from_values(
        request_id="request-3",
        trace_id="trace-3",
        tenant_id="tenant-1",
        user_id="user-1",
        agent_id="agent-1",
        session_id="session-1",
    )
    memory = await runtime.write_memory(
        MemoryEvent(
            event_type=MemoryEventType.USER_MEMORY,
            session_id="session-1",
            agent_id="agent-1",
            user_id="user-1",
            tenant_id="tenant-1",
            content="primary fact",
        ),
        context,
    )

    with pytest.raises(RuntimeError, match="b1 failed"):
        await runtime.embed(
            EmbeddingRequest(text="primary fact", source_type=SourceType.USER),
            context,
        )

    assert memory_events.items[0].id == memory.id
    assert memory_events.items[0].content == "primary fact"


async def test_long_memory_response_keeps_existing_schema_keys() -> None:
    runtime, _, _ = _runtime()
    result = await runtime.submit_long_memory(
        text="long document",
        tenant_id="tenant-1",
        user_id="user-1",
        agent_id="agent-1",
        session_id="session-1",
        source_id="doc-1",
        context=RequestContext.from_values(
            request_id="request-4",
            trace_id="trace-4",
            tenant_id="tenant-1",
            user_id="user-1",
            agent_id="agent-1",
            session_id="session-1",
        ),
    )

    for key in (
        "task_id",
        "memory_id",
        "state",
        "request_id",
        "trace_id",
        "source_id",
        "object_id",
        "content_ref",
    ):
        assert key in result.to_response_dict()
    assert result.formation["status"] == "FORMATION_PENDING"


async def test_runtime_rejects_incomplete_session_scope_before_store_access() -> None:
    embedding = _RecordingEmbedding()
    memory_events = _RecordingMemoryEvents()
    runtime = MemoryRuntime(
        dependencies=RuntimeDependencies(
            embedding=embedding,
            memory_events=memory_events,
            context_builder=_ContextBuilder(),
            task_queue=_TaskQueue(),
            object_store=_ObjectStore(),
            session_store=InMemorySessionStore(),
        ),
        profile=RuntimeProfile.LOCAL,
    )
    context = RequestContext.from_values(
        tenant_id="tenant-1",
        user_id="user-1",
        agent_id="agent-1",
    )

    with pytest.raises(ScopeError, match="requires session_id"):
        await runtime.append_session_message(
            SessionMessage(role=SessionMessageRole.USER, content="must not persist"),
            context,
        )


async def test_projection_reconcile_derives_authorized_scope_from_context() -> None:
    class _ProjectionWork:
        def __init__(self) -> None:
            self.scopes = []

        async def reconcile(self, scope, *, session_only):
            self.scopes.append((scope, session_only))
            return []

    work = _ProjectionWork()
    runtime = object.__new__(MemoryRuntime)
    runtime._projection_work = work
    context = RequestContext.from_values(
        tenant_id="tenant-1",
        user_id="user-1",
        agent_id="agent-1",
        session_id="session-1",
    )

    await runtime.reconcile_projection_work(context, session_only=True)

    assert work.scopes == [(context.scope, True)]

    with pytest.raises(ScopeError, match="requires tenant/user/agent scope"):
        await runtime.reconcile_projection_work(RequestContext(), session_only=False)


async def test_memory_data_use_cases_reject_mismatched_authenticated_scope() -> None:
    runtime, _, memory_events = _runtime()
    foreign = RequestContext.from_values(
        tenant_id="tenant-2",
        user_id="user-1",
        agent_id="agent-1",
        session_id="session-1",
    )

    with pytest.raises(ScopeError, match="does not match"):
        await runtime.write_memory(
            MemoryEvent(
                event_type=MemoryEventType.USER_MEMORY,
                tenant_id="tenant-1",
                user_id="user-1",
                agent_id="agent-1",
                session_id="session-1",
                content="private",
            ),
            foreign,
        )
    with pytest.raises(ScopeError, match="does not match"):
        await runtime.submit_long_memory(
            text="private document",
            tenant_id="tenant-1",
            user_id="user-1",
            agent_id="agent-1",
            session_id="session-1",
            source_id="doc-1",
            context=foreign,
        )
    with pytest.raises(ScopeError, match="does not match"):
        await runtime.search_memory(
            query="private",
            tenant_id="tenant-1",
            user_id="user-1",
            agent_id="agent-1",
            limit=5,
            context=foreign,
        )
    with pytest.raises(ScopeError, match="does not match"):
        await runtime.build_context(
            ContextRequest(
                tenant_id="tenant-1",
                user_id="user-1",
                agent_id="agent-1",
                session_id="session-1",
                query="private",
            ),
            foreign,
        )

    assert memory_events.items == []
