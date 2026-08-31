from __future__ import annotations

from typing import Protocol

from aether_agent_memory.core.scope import Scope
from aether_agent_memory.session.models import (
    SessionExtractionWorkItem,
    SessionRecord,
)


class SessionStorePort(Protocol):
    """Revision-aware session fact store used by the P3 application layer."""

    async def get(self, scope: Scope) -> SessionRecord | None: ...

    async def save(self, record: SessionRecord, *, expected_revision: int) -> bool: ...

    async def close(self) -> None: ...


class SessionExtractionQueuePort(Protocol):
    """Durable boundary between archive commit and Memory extraction."""

    async def enqueue(self, item: SessionExtractionWorkItem) -> SessionExtractionWorkItem: ...

    async def claim(self, work_id: str) -> SessionExtractionWorkItem | None: ...

    async def complete(self, work_id: str) -> SessionExtractionWorkItem | None: ...

    async def fail(self, work_id: str, error: str) -> SessionExtractionWorkItem | None: ...

    async def retry(self, work_id: str) -> SessionExtractionWorkItem | None: ...

    async def pending(self) -> list[SessionExtractionWorkItem]: ...

    async def close(self) -> None: ...
