from typing import Protocol, runtime_checkable

from aether_agent_memory.core.memory import P2Ref


@runtime_checkable
class StorageClient(Protocol):
    async def put(self, key: str, data: bytes) -> P2Ref: ...

    async def get(self, key: str) -> bytes | None: ...

    async def delete(self, key: str) -> bool: ...
