"""Read the actual prepared tier through Remember's verified cache boundary."""

import asyncio

from aether_agent_memory.remember.contracts.models import MemoryRef, MemorySnapshot
from aether_agent_memory.runtime.contracts.models import Scope, TrustedContext
from aether_agent_memory.runtime.storage.cache import BodyCache
from aether_agent_memory.runtime.storage.ports import MetadataTransaction

from .cache_port import CacheCapacityError, CacheExecutor


class TieredBodyCache:
    def __init__(self, executor: CacheExecutor, redis: BodyCache | None = None) -> None:
        self.executor, self.redis = executor, redis

    @property
    def namespace(self) -> str | None:
        value = getattr(self.redis, "namespace", None)
        return value if isinstance(value, str) else None

    def keys(self, scope: Scope, digest: str) -> tuple[str, str, str]:
        address = getattr(self.redis, "keys", None)
        if not callable(address):
            raise ValueError("cache backend does not expose a Redis address")
        bucket, body, expiry = address(scope, digest)
        return str(bucket), str(body), str(expiry)

    async def admit_initial(self, memory: MemorySnapshot, ctx: TrustedContext) -> bool:
        """Explicit Remember write; ordinary read-through keeps its policy guard."""
        admission = getattr(self.executor, "admit_initial", None)
        if not callable(admission):
            return False
        try:
            return bool(await asyncio.to_thread(admission, memory, ctx))
        except CacheCapacityError:
            return False

    def registration_current(self, tx: MetadataTransaction, memory: MemoryRef, digest: str) -> bool:
        registered = getattr(self.executor, "registration_current", None)
        return bool(registered(tx, memory, digest)) if callable(registered) else False

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
