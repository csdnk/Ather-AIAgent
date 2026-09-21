from __future__ import annotations

from time import perf_counter
from typing import Any

from aether_agent_memory.b1 import EmbeddingRequest, EmbeddingResult
from aether_agent_memory.runtime.request_context import RequestContext
from aether_agent_memory.runtime.status import ComponentHealth, ComponentStatus, RuntimeComponent


class LegacyEmbeddingAdapter:
    """EmbeddingPort adapter over the existing B1 EmbeddingPipeline."""

    def __init__(self, legacy_runtime: Any) -> None:
        self._legacy_runtime = legacy_runtime

    async def embed(
        self,
        request: EmbeddingRequest,
        context: RequestContext,
    ) -> EmbeddingResult:
        scoped_request = request.model_copy(
            update={
                "request_id": context.request_id,
                "trace_id": context.trace_id,
                "tenant_id": request.tenant_id or context.tenant_id,
                "metadata": {
                    **request.metadata,
                    "agent_id": context.agent_id,
                    "session_id": context.session_id,
                    "task_id": context.task_id,
                    "idempotency_key": context.idempotency_key,
                },
            }
        )
        result = await self._legacy_runtime.embed(scoped_request)
        if isinstance(result, EmbeddingResult):
            return result
        return EmbeddingResult.model_validate(result)

    async def health(self) -> ComponentHealth:
        started = perf_counter()
        try:
            ensure_ready = getattr(self._legacy_runtime.embedder, "ensure_ready", None)
            if ensure_ready is not None:
                await ensure_ready()
            status = ComponentStatus.HEALTHY
            detail = getattr(self._legacy_runtime, "embedding_path", "B1 adapter ready")
        except Exception as exc:
            status = ComponentStatus.UNAVAILABLE
            detail = f"{type(exc).__name__}: {exc}"
        return ComponentHealth(
            component=RuntimeComponent.B1,
            status=status,
            detail=detail,
            latency_ms=round((perf_counter() - started) * 1000, 3),
            critical=True,
        )
