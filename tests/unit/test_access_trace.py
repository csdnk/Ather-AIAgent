from __future__ import annotations

import pytest

from aether_agent_memory.adapters.access_trace import InMemoryAccessTraceAdapter
from aether_agent_memory.application.services import record_access_many
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
