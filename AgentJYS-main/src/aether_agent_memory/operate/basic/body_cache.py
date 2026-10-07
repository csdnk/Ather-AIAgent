"""Read the actual prepared tier through Remember's verified cache boundary."""

import asyncio

from aether_agent_memory.remember.contracts.models import MemoryRef
from aether_agent_memory.runtime.contracts.models import Scope
from aether_agent_memory.runtime.storage.cache import BodyCache

from .cache_port import CacheExecutor


class TieredBodyCache:
    def __init__(self, executor: CacheExecutor, redis: BodyCache | None = None) -> None:
        self.executor, self.redis = executor, redis

    async def get(self, scope: Scope, digest: str) -> str | None:
        content = await asyncio.to_thread(self.executor.read_cached, scope, digest)
        if content is not None:
            return content
        if getattr(self.executor, "policy_managed", False):
            return None
        return await self.redis.get(scope, digest) if self.redis else None

    async def put(self, scope: Scope, text: str) -> bool:
        # Read-through must not bypass heat admission or undo a completed cooling.
        if getattr(self.executor, "policy_managed", False):
            return False
        return await self.redis.put(scope, text) if self.redis else False

    async def delete(self, scope: Scope, digest: str) -> None:
        if self.redis:
            await self.redis.delete(scope, digest)

    async def cleanup_complete(self, memory: MemoryRef, digest: str) -> bool:
        # Another live Memory can legitimately cache identical content in this
        # scope. Operate owns purging the admitted Memory and its old versions.
        if not await asyncio.to_thread(self.executor.cleanup_complete, memory, permanent=False):
            return False
        return await self.redis.cleanup_complete(memory, digest) if self.redis else True
