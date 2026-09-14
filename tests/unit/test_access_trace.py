from __future__ import annotations

import pytest

from aether_agent_memory.adapters.access_trace import InMemoryAccessTraceAdapter
from aether_agent_memory.application.services import record_access, record_access_many
from aether_agent_memory.memory.retrieval.models import AccessTrace
from aether_agent_memory.runtime.dependencies import RuntimeDependencies


def _trace(memory_id: str) -> AccessTrace:
    return AccessTrace(
        trace_id="trace-1",
        request_id="request-1",
        memory_id=memory_id,
        source="context",
    )


@pytest.mark.asyncio
async def test_access_trace_adapter_accepts_a_result_set_in_one_call() -> None:
    adapter = InMemoryAccessTraceAdapter()
    dependencies = RuntimeDependencies(
        embedding=None,  # type: ignore[arg-type]
        memory_events=None,  # type: ignore[arg-type]
        context_builder=None,  # type: ignore[arg-type]
        access_trace=adapter,
    )

    await record_access_many(
        dependencies,
        [_trace("memory-1"), _trace("memory-2")],
    )

    assert [item.memory_id for item in adapter.items] == ["memory-1", "memory-2"]


@pytest.mark.asyncio
async def test_empty_access_trace_batch_is_a_noop() -> None:
    adapter = InMemoryAccessTraceAdapter()
    dependencies = RuntimeDependencies(
        embedding=None,  # type: ignore[arg-type]
        memory_events=None,  # type: ignore[arg-type]
        context_builder=None,  # type: ignore[arg-type]
        access_trace=adapter,
    )

    await record_access_many(dependencies, [])

    assert adapter.items == []


def _dependencies(adapter):
    return RuntimeDependencies(
        embedding=None,
        memory_events=None,
        context_builder=None,
        access_trace=adapter,
    )


async def test_single_record_preserves_scope_and_identifiers() -> None:
    from aether_agent_memory.runtime.dependencies import RuntimeProfile
    from aether_agent_memory.runtime.service import MemoryRuntime

    adapter = InMemoryAccessTraceAdapter()
    trace = AccessTrace(
        trace_id="trace",
        request_id="request",
        memory_id="memory",
        source="context",
        tenant_id="tenant",
        user_id="user",
        agent_id="agent",
        session_id="session",
        task_id="task",
        metadata={"purpose": "audit"},
    )
    runtime = MemoryRuntime(dependencies=_dependencies(adapter), profile=RuntimeProfile.INTEGRATION)
    await runtime.record_access(trace)
    assert len(adapter.items) == 1
    payload = adapter.items[0].model_dump()
    assert {
        name: payload[name]
        for name in (
            "tenant_id",
            "user_id",
            "agent_id",
            "session_id",
            "task_id",
            "trace_id",
            "request_id",
        )
    } == {
        "tenant_id": "tenant",
        "user_id": "user",
        "agent_id": "agent",
        "session_id": "session",
        "task_id": "task",
        "trace_id": "trace",
        "request_id": "request",
    }
    assert payload["metadata"] == {"purpose": "audit"}


async def test_batch_prefers_record_many_without_single_calls() -> None:
    class BatchAdapter:
        batches = []

        async def record_many(self, traces):
            self.batches.append(traces)

        async def record(self, trace):
            pytest.fail("batch adapter should receive one record_many call")

    adapter = BatchAdapter()
    traces = [_trace("first"), _trace("second")]
    await record_access_many(_dependencies(adapter), traces)
    assert adapter.batches == [traces]


async def test_legacy_batch_records_each_trace_exactly_once() -> None:
    class LegacyAdapter:
        items = []

        async def record(self, trace):
            self.items.append(trace)

    adapter = LegacyAdapter()
    traces = [_trace("first"), _trace("second")]
    await record_access_many(_dependencies(adapter), traces)
    assert adapter.items == traces


async def test_failed_batch_is_not_replayed_after_partial_write() -> None:
    class PartialAdapter:
        def __init__(self):
            self.items = []
            self.single_calls = 0

        async def record_many(self, traces):
            self.items.append(traces[0])
            raise ConnectionError("partial telemetry failure")

        async def record(self, trace):
            self.single_calls += 1

    adapter = PartialAdapter()
    traces = [_trace("first"), _trace("second")]
    await record_access_many(_dependencies(adapter), traces)
    assert adapter.items == traces[:1]
    assert adapter.single_calls == 0


async def test_legacy_batch_continues_after_one_failed_trace() -> None:
    class PartialAdapter:
        def __init__(self):
            self.attempts = []

        async def record(self, trace):
            self.attempts.append(trace)
            if trace.memory_id == "first":
                raise ConnectionError("one telemetry failure")

    adapter = PartialAdapter()
    traces = [_trace("first"), _trace("second")]
    await record_access_many(_dependencies(adapter), traces)
    assert adapter.attempts == traces


@pytest.mark.parametrize("mode", ["single", "batch", "legacy_batch"])
async def test_telemetry_failure_is_best_effort(mode) -> None:
    class FailingAdapter:
        async def record(self, trace):
            raise ConnectionError("telemetry unavailable")

    class FailingBatchAdapter(FailingAdapter):
        async def record_many(self, traces):
            raise ConnectionError("telemetry unavailable")

    adapter = FailingBatchAdapter() if mode == "batch" else FailingAdapter()
    deps = _dependencies(adapter)
    if mode == "single":
        await record_access(deps, _trace("memory"))
    else:
        await record_access_many(deps, [_trace("memory")])


@pytest.mark.parametrize("operation", ["context", "search"])
@pytest.mark.parametrize("unavailable", [False, True])
async def test_recall_keeps_business_result_and_telemetry_scope(operation, unavailable) -> None:
    from datetime import UTC, datetime

    from aether_agent_memory.application.services import BuildContextUseCase, SearchMemoryUseCase
    from aether_agent_memory.context import ContextPack, ContextRequest
    from aether_agent_memory.runtime.dtos import MemorySearchHit, MemorySearchResult
    from aether_agent_memory.runtime.request_context import RequestContext

    items = []

    class Adapter:
        async def record_many(self, traces):
            if unavailable:
                raise ConnectionError("telemetry unavailable")
            items.extend(traces)

    class Builder:
        async def build_context(self, request, context):
            return ContextPack(
                request=request,
                memories=[],
                total_tokens=0,
                budget_tokens=100,
                recall_scores={"memory": 0.9},
                assembled_text="fact",
                built_at=datetime.now(UTC),
            )

    class Search:
        async def search_memory(self, **kwargs):
            return MemorySearchResult(
                items=[MemorySearchHit(memory_id="memory", text="fact", score=0.9)],
                backend="test",
            )

    context = RequestContext.from_values(
        tenant_id="tenant",
        user_id="user",
        agent_id="agent",
        session_id="session",
        request_id="request",
        trace_id="trace",
    )
    deps = _dependencies(Adapter())
    deps.context_builder = Builder()
    deps.vector_search = Search()
    if operation == "context":
        result = await BuildContextUseCase(deps).execute(
            ContextRequest(
                tenant_id="tenant",
                user_id="user",
                agent_id="agent",
                session_id="session",
                query="fact",
            ),
            context,
        )
        assert result.assembled_text == "fact"
    else:
        result = await SearchMemoryUseCase(deps).execute(
            query="fact",
            tenant_id="tenant",
            user_id="user",
            agent_id="agent",
            limit=1,
            context=context,
        )
        assert result.items[0].text == "fact"
    if unavailable:
        assert items == []
    else:
        assert len(items) == 1
        payload = items[0].model_dump()
        for field in ("tenant_id", "user_id", "agent_id", "session_id", "trace_id", "request_id"):
            assert payload[field] == getattr(context, field)
