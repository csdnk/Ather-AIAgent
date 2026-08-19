from __future__ import annotations

from typing import Any

from aether_agent_memory.b2 import MemoryEvent
from aether_agent_memory.context import ContextPack, ContextRequest
from aether_agent_memory.core.memory import Memory
from aether_agent_memory.runtime.request_context import RequestContext


class LegacyMemoryEventAdapter:
    def __init__(self, legacy_runtime: Any) -> None:
        self._legacy_runtime = legacy_runtime

    async def write_memory(self, event: MemoryEvent, context: RequestContext) -> Memory:
        result = await self._legacy_runtime.ingest_memory(
            event.model_copy(
                update={
                    "request_id": event.request_id or context.request_id,
                    "trace_id": event.trace_id or context.trace_id,
                    "tenant_id": event.tenant_id or context.tenant_id,
                    "user_id": event.user_id or context.user_id,
                    "agent_id": event.agent_id or context.agent_id,
                    "session_id": event.session_id or context.session_id,
                    "task_id": event.task_id or context.task_id,
                }
            )
        )
        if isinstance(result, Memory):
            return result
        return Memory.model_validate(result)


class LegacyContextAdapter:
    def __init__(self, legacy_runtime: Any) -> None:
        self._legacy_runtime = legacy_runtime

    async def build_context(
        self,
        request: ContextRequest,
        context: RequestContext,
    ) -> ContextPack:
        scoped_request = request.model_copy(
            update={
                "request_id": request.request_id or context.request_id,
                "trace_id": request.trace_id or context.trace_id,
                "tenant_id": request.tenant_id or context.tenant_id,
                "user_id": request.user_id or context.user_id,
                "agent_id": request.agent_id or context.agent_id,
                "session_id": request.session_id or context.session_id,
                "task_id": request.task_id or context.task_id,
            }
        )
        result = await self._legacy_runtime.build_context(scoped_request)
        if isinstance(result, ContextPack):
            return result
        return ContextPack.model_validate(result)
