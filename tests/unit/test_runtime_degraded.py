from __future__ import annotations

from datetime import UTC, datetime

from aether_agent_memory.b1 import EmbeddingRequest, EmbeddingResult, ProcessingStatus
from aether_agent_memory.b2 import MemoryEvent, MemoryEventType
from aether_agent_memory.b3 import ScheduleRequest, ScheduleRunResult
from aether_agent_memory.context import ContextPack, ContextRequest
from aether_agent_memory.core.enums import MemoryType
from aether_agent_memory.core.memory import Memory
from aether_agent_memory.memory.retrieval.models import RecallCandidate
from aether_agent_memory.memory.retrieval.service import MemoryRetrievalService
from aether_agent_memory.runtime import MemoryRuntime, RuntimeDependencies, RuntimeProfile
from aether_agent_memory.runtime.request_context import RequestContext


class _Embedding:
    async def embed(self, request: EmbeddingRequest, context: RequestContext) -> EmbeddingResult:
        return EmbeddingResult(
            request_id=context.request_id,
            trace_id=context.trace_id,
            source_id=request.source_id or "source",
            status=ProcessingStatus.SUCCESS,
            latency_ms=0.0,
        )


class _MemoryEvents:
    async def write_memory(self, event: MemoryEvent, context: RequestContext) -> Memory:
        return Memory(
            type=MemoryType.WORKING,
            session_id=event.session_id,
            agent_id=event.agent_id,
            content=event.content,
            request_id=context.request_id,
            trace_id=context.trace_id,
        )


class _Context:
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


class _FailingScheduler:
    async def schedule(
        self,
        request: ScheduleRequest,
        context: RequestContext,
    ) -> ScheduleRunResult:
        raise RuntimeError("b3 failed")


class _RecallSource:
    def __init__(self, name: str, *, fail: bool = False) -> None:
        self._name = name
        self._fail = fail

    @property
    def name(self) -> str:
        return self._name

    async def recall(
        self,
        request: ContextRequest,
        context: RequestContext,
    ) -> list[RecallCandidate]:
        if self._fail:
            raise RuntimeError("source failed")
        return [
            RecallCandidate(
                memory_id="memory-1",
                content="working memory",
                source=self.name,
                score=0.9,
                memory_type=MemoryType.WORKING,
            )
        ]


async def test_b3_failure_does_not_block_memory_write_or_context() -> None:
    runtime = MemoryRuntime(
        dependencies=RuntimeDependencies(
            embedding=_Embedding(),
            memory_events=_MemoryEvents(),
            context_builder=_Context(),
            scheduler=_FailingScheduler(),
        ),
        profile=RuntimeProfile.LOCAL,
    )
    context = RequestContext.from_values(request_id="request-1", trace_id="trace-1")

    memory = await runtime.write_memory(
        MemoryEvent(
            event_type=MemoryEventType.AFTER_TURN,
            session_id="session-1",
            agent_id="agent-1",
            content="write survives",
        ),
        context,
    )
    pack = await runtime.build_context(
        ContextRequest(session_id="session-1", agent_id="agent-1", query="q"),
        context,
    )

    assert memory.content == "write survives"
    assert pack.complete is True


async def test_one_recall_source_failure_returns_degraded_result() -> None:
    retrieval = MemoryRetrievalService(
        [
            _RecallSource("working"),
            _RecallSource("long_term", fail=True),
        ]
    )

    result = await retrieval.recall(
        ContextRequest(session_id="session-1", agent_id="agent-1", query="q"),
        RequestContext.from_values(request_id="request-2", trace_id="trace-2"),
    )

    assert result.complete is False
    assert result.missing_sources == ["long_term"]
    assert result.candidates[0].memory_id == "memory-1"
