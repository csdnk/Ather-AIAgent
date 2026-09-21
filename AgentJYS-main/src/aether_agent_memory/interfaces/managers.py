from datetime import datetime
from typing import Protocol, runtime_checkable

from aether_agent_memory.context.models import ContextRequest
from aether_agent_memory.core.enums import MemoryState
from aether_agent_memory.core.memory import Memory, RecalledMemory


@runtime_checkable
class MemoryManager(Protocol):
    async def write(self, memory: Memory) -> Memory: ...

    async def get(self, memory_id: str) -> Memory | None: ...

    async def query(
        self,
        *,
        session_id: str | None = None,
        agent_id: str | None = None,
        user_id: str | None = None,
        tenant_id: str | None = None,
        state: MemoryState | None = None,
        tags: list[str] | None = None,
        limit: int = 100,
    ) -> list[Memory]: ...

    async def update_state(self, memory_id: str, new_state: MemoryState) -> Memory: ...

    async def delete(self, memory_id: str) -> bool: ...

    async def expire_stale(self, now: datetime | None = None) -> int: ...

    async def recall(self, request: ContextRequest) -> list[RecalledMemory]: ...


@runtime_checkable
class WorkingMemoryManager(MemoryManager, Protocol):
    pass


@runtime_checkable
class EpisodicMemoryManager(MemoryManager, Protocol):
    pass


@runtime_checkable
class SemanticMemoryManager(MemoryManager, Protocol):
    pass
