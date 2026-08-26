from __future__ import annotations

import asyncio
import json
from time import time

from aether_agent_memory.memory.retrieval.models import AccessTrace


class InMemoryAccessTraceAdapter:
    """Process-local access trace sink (used by demo/mock profiles)."""

    def __init__(self) -> None:
        self._items: list[AccessTrace] = []

    async def record(self, trace: AccessTrace) -> None:
        self._items.append(trace)

    @property
    def items(self) -> list[AccessTrace]:
        return list(self._items)


class RedisAccessTraceAdapter:
    """Persist access traces in a Redis sorted set for cross-request recall.

    The score is the event timestamp, so the most recent access per memory id
    can be recovered after a process restart.
    """

    def __init__(
        self,
        redis_url: str,
        *,
        key_prefix: str = "p3:access:",
        ttl_seconds: int = 7 * 24 * 60 * 60,
    ) -> None:
        from redis import Redis

        if ttl_seconds <= 0:
            raise ValueError("access trace TTL must be positive")
        self._redis = Redis.from_url(redis_url, decode_responses=True)
        self._prefix = key_prefix
        self._ttl_seconds = ttl_seconds

    async def record(self, trace: AccessTrace) -> None:
        await asyncio.to_thread(self._record_sync, trace)

    def _record_sync(self, trace: AccessTrace) -> None:
        key = f"{self._prefix}{trace.memory_id}"
        member = json.dumps(trace.model_dump(mode="json"), ensure_ascii=False)
        self._redis.zadd(key, {member: time()})
        self._redis.expire(key, self._ttl_seconds)
