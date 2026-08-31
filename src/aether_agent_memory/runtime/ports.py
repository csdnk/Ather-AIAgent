"""Canonical ports for P3 application services.

This module is the single Runtime/Application ports layer.  Older protocols in
``aether_agent_memory.interfaces`` remain for low-level domain manager
compatibility only; new application-facing adapters should depend here.
"""

from __future__ import annotations

import builtins
from typing import TYPE_CHECKING, Any, Protocol

from aether_agent_memory.runtime.dtos import (
    LongMemorySubmission,
    MemorySearchResult,
    ObjectReference,
    TaskStatusRecord,
)
from aether_agent_memory.runtime.request_context import RequestContext
from aether_agent_memory.runtime.status import ComponentHealth

if TYPE_CHECKING:
    from aether_agent_memory.b1 import EmbeddingRequest, EmbeddingResult
    from aether_agent_memory.b2 import MemoryEvent
    from aether_agent_memory.b3 import ActionLogEntry, ScheduleRequest, ScheduleRunResult
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


class ContextPackBuilder(Protocol):
    async def build(self, request: ContextRequest) -> ContextPack: ...


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
    ) -> LongMemorySubmission: ...


class TaskStatusPort(Protocol):
    async def get_task(
        self,
        task_id: str,
        context: RequestContext,
    ) -> TaskStatusRecord | None: ...


class ObjectStorePort(Protocol):
    async def put_text(
        self,
        *,
        text: str,
        object_key: str,
        context: RequestContext,
    ) -> ObjectReference: ...

    async def read_text(
        self,
        *,
        content_ref: str,
        context: RequestContext,
    ) -> str | None: ...

    async def read_bytes(
        self,
        *,
        content_ref: str,
        context: RequestContext,
    ) -> bytes | None: ...


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
    ) -> MemorySearchResult: ...


class VectorIndexPort(Protocol):
    """Write a Memory's derived vector representation to an index provider."""

    async def upsert_memory(
        self,
        memory: Memory,
        context: RequestContext,
    ) -> None: ...


class MemoryStorePort(Protocol):
    """Canonical Memory fact store boundary, including versioned writes."""

    async def upsert(self, memory: Memory) -> None: ...

    async def upsert_if_revision(
        self,
        memory: Memory,
        *,
        expected_revision: int,
    ) -> bool: ...

    async def get(self, memory_id: str) -> Memory | None: ...

    async def list(self) -> builtins.list[Memory]: ...

    async def list_scoped(
        self,
        *,
        tenant_id: str | None = None,
        user_id: str | None = None,
        agent_id: str | None = None,
        session_id: str | None = None,
    ) -> builtins.list[Memory]: ...

    async def delete(self, memory_id: str) -> bool: ...


class SchedulerPort(Protocol):
    async def schedule(
        self,
        request: ScheduleRequest,
        context: RequestContext,
    ) -> ScheduleRunResult: ...


class AccessTracePort(Protocol):
    async def record(self, trace: AccessTrace) -> None: ...

    async def record_many(self, traces: builtins.list[AccessTrace]) -> None: ...


class IdempotencyPort(Protocol):
    def claim(
        self,
        *,
        key: str,
        tenant_id: str | None,
        op_type: str,
        payload_hash: str,
    ) -> tuple[str, dict[str, Any] | None]: ...

    def complete(
        self,
        *,
        key: str,
        tenant_id: str | None,
        op_type: str,
        response: dict[str, Any],
    ) -> None: ...

    def fail(self, *, key: str, tenant_id: str | None, op_type: str) -> None: ...


class ActionLogPort(Protocol):
    def append_entries(self, entries: list[ActionLogEntry]) -> None: ...


class HealthCheckPort(Protocol):
    async def health(self) -> ComponentHealth: ...
