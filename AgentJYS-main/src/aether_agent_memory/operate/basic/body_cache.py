"""Read the actual prepared tier through Remember's verified cache boundary."""

import asyncio

from aether_agent_memory.remember.basic.content import RedisBodyCache
from aether_agent_memory.remember.contracts.models import MemoryRef
from aether_agent_memory.runtime.contracts.models import Scope
from aether_agent_memory.runtime.foundation.requests import text_hash

from ..contracts.models import Tier
from .executor import LocalCacheExecutor


class TieredBodyCache:
    def __init__(self, executor: LocalCacheExecutor, redis: RedisBodyCache | None = None) -> None:
        self.executor, self.redis = executor, redis

    async def get(self, scope: Scope, digest: str) -> str | None:
        def read() -> str | None:
            with self.executor.db() as db:
                rows = db.execute(
                    "SELECT memory,tier FROM copies WHERE content_hash=?", (digest,)
                ).fetchall()
                for memory, tier in rows:
                    ref = MemoryRef.model_validate_json(memory)
                    if ref.scope != scope:
                        continue
                    path = self.executor.path(ref, Tier(tier))
                    try:
                        content = path.read_text(encoding="utf-8")
                    except (OSError, UnicodeError):
                        continue
                    if text_hash(content) == digest:
                        db.execute(
                            "CREATE TABLE IF NOT EXISTS reads(tier TEXT PRIMARY KEY,count INTEGER)"
                        )
                        db.execute(
                            "INSERT INTO reads VALUES (?,1) "
                            "ON CONFLICT(tier) DO UPDATE SET count=count+1",
                            (tier,),
                        )
                        return content
            return None

        content = await asyncio.to_thread(read)
        if content is not None:
            return content
        return await self.redis.get(scope, digest) if self.redis else None

    async def put(self, scope: Scope, text: str) -> bool:
        return await self.redis.put(scope, text) if self.redis else False

    async def delete(self, scope: Scope, digest: str) -> None:
        if self.redis:
            await self.redis.delete(scope, digest)
