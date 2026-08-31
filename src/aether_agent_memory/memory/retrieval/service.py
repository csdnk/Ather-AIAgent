from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from time import perf_counter
from uuid import uuid4

from aether_agent_memory.context import ContextPack, ContextRequest
from aether_agent_memory.context_store.mapping import resource_uri
from aether_agent_memory.context_store.models import (
    ContextCandidate,
    ContextItemKind,
    ContextLayer,
    RetrievalTrace,
    RetrievalTraceAction,
    RetrievalTraceStep,
)
from aether_agent_memory.context_store.ports import RetrievalTraceStorePort
from aether_agent_memory.core.enums import MemoryType, SourceType
from aether_agent_memory.core.memory import Memory
from aether_agent_memory.memory.retrieval.models import (
    MemoryRetrievalResult,
    RecallCandidate,
    RecallSourceResult,
)
from aether_agent_memory.memory.retrieval.pipeline import RecallPipeline
from aether_agent_memory.memory.retrieval.sources import RecallSource
from aether_agent_memory.runtime.request_context import RequestContext


class ContextRetrievalService:
    def __init__(
        self,
        sources: list[RecallSource],
        *,
        trace_store: RetrievalTraceStorePort | None = None,
        pipeline: RecallPipeline | None = None,
    ) -> None:
        self._sources = list(sources)
        self._trace_store = trace_store
        self._pipeline = pipeline or RecallPipeline()

    async def close(self) -> None:
        if self._trace_store is not None:
            await self._trace_store.close()

    async def recall(
        self,
        request: ContextRequest,
        context: RequestContext,
        *,
        persist_trace: bool = True,
    ) -> MemoryRetrievalResult:
        async def recall_one(
            source: RecallSource,
        ) -> tuple[str, RecallSourceResult, float, str | None]:
            started = perf_counter()
            try:
                recalled = await asyncio.wait_for(
                    source.recall(request, context),
                    timeout=request.deadline_ms / 1000,
                )
                result = (
                    recalled
                    if isinstance(recalled, RecallSourceResult)
                    else RecallSourceResult(candidates=recalled)
                )
                return source.name, result, (perf_counter() - started) * 1000, None
            except TimeoutError:
                return (
                    source.name,
                    RecallSourceResult(),
                    (perf_counter() - started) * 1000,
                    "recall deadline exceeded",
                )
            except Exception as exc:
                return (
                    source.name,
                    RecallSourceResult(),
                    (perf_counter() - started) * 1000,
                    f"{type(exc).__name__}: {exc}",
                )

        trace = RetrievalTrace(
            trace_id=context.trace_id,
            query=request.query,
            scope=context.scope,
        )
        results = await asyncio.gather(*(recall_one(source) for source in self._sources))
        candidates: list[RecallCandidate] = []
        missing_sources: list[str] = []
        degraded_reasons: dict[str, str] = {}
        source_latency_ms: dict[str, float] = {}
        for name, source_result, latency_ms, error in results:
            source_latency_ms[name] = round(latency_ms, 3)
            if error is not None:
                missing_sources.append(name)
                degraded_reasons[name] = error
                trace.record(
                    RetrievalTraceStep(
                        action=RetrievalTraceAction.DEGRADED,
                        source=name,
                        latency_ms=latency_ms,
                        candidate_count=0,
                        reason=error,
                    )
                )
            else:
                trace.record(
                    RetrievalTraceStep(
                        action=RetrievalTraceAction.SOURCE_RECALL,
                        source=name,
                        latency_ms=latency_ms,
                        candidate_count=len(source_result.candidates),
                    )
                )
                if not source_result.complete:
                    source_missing = source_result.missing_sources or [name]
                    for missing in source_missing:
                        if missing not in missing_sources:
                            missing_sources.append(missing)
                        reason = source_result.degraded_reasons.get(
                            missing, "source returned a partial result"
                        )
                        degraded_reasons[missing] = reason
                        trace.record(
                            RetrievalTraceStep(
                                action=RetrievalTraceAction.DEGRADED,
                                source=missing,
                                candidate_count=len(source_result.candidates),
                                reason=reason,
                            )
                        )
            candidates.extend(source_result.candidates)
        candidates = self._pipeline.process(candidates, request, context)
        context_candidates = [
            _to_context_candidate(candidate, context)
            for candidate in candidates[: request.max_candidates]
        ]
        for candidate in context_candidates:
            trace.record(
                RetrievalTraceStep(
                    action=RetrievalTraceAction.CANDIDATE_SCORED,
                    source=candidate.source,
                    uri=candidate.uri,
                    layer=candidate.layer,
                    score=candidate.score,
                )
            )
        trace.finish(complete=not missing_sources, missing_sources=missing_sources)
        if persist_trace:
            await self._persist_trace(trace)
        return MemoryRetrievalResult(
            candidates=candidates,
            context_candidates=context_candidates,
            complete=not missing_sources,
            missing_sources=missing_sources,
            degraded_reasons=degraded_reasons,
            source_latency_ms=source_latency_ms,
            trace_id=context.trace_id,
            retrieval_trace=trace,
        )

    async def augment_context(
        self,
        pack: ContextPack,
        request: ContextRequest,
        context: RequestContext,
    ) -> ContextPack:
        result = await self.recall(request, context, persist_trace=False)
        _merge_retrieval_status(pack, result)
        for candidate in result.candidates[: request.max_candidates]:
            memory = _memory_from_candidate(candidate, context)
            if any(existing.id == memory.id for existing in pack.memories):
                continue
            tokens = _estimate_tokens(memory.content)
            if pack.total_tokens + tokens > pack.budget_tokens:
                if result.retrieval_trace is not None:
                    result.retrieval_trace.record(
                        RetrievalTraceStep(
                            action=RetrievalTraceAction.BUDGET_SKIPPED,
                            source=candidate.source,
                            uri=candidate.context_uri,
                            layer=ContextLayer.DETAIL,
                            score=candidate.score,
                            reason="context token budget exceeded",
                        )
                    )
                continue
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
            if result.retrieval_trace is not None:
                result.retrieval_trace.record(
                    RetrievalTraceStep(
                        action=RetrievalTraceAction.CONTEXT_SELECTED,
                        source=candidate.source,
                        uri=candidate.context_uri,
                        layer=ContextLayer.DETAIL,
                        score=candidate.score,
                    )
                )
        _trim_to_budget(pack)
        pack.summary = "\n".join(memory.content for memory in pack.memories[:3])[:1000]
        pack.budget_info = {
            "used_tokens": pack.total_tokens,
            "budget_tokens": pack.budget_tokens,
            "remaining_tokens": max(pack.budget_tokens - pack.total_tokens, 0),
        }
        if result.retrieval_trace is not None:
            result.retrieval_trace.finish(
                complete=result.complete,
                missing_sources=result.missing_sources,
            )
            await self._persist_trace(result.retrieval_trace)
        return pack

    async def _persist_trace(self, trace: RetrievalTrace) -> None:
        if self._trace_store is None:
            return
        try:
            await self._trace_store.put(trace)
        except Exception:
            # Trace persistence must not fail the foreground context request.
            return


class MemoryRetrievalService(ContextRetrievalService):
    """Deprecated compatibility name; use ContextRetrievalService for new wiring."""


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


def _to_context_candidate(
    candidate: RecallCandidate,
    context: RequestContext,
) -> ContextCandidate:
    uri = candidate.context_uri or resource_uri(
        context.scope,
        candidate.memory_id,
        category=candidate.source,
    )
    candidate.context_uri = uri
    kind = candidate.context_kind or (
        ContextItemKind.MEMORY
        if candidate.memory is not None
        else ContextItemKind.RESOURCE
    )
    return ContextCandidate(
        item_id=candidate.memory_id,
        uri=uri,
        kind=kind,
        layer=candidate.context_layer,
        content=candidate.content,
        content_ref=candidate.content_ref,
        source=candidate.source,
        score=candidate.score,
        semantic_score=candidate.semantic_score,
        temporal_score=candidate.temporal_score,
        created_at=candidate.created_at,
        last_access=candidate.last_access,
        metadata=dict(candidate.trace_metadata),
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
