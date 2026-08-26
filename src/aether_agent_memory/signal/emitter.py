from __future__ import annotations

import asyncio
from time import time
from typing import Any

from aether_agent_memory.signal.models import MemorySignal


class MockSignalEmitter:
    """Process-local signal sink (used by demo/mock profiles)."""

    def __init__(self) -> None:
        self._signals: list[MemorySignal] = []

    async def emit(self, signal: MemorySignal) -> None:
        self._signals.append(signal)

    @property
    def signals(self) -> list[MemorySignal]:
        return list(self._signals)

    def clear(self) -> None:
        self._signals.clear()


class RedisSignalEmitter:
    """Persist memory signals in a Redis sorted set for cross-restart audit."""

    def __init__(
        self,
        redis_url: str,
        *,
        key_prefix: str = "p3:signal:",
        ttl_seconds: int = 7 * 24 * 60 * 60,
        client: Any | None = None,
    ) -> None:
        from redis import Redis

        if ttl_seconds <= 0:
            raise ValueError("signal TTL must be positive")
        self._redis = client or Redis.from_url(redis_url, decode_responses=True)
        self._prefix = key_prefix
        self._ttl_seconds = ttl_seconds

    async def emit(self, signal: MemorySignal) -> None:
        await asyncio.to_thread(self._emit_sync, signal)

    def _emit_sync(self, signal: MemorySignal) -> None:
        key = f"{self._prefix}{signal.memory_id}"
        self._redis.zadd(key, {signal.model_dump_json(): time()})
        self._redis.expire(key, self._ttl_seconds)

    async def close(self) -> None:
        await asyncio.to_thread(self._redis.close)
