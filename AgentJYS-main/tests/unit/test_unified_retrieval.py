"""Unified retrieval fusion tests: Working + Long-document through MemoryRetrievalService."""

from __future__ import annotations

from typing import Any

import pytest

from aether_agent_memory.context.models import ContextRequest
from aether_agent_memory.context_store import (
    ContextItemKind,
    ContextLayer,
    RetrievalTraceAction,
)
from aether_agent_memory.core.enums import MemoryType
from aether_agent_memory.core.memory import Memory, RecalledMemory
from aether_agent_memory.memory.retrieval.models import RecallCandidate
from aether_agent_memory.memory.retrieval.service import MemoryRetrievalService
from aether_agent_memory.memory.retrieval.sources import P2E1RecallSource, WorkingRecallSource
from aether_agent_memory.runtime.request_context import RequestContext


class _FakeWorkingManager:
    async def recall(self, request: ContextRequest) -> list[RecalledMemory]:
        memory = Memory(
            type=MemoryType.WORKING,
            session_id="s",
            agent_id="a",
            user_id="u",
            tenant_id="t",
            content="working memory item",
        )
        return [RecalledMemory(memory=memory, score=0.8)]


class _FakeVectorSearch:
    async def search_memory(self, **_: Any) -> dict[str, Any]:
        return {
            "items": [
                {
                    "memory_id": "ld-1",
                    "chunk_id": "c1",
                    "text": "document chunk from P2 E1",
                    "score": 0.91,
                    "content_ref": "p2://p3-memory/obj",
                    "trace_id": "trace-1",
                }
            ],
            "backend": "p2-e1",
        }


def _request() -> ContextRequest:
    return ContextRequest(
        session_id="s", agent_id="a", user_id="u", tenant_id="t", query="query"
    )


def _scope() -> RequestContext:
    return RequestContext.from_values(
        tenant_id="t", user_id="u", agent_id="a", session_id="s"
    )


@pytest.mark.asyncio
async def test_unified_retrieval_fuses_working_and_long_document() -> None:
    service = MemoryRetrievalService(
        [
            WorkingRecallSource(_FakeWorkingManager()),
            P2E1RecallSource(_FakeVectorSearch()),
        ]
    )
    result = await service.recall(_request(), _scope())

    assert len(result.candidates) == 2
    sources = {candidate.source for candidate in result.candidates}
    assert sources == {"working", "long_document"}
    # score-descending fusion, unchanged ranking semantics
    assert result.candidates[0].score >= result.candidates[1].score
    assert result.complete is True
    assert result.missing_sources == []
    assert len(result.context_candidates) == 2
    assert {candidate.kind for candidate in result.context_candidates} == {
        ContextItemKind.MEMORY,
        ContextItemKind.RESOURCE,
    }
    assert all(
        candidate.layer == ContextLayer.DETAIL
        and str(candidate.uri).startswith("aether://")
        for candidate in result.context_candidates
    )
    assert result.retrieval_trace is not None
    assert result.retrieval_trace.complete is True
    assert any(
        step.action == RetrievalTraceAction.CANDIDATE_SCORED
        and step.uri is not None
        and str(step.uri).startswith("aether://")
        for step in result.retrieval_trace.steps
    )


@pytest.mark.asyncio
async def test_p2e1_source_marks_failure_as_missing_source() -> None:
    class _BrokenVectorSearch:
        async def search_memory(self, **_: Any) -> dict[str, Any]:
            raise RuntimeError("P2 down")

    service = MemoryRetrievalService(
        [
            WorkingRecallSource(_FakeWorkingManager()),
            P2E1RecallSource(_BrokenVectorSearch()),
        ]
    )
    result = await service.recall(_request(), _scope())

    assert result.complete is False
    assert "long_document" in result.missing_sources
    # working candidates survive the degraded long-document source
    assert any(candidate.source == "working" for candidate in result.candidates)
    assert result.retrieval_trace is not None
    assert any(
        step.action == RetrievalTraceAction.DEGRADED
        and step.source == "long_document"
        and "P2 down" in (step.reason or "")
        for step in result.retrieval_trace.steps
    )


@pytest.mark.asyncio
async def test_p2e1_source_returns_recall_candidates() -> None:
    candidates = await P2E1RecallSource(_FakeVectorSearch()).recall(
        _request(), _scope()
    )
    assert len(candidates) == 1
    candidate = candidates[0]
    assert isinstance(candidate, RecallCandidate)
    assert candidate.memory_id == "ld-1"
    assert candidate.score == pytest.approx(0.91)
    assert candidate.content_ref == "p2://p3-memory/obj"
