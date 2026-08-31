from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

import pytest

from aether_agent_memory.api.mappers import (
    long_memory_submission_to_v1,
    memory_search_result_to_v1,
    task_status_record_to_v1,
)
from aether_agent_memory.application.services import BuildContextUseCase
from aether_agent_memory.context.models import ContextPack, ContextRequest
from aether_agent_memory.core.enums import MemoryType
from aether_agent_memory.core.memory import Memory, RecalledMemory
from aether_agent_memory.memory.retrieval.models import RecallCandidate
from aether_agent_memory.memory.retrieval.service import MemoryRetrievalService
from aether_agent_memory.memory.retrieval.sources import (
    EpisodicRecallSource,
    P2E1RecallSource,
    SemanticRecallSource,
    WorkingRecallSource,
)
from aether_agent_memory.runtime.dependencies import RuntimeDependencies, RuntimeProfile
from aether_agent_memory.runtime.dtos import (
    LongMemorySubmission,
    MemorySearchResult,
    ObjectReference,
    TaskStatusRecord,
)
from aether_agent_memory.runtime.request_context import RequestContext
from aether_agent_memory.runtime.service import MemoryRuntime


class _UnusedContextBuilder:
    calls = 0

    async def build_context(
        self,
        request: ContextRequest,
        context: RequestContext,
    ) -> ContextPack:
        self.calls += 1
        return ContextPack(
            request=request,
            memories=[],
            total_tokens=0,
            budget_tokens=request.max_tokens,
            recall_scores={},
            assembled_text="",
            built_at=datetime.now(UTC),
        )


class _Manager:
    def __init__(self, memory_type: MemoryType, memory_id: str, score: float) -> None:
        self.memory_type = memory_type
        self.memory_id = memory_id
        self.score = score
        self.calls = 0

    async def recall(self, request: ContextRequest) -> list[RecalledMemory]:
        self.calls += 1
        return [
            RecalledMemory(
                memory=Memory(
                    id=self.memory_id,
                    type=self.memory_type,
                    session_id=request.session_id,
                    agent_id=request.agent_id,
                    user_id=request.user_id,
                    tenant_id=request.tenant_id,
                    content=f"{self.memory_type.value} memory",
                    metadata={"evidence_refs": [f"memory://{self.memory_id}"]},
                ),
                score=self.score,
            )
        ]


class _VectorSearch:
    def __init__(self, *, fail: bool = False, delay: float = 0.0) -> None:
        self.calls = 0
        self.fail = fail
        self.delay = delay

    async def search_memory(self, **_: Any) -> dict[str, Any]:
        self.calls += 1
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.fail:
            raise RuntimeError("vector search failed")
        return {
            "backend": "p2-e1",
            "collection": "p3-b2-d512-test",
            "items": [
                {
                    "memory_id": "long-1",
                    "chunk_id": "chunk-1",
                    "text": "long document memory",
                    "score": 0.95,
                    "content_ref": "p2://bucket/object",
                }
            ],
        }


class _CrmRecallSource:
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
                content="crm memory",
                content_ref="crm://account/1",
                source=self.name,
                score=0.88,
            )
        ]


def _request(**kwargs: Any) -> ContextRequest:
    return ContextRequest(
        session_id="s",
        agent_id="a",
        user_id="u",
        tenant_id="t",
        query="memory",
        **kwargs,
    )


def _context() -> RequestContext:
    return RequestContext.from_values(
        tenant_id="t",
        user_id="u",
        agent_id="a",
        session_id="s",
        request_id="request-1",
        trace_id="trace-1",
    )


@pytest.mark.asyncio
async def test_context_uses_unified_retrieval_without_legacy_builder_recall() -> None:
    builder = _UnusedContextBuilder()
    working = _Manager(MemoryType.WORKING, "working-1", 0.7)
    episodic = _Manager(MemoryType.EPISODIC, "episodic-1", 0.8)
    semantic = _Manager(MemoryType.SEMANTIC, "semantic-1", 0.9)
    vector = _VectorSearch()
    runtime = MemoryRuntime(
        dependencies=RuntimeDependencies(
            embedding=None,  # type: ignore[arg-type]
            memory_events=None,  # type: ignore[arg-type]
            context_builder=builder,
            retrieval=MemoryRetrievalService(
                [
                    WorkingRecallSource(working),
                    EpisodicRecallSource(episodic),
                    SemanticRecallSource(semantic),
                    P2E1RecallSource(vector),
                ]
            ),
        ),
        profile=RuntimeProfile.LOCAL,
    )

    pack = await runtime.build_context(_request(), _context())

    assert builder.calls == 0
    assert working.calls == 1
    assert episodic.calls == 1
    assert semantic.calls == 1
    assert vector.calls == 1
    assert {memory.id for memory in pack.memories} == {
        "working-1",
        "episodic-1",
        "semantic-1",
        "long-1",
    }


@pytest.mark.asyncio
async def test_future_recall_source_does_not_change_build_context_flow() -> None:
    builder = _UnusedContextBuilder()
    use_case = BuildContextUseCase(
        RuntimeDependencies(
            embedding=None,  # type: ignore[arg-type]
            memory_events=None,  # type: ignore[arg-type]
            context_builder=builder,
            retrieval=MemoryRetrievalService([_CrmRecallSource()]),
        )
    )

    pack = await use_case.execute(_request(), _context())

    assert builder.calls == 0
    assert [memory.id for memory in pack.memories] == ["crm-1"]
    assert pack.evidence_refs == ["crm://account/1"]


@pytest.mark.asyncio
async def test_recall_source_exception_degrades_but_keeps_other_results() -> None:
    working = _Manager(MemoryType.WORKING, "working-1", 0.7)
    result = await MemoryRetrievalService(
        [WorkingRecallSource(working), P2E1RecallSource(_VectorSearch(fail=True))]
    ).recall(_request(), _context())

    assert result.complete is False
    assert "long_document" in result.missing_sources
    assert [candidate.memory_id for candidate in result.candidates] == ["working-1"]


@pytest.mark.asyncio
async def test_recall_source_timeout_degrades_but_keeps_other_results() -> None:
    working = _Manager(MemoryType.WORKING, "working-1", 0.7)
    result = await MemoryRetrievalService(
        [WorkingRecallSource(working), P2E1RecallSource(_VectorSearch(delay=0.05))]
    ).recall(_request(deadline_ms=1), _context())

    assert result.complete is False
    assert "long_document" in result.missing_sources
    assert [candidate.memory_id for candidate in result.candidates] == ["working-1"]


@pytest.mark.asyncio
async def test_token_budget_is_applied_after_all_sources_once() -> None:
    short = _Manager(MemoryType.WORKING, "short", 0.4)
    long = _Manager(MemoryType.SEMANTIC, "long", 0.9)

    async def recall_long(request: ContextRequest) -> list[RecalledMemory]:
        long.calls += 1
        return [
            RecalledMemory(
                memory=Memory(
                    id="long",
                    type=MemoryType.SEMANTIC,
                    session_id="s",
                    agent_id="a",
                    content="x" * 100,
                ),
                score=0.9,
            )
        ]

    long.recall = recall_long  # type: ignore[method-assign]
    service = MemoryRetrievalService(
        [WorkingRecallSource(short), SemanticRecallSource(long)]
    )
    pack = ContextPack(
        request=_request(max_tokens=5),
        memories=[],
        total_tokens=0,
        budget_tokens=5,
        recall_scores={},
        assembled_text="",
        built_at=datetime.now(UTC),
    )

    await service.augment_context(pack, _request(max_tokens=5), _context())

    assert pack.total_tokens <= 5
    assert [memory.id for memory in pack.memories] == ["short"]


def test_canonical_dtos_do_not_require_provider_specific_fields() -> None:
    object_ref = ObjectReference(
        provider="p2",
        namespace="bucket",
        object_key="object",
        content_ref="p2://bucket/object",
    )
    submission = LongMemorySubmission(
        task_id="task",
        memory_id="memory",
        state="PENDING",
        request_id="request",
        trace_id="trace",
        source_id="source",
        provider="p2",
        namespace="bucket",
        object_key=object_ref.object_key,
        content_ref=object_ref.content_ref,
    )
    record = TaskStatusRecord(
        task_id="task",
        state="SUCCEEDED",
        provider="p2",
        projection_count=3,
        projection_namespace="collection",
    )

    assert object_ref.namespace == "bucket"
    assert submission.namespace == "bucket"
    assert record.projection_namespace == "collection"
    for model in (object_ref, submission, record):
        assert not any(name.startswith("p2_") for name in type(model).model_fields)


def test_legacy_p2_task_payload_maps_to_canonical_and_v1_response() -> None:
    record = TaskStatusRecord.from_mapping(
        {
            "task_id": "task",
            "state": "SUCCEEDED",
            "p2_vector_count": 5,
            "p2_collection": "p3-b2-d512-test",
        }
    )

    assert record.projection_count == 5
    assert record.projection_namespace == "p3-b2-d512-test"
    response = task_status_record_to_v1(record)
    assert response["p2_vector_count"] == 5
    assert response["p2_collection"] == "p3-b2-d512-test"


def test_v1_response_preserves_long_memory_and_search_legacy_fields() -> None:
    submission = LongMemorySubmission.from_mapping(
        {
            "task_id": "task",
            "memory_id": "memory",
            "state": "PENDING",
            "request_id": "request",
            "trace_id": "trace",
            "source_id": "source",
            "p2_bucket": "bucket",
            "object_key": "object",
            "content_ref": "p2://bucket/object",
        }
    )
    search = MemorySearchResult.from_mapping(
        {
            "backend": "p2-e1",
            "collection": "collection",
            "items": [],
        }
    )

    assert long_memory_submission_to_v1(submission)["p2_bucket"] == "bucket"
    assert memory_search_result_to_v1(search)["collection"] == "collection"
