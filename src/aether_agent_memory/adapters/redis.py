from __future__ import annotations

import asyncio
from time import perf_counter
from typing import Any

from aether_agent_memory.runtime.status import ComponentHealth, ComponentStatus, RuntimeComponent


class RedisHealthAdapter:
    def __init__(
        self,
        redis_url: str,
        *,
        timeout_seconds: float = 0.5,
        critical: bool = True,
    ) -> None:
        self._redis_url = redis_url
        self._timeout_seconds = timeout_seconds
        self._critical = critical

    async def health(self) -> ComponentHealth:
        started = perf_counter()
        client: Any | None = None
        try:
            from redis.asyncio import Redis

            client = Redis.from_url(
                self._redis_url,
                decode_responses=True,
                socket_connect_timeout=self._timeout_seconds,
                socket_timeout=self._timeout_seconds,
            )
            async with asyncio.timeout(self._timeout_seconds):
                await client.ping()
            status = ComponentStatus.HEALTHY
            detail = "Redis ping succeeded"
        except Exception as exc:
            status = ComponentStatus.UNAVAILABLE
            detail = f"{type(exc).__name__}: {exc}"
        finally:
            if client is not None:
                await client.aclose()
        return ComponentHealth(
            component=RuntimeComponent.REDIS,
            status=status,
            detail=detail,
            latency_ms=round((perf_counter() - started) * 1000, 3),
            critical=self._critical,
        )
