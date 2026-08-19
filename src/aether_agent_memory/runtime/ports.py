from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol

from aether_agent_memory.runtime.request_context import RequestContext
from aether_agent_memory.runtime.status import ComponentHealth

if TYPE_CHECKING:
    from aether_agent_memory.b1 import EmbeddingRequest, EmbeddingResult
    from aether_agent_memory.b2 import MemoryEvent
    from aether_agent_memory.b3 import ScheduleRequest, ScheduleRunResult
    from aether_agent_memory.context import ContextPack, ContextRequest
    from aether_agent_memory.core.memory import Memory
    from aether_agent_memory.memory.retrieval.models import AccessTrace


class EmbeddingPort(Protocol):
    async def embed(
        self,
        request: EmbeddingRequest,
        context: RequestContext,
    ) -> EmbeddingResult: ...


class MemoryEventPort(Protocol):
    async def write_memory(
        self,
        event: MemoryEvent,
        context: RequestContext,
    ) -> Memory: ...


class ContextPort(Protocol):
    async def build_context(
        self,
        request: ContextRequest,
        context: RequestContext,
    ) -> ContextPack: ...


class TaskQueuePort(Protocol):
    async def submit_long_memory(
        self,
        *,
        text: str,
        tenant_id: str,
        user_id: str,
        agent_id: str,
        session_id: str,
        source_id: str,
        object_id: str | None,
        content_ref: str | None,
        context: RequestContext,
    ) -> dict[str, Any]: ...


class TaskStatusPort(Protocol):
    async def get_task(self, task_id: str, context: RequestContext) -> dict[str, Any] | None: ...


class ObjectStorePort(Protocol):
    async def put_text(
        self,
        *,
        text: str,
        object_key: str,
        context: RequestContext,
    ) -> dict[str, str]: ...


class VectorSearchPort(Protocol):
    async def search_memory(
        self,
        *,
        query: str,
        tenant_id: str,
        user_id: str,
        agent_id: str,
        limit: int,
        context: RequestContext,
        task_id: str | None = None,
    ) -> dict[str, Any]: ...


class SchedulerPort(Protocol):
    async def schedule(
        self,
        request: ScheduleRequest,
        context: RequestContext,
    ) -> ScheduleRunResult: ...


class AccessTracePort(Protocol):
    async def record(self, trace: AccessTrace) -> None: ...


class HealthCheckPort(Protocol):
    async def health(self) -> ComponentHealth: ...
