from __future__ import annotations

from typing import Protocol

from aether_agent_memory.context import ContextPack, ContextRequest
from aether_agent_memory.memory.retrieval.service import ContextRetrievalService
from aether_agent_memory.runtime.ports import ContextPackBuilder
from aether_agent_memory.runtime.request_context import RequestContext


class ContextCompressor(Protocol):
    async def compress(self, pack: ContextPack, context: RequestContext) -> ContextPack: ...


class NoOpContextCompressor:
    async def compress(self, pack: ContextPack, context: RequestContext) -> ContextPack:
        return pack


class RetrievalContextBuilder:
    """Context hook that makes the retrieval layer explicit.

    The compatibility builder is injected through the canonical Port. This
    keeps the legacy construction path replaceable while RetrievalService owns
    source fusion and degradation metadata.
    """

    def __init__(
        self,
        *,
        retrieval: ContextRetrievalService,
        legacy_builder: ContextPackBuilder,
        compressor: ContextCompressor | None = None,
    ) -> None:
        self._retrieval = retrieval
        self._legacy_builder = legacy_builder
        self._compressor = compressor or NoOpContextCompressor()

    async def build(self, request: ContextRequest, context: RequestContext) -> ContextPack:
        retrieval_result = await self._retrieval.recall(request, context)
        pack = await self._legacy_builder.build(request)
        if not retrieval_result.complete:
            pack.complete = False
            pack.status = "degraded"
            pack.missing_sources = list(
                dict.fromkeys([*pack.missing_sources, *retrieval_result.missing_sources])
            )
            pack.degradation_reasons = {
                **pack.degradation_reasons,
                **retrieval_result.degraded_reasons,
            }
            pack.source_latency_ms = {
                **pack.source_latency_ms,
                **retrieval_result.source_latency_ms,
            }
        return await self._compressor.compress(pack, context)
