from __future__ import annotations

import asyncio
from hashlib import sha256
from typing import Any

from aether_agent_memory.context_store.models import RetrievalTrace


class RedisRetrievalTraceStore:
    """TTL-bound Redis persistence for explainable context retrieval traces."""

    def __init__(
        self,
        redis_url: str,
        *,
        namespace: str = "aether:p3:retrieval-trace",
        ttl_seconds: int = 3600,
        operation_timeout_seconds: float = 0.25,
    ) -> None:
        if ttl_seconds <= 0:
            raise ValueError("retrieval trace TTL must be positive")
        if operation_timeout_seconds <= 0:
            raise ValueError("retrieval trace operation timeout must be positive")
        from redis.asyncio import Redis

        self._redis: Any = Redis.from_url(
            redis_url,
            decode_responses=True,
            socket_connect_timeout=operation_timeout_seconds,
            socket_timeout=operation_timeout_seconds,
        )
        self._namespace = namespace.rstrip(":")
        self._ttl_seconds = ttl_seconds
        self._operation_timeout_seconds = operation_timeout_seconds

    def _key(self, trace_id: str) -> str:
        digest = sha256(trace_id.encode("utf-8")).hexdigest()
        return f"{self._namespace}:{digest}"

    async def put(self, trace: RetrievalTrace) -> None:
        async with asyncio.timeout(self._operation_timeout_seconds):
            await self._redis.set(
                self._key(trace.trace_id),
                trace.model_dump_json(),
                ex=self._ttl_seconds,
            )

    async def get(self, trace_id: str) -> RetrievalTrace | None:
        async with asyncio.timeout(self._operation_timeout_seconds):
            payload = await self._redis.get(self._key(trace_id))
        if payload is None:
            return None
        return RetrievalTrace.model_validate_json(payload)

    async def close(self) -> None:
        await self._redis.aclose()
