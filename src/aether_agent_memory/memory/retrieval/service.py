from __future__ import annotations

import asyncio
from time import perf_counter

from aether_agent_memory.context import ContextRequest
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
