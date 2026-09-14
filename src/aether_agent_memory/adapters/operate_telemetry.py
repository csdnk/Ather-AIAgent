from __future__ import annotations

from typing import Any

from aether_agent_memory.core.memory import Memory
from aether_agent_memory.memory.retrieval.models import AccessTrace
from aether_agent_memory.signal.models import MemorySignal


class MemoryControlTelemetry:
    def __init__(self, access_sink: Any, signal_sink: Any) -> None:
        self.access_sink = access_sink
        self.signal_sink = signal_sink

    async def read(self, memory: Memory) -> tuple[list[MemorySignal], list[AccessTrace]]:
        signals = [s for s in getattr(self.signal_sink, "signals", []) if s.memory_id == memory.id]
        traces = [t for t in getattr(self.access_sink, "items", []) if t.memory_id == memory.id]
        return signals[-100:], traces[-100:]


class RedisControlTelemetry:
    def __init__(self, redis_url: str) -> None:
        from redis.asyncio import Redis

        self.client: Any = Redis.from_url(
            redis_url, decode_responses=True, socket_timeout=1, socket_connect_timeout=1
        )

    async def read(self, memory: Memory) -> tuple[list[MemorySignal], list[AccessTrace]]:
        signals = await self.client.zrevrange(f"p3:signal:{memory.id}", 0, 99)
        traces = await self.client.zrevrange(f"p3:access:{memory.id}", 0, 99)
        return (
            [MemorySignal.model_validate_json(s) for s in signals],
            [AccessTrace.model_validate_json(t) for t in traces],
        )

    async def close(self) -> None:
        await self.client.aclose()
