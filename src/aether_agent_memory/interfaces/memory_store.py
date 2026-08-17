import builtins
from typing import Protocol, runtime_checkable

from aether_agent_memory.core.memory import Memory


@runtime_checkable
class MemoryStore(Protocol):
    async def upsert(self, memory: Memory) -> None: ...

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
