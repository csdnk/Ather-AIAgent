"""Redis-backed logical tombstones for derived Context index entries."""

from __future__ import annotations

import asyncio
from hashlib import sha256
from typing import Any

from aether_agent_memory.context_store.uri import AetherUri


class RedisContextIndexTombstones:
    """Persist derived-index invalidation without changing the P2 contract.

    Tombstones intentionally have no TTL. They disappear only when an
    authoritative re-index succeeds and calls ``restore``.
    """

    def __init__(
        self,
        redis_url: str,
        *,
        namespace: str = "aether:p3:context-index-tombstone",
        operation_timeout_seconds: float = 1.0,
    ) -> None:
        if operation_timeout_seconds <= 0:
            raise ValueError("context index tombstone timeout must be positive")
        from redis.asyncio import Redis

        self._redis: Any = Redis.from_url(
            redis_url,
            decode_responses=True,
            socket_connect_timeout=operation_timeout_seconds,
            socket_timeout=operation_timeout_seconds,
        )
        self._namespace = namespace.rstrip(":")
        self._operation_timeout_seconds = operation_timeout_seconds

    async def invalidate(self, uri: AetherUri) -> None:
        async with asyncio.timeout(self._operation_timeout_seconds):
            await self._redis.set(self._key(uri), "1")

    async def restore(self, uri: AetherUri) -> None:
        async with asyncio.timeout(self._operation_timeout_seconds):
            await self._redis.delete(self._key(uri))

    async def is_invalidated(self, uri: AetherUri) -> bool:
        async with asyncio.timeout(self._operation_timeout_seconds):
            return bool(await self._redis.exists(self._key(uri)))

    async def close(self) -> None:
        await self._redis.aclose()

    def _key(self, uri: AetherUri) -> str:
        digest = sha256(str(uri).encode("utf-8")).hexdigest()
        return f"{self._namespace}:{digest}"


__all__ = ["RedisContextIndexTombstones"]
