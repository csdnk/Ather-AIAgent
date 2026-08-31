from __future__ import annotations

import pytest

from aether_agent_memory.application import GetRetrievalTraceUseCase
from aether_agent_memory.context import ContextPack, ContextRequest
from aether_agent_memory.context_store.in_memory import InMemoryRetrievalTraceStore
from aether_agent_memory.context_store.models import RetrievalTrace
from aether_agent_memory.core.scope import Scope
from aether_agent_memory.memory.retrieval.models import RecallCandidate
from aether_agent_memory.memory.retrieval.service import MemoryRetrievalService
from aether_agent_memory.runtime.dependencies import RuntimeDependencies
from aether_agent_memory.runtime.errors import ScopeError
from aether_agent_memory.runtime.request_context import RequestContext


class _Source:
    @property
    def name(self) -> str:
        return "crm"

    async def recall(
        self,
        request: ContextRequest,
        context: RequestContext,
    ) -> list[RecallCandidate]:
        return [
            RecallCandidate(
                memory_id="crm-1",
                scope=context.scope,
                content="customer context",
                source=self.name,
                score=0.9,
            )
        ]


def _request() -> ContextRequest:
    return ContextRequest(
        tenant_id="tenant-1",
        user_id="user-1",
        agent_id="agent-1",
        session_id="session-1",
        query="customer",
    )


def _context(*, tenant_id: str = "tenant-1") -> RequestContext:
    return RequestContext.from_values(
        trace_id="trace-1",
        tenant_id=tenant_id,
        user_id="user-1",
        agent_id="agent-1",
        session_id="session-1",
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_retrieval_service_persists_scope_aware_trace() -> None:
    store = InMemoryRetrievalTraceStore()
    service = MemoryRetrievalService([_Source()], trace_store=store)

    await service.recall(_request(), _context())

    trace = await store.get("trace-1")
    assert trace is not None
    assert trace.scope == _context().scope
    assert trace.steps


@pytest.mark.unit
@pytest.mark.asyncio
async def test_context_augmentation_persists_only_final_trace() -> None:
    class _CountingStore(InMemoryRetrievalTraceStore):
        writes = 0

        async def put(self, trace: RetrievalTrace) -> None:
            self.writes += 1
            await super().put(trace)

    store = _CountingStore()
    service = MemoryRetrievalService([_Source()], trace_store=store)
    request = _request()
    pack = ContextPack(
        request=request,
        memories=[],
        total_tokens=0,
        budget_tokens=request.max_tokens,
        recall_scores={},
        assembled_text="",
        built_at=_context().created_at,
    )

    await service.augment_context(pack, request, _context())

    assert store.writes == 1
    trace = await store.get("trace-1")
    assert trace is not None
    assert any(step.action.value == "context_selected" for step in trace.steps)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_trace_use_case_rejects_cross_tenant_read() -> None:
    store = InMemoryRetrievalTraceStore()
    await store.put(
        RetrievalTrace(
            trace_id="trace-1",
            query="private",
            scope=_context().scope,
        )
    )
    use_case = GetRetrievalTraceUseCase(
        RuntimeDependencies(
            embedding=None,  # type: ignore[arg-type]
            memory_events=None,  # type: ignore[arg-type]
            context_builder=None,  # type: ignore[arg-type]
            retrieval_trace_store=store,
        )
    )

    with pytest.raises(ScopeError):
        await use_case.execute("trace-1", _context(tenant_id="tenant-2"))

    same_agent_without_session = RequestContext.from_values(
        tenant_id="tenant-1",
        user_id="user-1",
        agent_id="agent-1",
    )
    with pytest.raises(ScopeError):
        await use_case.execute("trace-1", same_agent_without_session)

    wrong_session = RequestContext.from_values(
        tenant_id="tenant-1",
        user_id="user-1",
        agent_id="agent-1",
        session_id="session-2",
    )
    with pytest.raises(ScopeError):
        await use_case.execute("trace-1", wrong_session)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_in_memory_trace_store_is_bounded() -> None:
    store = InMemoryRetrievalTraceStore(max_entries=2)
    scope = Scope(tenant_id="t", user_id="u", agent_id="a")
    for index in range(3):
        await store.put(
            RetrievalTrace(
                trace_id=f"trace-{index}",
                query="q",
                scope=scope,
            )
        )

    assert await store.get("trace-0") is None
    assert await store.get("trace-1") is not None
    assert await store.get("trace-2") is not None


@pytest.mark.unit
@pytest.mark.asyncio
async def test_redis_trace_adapter_round_trips_typed_payload() -> None:
    from aether_agent_memory.adapters.retrieval_trace import RedisRetrievalTraceStore

    class _FakeRedis:
        def __init__(self) -> None:
            self.payloads: dict[str, str] = {}
            self.expirations: dict[str, int] = {}

        async def set(self, key: str, value: str, *, ex: int) -> None:
            self.payloads[key] = value
            self.expirations[key] = ex

        async def get(self, key: str) -> str | None:
            return self.payloads.get(key)

        async def aclose(self) -> None:
            return None

    store = RedisRetrievalTraceStore("redis://unused", ttl_seconds=60)
    fake = _FakeRedis()
    store._redis = fake  # type: ignore[assignment]
    trace = RetrievalTrace(
        trace_id="trace-sensitive:value",
        query="q",
        scope=Scope(tenant_id="t", user_id="u", agent_id="a"),
    )

    await store.put(trace)
    restored = await store.get(trace.trace_id)

    assert restored == trace
    assert list(fake.expirations.values()) == [60]
    assert all("trace-sensitive:value" not in key for key in fake.payloads)
