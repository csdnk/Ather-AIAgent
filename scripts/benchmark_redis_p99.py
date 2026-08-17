"""Small Redis Working Memory latency benchmark.

Run from the repository root with the project environment active:
    python scripts/benchmark_redis_p99.py
"""

from __future__ import annotations

import asyncio
import os
import statistics
import time
from uuid import uuid4

from aether_agent_memory.context.models import ContextRequest
from aether_agent_memory.core.enums import MemoryType
from aether_agent_memory.core.memory import Memory
from aether_agent_memory.persistence import RedisMemoryStore
from aether_agent_memory.working.manager import MockWorkingMemoryManager


def percentile(values: list[float], percentage: float) -> float:
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int((percentage / 100) * len(ordered))))
    return ordered[index]


async def timed(operation) -> float:
    started = time.perf_counter_ns()
    await operation()
    return (time.perf_counter_ns() - started) / 1_000_000


async def main() -> None:
    redis_url = os.getenv("AETHER_B2_REDIS_URL", "redis://localhost:6379/0")
    count = int(os.getenv("AETHER_P99_SAMPLES", "500"))
    namespace = f"aether:b2:p99:{uuid4().hex}"
    store = RedisMemoryStore(redis_url, namespace=namespace)
    manager = MockWorkingMemoryManager(store=store)
    memories = [
        Memory(
            type=MemoryType.WORKING,
            session_id="p99-session",
            agent_id="p99-agent",
            tenant_id="p99-tenant",
            content=f"benchmark memory {index}",
            importance=1.0,
        )
        for index in range(count)
    ]
    try:
        write_ms = await asyncio.gather(*(timed(lambda m=m: manager.write(m)) for m in memories))
        read_ms = await asyncio.gather(*(timed(lambda m=m: manager.get(m.id)) for m in memories))
        request = ContextRequest(
            session_id="p99-session",
            agent_id="p99-agent",
            tenant_id="p99-tenant",
            query="benchmark memory",
            max_candidates=5,
        )
        recall_ms = await asyncio.gather(*(timed(lambda: manager.recall(request)) for _ in range(count)))
        print(f"redis_url={redis_url}")
        print(f"samples={count}")
        for name, values in (("write", write_ms), ("get", read_ms), ("recall", recall_ms)):
            print(
                f"{name}: p50={statistics.median(values):.3f}ms "
                f"p99={percentile(values, 99):.3f}ms max={max(values):.3f}ms"
            )
        print(f"recall_count={len(await manager.recall(request))}")
    finally:
        for memory in memories:
            await store.delete(memory.id)
        await store.close()


if __name__ == "__main__":
    asyncio.run(main())
