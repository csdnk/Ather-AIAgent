from __future__ import annotations

from typing import Protocol

from aether_agent_memory.core.memory import Memory
from aether_agent_memory.runtime.request_context import Scope


class MemoryRepository(Protocol):
    async def upsert(self, memory: Memory) -> None: ...

    async def get(self, memory_id: str) -> Memory | None: ...

    async def list_scope(self, scope: Scope) -> list[Memory]: ...
