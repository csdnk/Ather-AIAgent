"""Long-document recall tests for the unified /context path."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest

from aether_agent_memory.context.models import ContextPack, ContextRequest
from aether_agent_memory.core.enums import MemoryType
from aether_agent_memory.core.memory import Memory
from aether_agent_memory.runtime.request_context import RequestContext
from aether_agent_memory.runtime.service import MemoryRuntime


class _FakeVectorSearch:
    def __init__(self, items: list[dict[str, Any]] | None = None, *, fail: bool = False) -> None:
        self._items = items or []
        self._fail = fail

    async def search_memory(self, **_: Any) -> dict[str, Any]:
        if self._fail:
            raise RuntimeError("P2 down")
        return {"items": self._items, "backend": "p2-e1"}


def _request() -> ContextRequest:
    return ContextRequest(
        session_id="s",
        agent_id="a",
        user_id="u",
        tenant_id="t",
        query="what is in the document?",
    )


def _pack() -> ContextPack:
    request = _request()
    return ContextPack(
        request=request,
        memories=[],
        total_tokens=0,
        budget_tokens=4096,
        recall_scores={},
        assembled_text="",
        built_at=datetime.now(UTC),
    )


def _scope() -> RequestContext:
    return RequestContext.from_values(
        tenant_id="t", user_id="u", agent_id="a", session_id="s"
    )


def _runtime(search: _FakeVectorSearch) -> MemoryRuntime:
    runtime = object.__new__(MemoryRuntime)
    runtime.dependencies = SimpleNamespace(vector_search=search)
    return runtime


@pytest.mark.asyncio
async def test_long_document_recall_appended_to_context() -> None:
    search = _FakeVectorSearch(
        items=[
            {
                "memory_id": "m1",
                "chunk_id": "c1",
                "text": "document chunk text",
                "score": 0.91,
                "content_ref": "p2://p3-memory/obj",
            }
        ]
    )
    pack = _pack()
    await _runtime(search)._recall_long_documents(pack, _request(), _scope())

    assert len(pack.memories) == 1
    assert pack.memories[0].metadata["source"] == "long_document"
    assert pack.memories[0].embedding_status == "succeeded"
    assert pack.memories[0].vector_projection_status == "succeeded"
    assert pack.memories[0].metadata["embedding_status"] == "succeeded"
    assert pack.memories[0].metadata["vector_projection_status"] == "succeeded"
    assert pack.recall_scores["m1"] == pytest.approx(0.91)
    assert pack.memory_refs == ["m1"]
    assert pack.evidence_refs == ["p2://p3-memory/obj"]
    assert "long_document" in pack.assembled_text


@pytest.mark.asyncio
async def test_long_document_recall_degrades_on_p2_failure() -> None:
    pack = _pack()
    await _runtime(_FakeVectorSearch(fail=True))._recall_long_documents(
        pack, _request(), _scope()
    )

    assert pack.status == "degraded"
    assert pack.complete is False
    assert "long_document" in pack.missing_sources
    assert pack.memories == []


@pytest.mark.asyncio
async def test_long_document_recall_skips_without_scope() -> None:
    search = _FakeVectorSearch(items=[{"memory_id": "m1", "text": "x", "score": 1.0}])
    pack = _pack()
    request = ContextRequest(session_id="s", agent_id="a", query="q")
    await _runtime(search)._recall_long_documents(
        pack, request, RequestContext.from_values()
    )
    assert pack.memories == []


def test_trim_to_budget_keeps_total_within_budget() -> None:
    runtime = _runtime(_FakeVectorSearch())
    pack = _pack()
    high = Memory(
        type=MemoryType.SEMANTIC, session_id="s", agent_id="a", content="h" * 12000
    )
    low = Memory(
        type=MemoryType.WORKING, session_id="s", agent_id="a", content="l" * 12000
    )
    pack.memories = [low, high]
    pack.recall_scores = {high.id: 0.9, low.id: 0.1}
    pack.total_tokens = 6000  # > budget 4096

    runtime._trim_to_budget(pack)

    assert pack.total_tokens <= pack.budget_tokens
    assert pack.memories == [high]  # higher-score memory survives


def test_trim_to_budget_unchanged_within_budget() -> None:
    runtime = _runtime(_FakeVectorSearch())
    pack = _pack()
    memory = Memory(
        type=MemoryType.SEMANTIC, session_id="s", agent_id="a", content="small"
    )
    pack.memories = [memory]
    pack.recall_scores = {memory.id: 0.9}
    pack.total_tokens = 10

    runtime._trim_to_budget(pack)

    assert pack.total_tokens == 10
    assert pack.memories == [memory]
