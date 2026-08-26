from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from time import perf_counter
from uuid import uuid4

from aether_agent_memory.context import ContextPack, ContextRequest
from aether_agent_memory.core.enums import MemoryType, SourceType
from aether_agent_memory.core.memory import Memory
from aether_agent_memory.memory.retrieval.models import MemoryRetrievalResult, RecallCandidate
from aether_agent_memory.memory.retrieval.sources import RecallSource
from aether_agent_memory.runtime.request_context import RequestContext


class MemoryRetrievalService:
    def __init__(self, sources: list[RecallSource]) -> None:
        self._sources = list(sources)

    async def recall(
        self,
        request: ContextRequest,
        context: RequestContext,
    ) -> MemoryRetrievalResult:
        async def recall_one(
            source: RecallSource,
        ) -> tuple[str, list[RecallCandidate], float, str | None]:
            started = perf_counter()
            try:
                candidates = await asyncio.wait_for(
                    source.recall(request, context),
                    timeout=request.deadline_ms / 1000,
                )
                return source.name, candidates, (perf_counter() - started) * 1000, None
            except TimeoutError:
                return (
                    source.name,
                    [],
                    (perf_counter() - started) * 1000,
                    "recall deadline exceeded",
                )
            except Exception as exc:
                return (
                    source.name,
                    [],
                    (perf_counter() - started) * 1000,
                    f"{type(exc).__name__}: {exc}",
                )

        results = await asyncio.gather(*(recall_one(source) for source in self._sources))
        candidates: list[RecallCandidate] = []
        missing_sources: list[str] = []
        degraded_reasons: dict[str, str] = {}
        source_latency_ms: dict[str, float] = {}
        for name, recalled, latency_ms, error in results:
            source_latency_ms[name] = round(latency_ms, 3)
            if error is not None:
                missing_sources.append(name)
                degraded_reasons[name] = error
            candidates.extend(recalled)
        candidates.sort(key=lambda item: item.score, reverse=True)
        return MemoryRetrievalResult(
            candidates=candidates,
            complete=not missing_sources,
            missing_sources=missing_sources,
            degraded_reasons=degraded_reasons,
            source_latency_ms=source_latency_ms,
            trace_id=context.trace_id,
        )

    async def augment_context(
        self,
        pack: ContextPack,
        request: ContextRequest,
        context: RequestContext,
    ) -> ContextPack:
        result = await self.recall(request, context)
        _merge_retrieval_status(pack, result)
        for candidate in result.candidates[: request.max_candidates]:
            memory = _memory_from_candidate(candidate, context)
            if any(existing.id == memory.id for existing in pack.memories):
                continue
            tokens = _estimate_tokens(memory.content)
            pack.memories.append(memory)
            pack.recall_scores[memory.id] = candidate.score
            pack.memory_refs.append(memory.id)
            if candidate.content_ref and candidate.content_ref not in pack.evidence_refs:
                pack.evidence_refs.append(candidate.content_ref)
            pack.total_tokens += tokens
            prefix = "\n" if pack.assembled_text else ""
            pack.assembled_text += (
                f"{prefix}[{len(pack.memory_refs)}] ({candidate.source}) {memory.content}"
            )
        _trim_to_budget(pack)
        return pack


def _merge_retrieval_status(
    pack: ContextPack,
    result: MemoryRetrievalResult,
) -> None:
    if result.complete:
        pack.source_latency_ms = {
            **pack.source_latency_ms,
            **result.source_latency_ms,
        }
        return
    pack.complete = False
    pack.status = "degraded"
    pack.missing_sources = list(
        dict.fromkeys([*pack.missing_sources, *result.missing_sources])
    )
    pack.degradation_reasons = {
        **pack.degradation_reasons,
        **result.degraded_reasons,
    }
    pack.source_latency_ms = {
        **pack.source_latency_ms,
        **result.source_latency_ms,
    }


def _memory_from_candidate(
    candidate: RecallCandidate,
    context: RequestContext,
) -> Memory:
    if candidate.memory is not None:
        return candidate.memory
    return Memory(
        id=candidate.memory_id or uuid4().hex,
        type=candidate.memory_type or MemoryType.SEMANTIC,
        session_id=context.session_id or "",
        agent_id=context.agent_id or "",
        user_id=context.user_id,
        tenant_id=context.tenant_id,
        request_id=context.request_id,
        trace_id=context.trace_id,
        source_id=candidate.content_ref,
        content=candidate.content or candidate.content_ref or "",
        source=SourceType.DOCUMENT if candidate.content_ref else SourceType.SYSTEM,
        importance=1.0,
        embedding_status="succeeded",
        vector_projection_status="succeeded",
        metadata={
            "source": candidate.source,
            "content_ref": candidate.content_ref,
            "embedding_status": "succeeded",
            "vector_projection_status": "succeeded",
            **candidate.trace_metadata,
        },
        created_at=candidate.created_at or datetime.now(UTC),
    )


def _trim_to_budget(pack: ContextPack) -> None:
    if pack.total_tokens <= pack.budget_tokens:
        return
    ranked = sorted(
        pack.memories,
        key=lambda memory: pack.recall_scores.get(memory.id, 0.0),
        reverse=True,
    )
    selected: list[Memory] = []
    total = 0
    for memory in ranked:
        tokens = _estimate_tokens(memory.content)
        if total + tokens > pack.budget_tokens:
            continue
        selected.append(memory)
        total += tokens
    pack.memories = selected
    pack.total_tokens = total
    pack.recall_scores = {
        memory.id: pack.recall_scores.get(memory.id, 0.0) for memory in selected
    }
    pack.memory_refs = [memory.id for memory in selected]
    pack.assembled_text = "\n".join(
        f"[{index + 1}] ({memory.type.value}) {memory.content}"
        for index, memory in enumerate(selected)
    )


def _estimate_tokens(text: str) -> int:
    return max(len(text) // 4, 1)
