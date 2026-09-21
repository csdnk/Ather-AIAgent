from __future__ import annotations

import pytest

from aether_agent_memory.b1 import EmbeddingRequest, EmbeddingResult, ProcessingStatus
from aether_agent_memory.b2 import MemoryEvent
from aether_agent_memory.context import ContextPack, ContextRequest
from aether_agent_memory.core.enums import MemoryType, SourceType
from aether_agent_memory.core.memory import Memory
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
            user_id=event.user_id,
            tenant_id=event.tenant_id,
            request_id=context.request_id,
            trace_id=context.trace_id,
            content=event.content,
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
            built_at=context.created_at,
            trace_id=context.trace_id,
        )


def test_runtime_profile_reads_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AETHER_RUNTIME_PROFILE", "production")
    assert RuntimeProfile.from_environment() == RuntimeProfile.PRODUCTION
    monkeypatch.setenv("AETHER_RUNTIME_PROFILE", "demo")
    assert RuntimeProfile.from_environment() == RuntimeProfile.DEMO


async def test_mock_demo_profile_works_with_injected_dependencies() -> None:
    runtime = MemoryRuntime(
        dependencies=RuntimeDependencies(
            embedding=_Embedding(),
            memory_events=_MemoryEvents(),
            context_builder=_Context(),
        ),
        profile=RuntimeProfile.DEMO,
    )

    result = await runtime.embed(
        EmbeddingRequest(
            text="hello",
            source_type=SourceType.USER,
            request_id="request-1",
            trace_id="trace-1",
        )
    )

    assert result.request_id == "request-1"
    assert result.trace_id == "trace-1"
