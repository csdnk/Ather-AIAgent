"""B2 Working Memory latency benchmark matching the handoff baseline."""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import time
from pathlib import Path
from uuid import uuid4

from aether_agent_memory.core.enums import MemoryType
from aether_agent_memory.core.memory import Memory
from aether_agent_memory.persistence import RedisMemoryStore
from aether_agent_memory.working.manager import MockWorkingMemoryManager


TEXT_1KI = "x" * 1024


def percentile(values: list[float], percentage: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(len(ordered) * percentage / 100))]


def make_memory(content: str = TEXT_1KI) -> Memory:
    return Memory(
        type=MemoryType.WORKING,
        session_id="b2-latency-session",
        agent_id="b2-latency-agent",
        user_id="b2-latency-user",
        tenant_id="b2-latency-tenant",
        content=content,
    )


async def run_scenario(
    manager: MockWorkingMemoryManager,
    preloaded: list[Memory],
    scenario: str,
    concurrency: int,
    samples: int,
) -> dict[str, float | int | str]:
    latencies: list[float] = []
    cursor = 0
    lock = asyncio.Lock()

    async def next_index() -> int | None:
        nonlocal cursor
        async with lock:
            if cursor >= samples:
                return None
            index = cursor
            cursor += 1
            return index

    async def worker() -> None:
        while True:
            index = await next_index()
            if index is None:
                return
            is_write = scenario == "write" or (scenario == "mixed" and index % 2 == 0)
            started = time.perf_counter_ns()
            if is_write:
                await manager.write(make_memory())
            else:
                await manager.get(preloaded[index % len(preloaded)].id)
            latencies.append((time.perf_counter_ns() - started) / 1_000_000)

    started = time.perf_counter()
    await asyncio.gather(*(worker() for _ in range(concurrency)))
    elapsed = time.perf_counter() - started
    return {
        "scenario": scenario,
        "concurrency": concurrency,
        "samples": samples,
        "throughput_ops_s": round(samples / elapsed, 3),
        "p50_ms": round(statistics.median(latencies), 3),
        "p95_ms": round(percentile(latencies, 95), 3),
        "p99_ms": round(percentile(latencies, 99), 3),
        "max_ms": round(max(latencies), 3),
    }


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--redis-url", default="redis://localhost:6379/0")
    parser.add_argument("--samples", type=int, default=10_000)
    parser.add_argument("--preload", type=int, default=256)
    parser.add_argument("--output", type=Path, default=Path("artifacts/p3_b2_working_latency.json"))
    args = parser.parse_args()
    results: list[dict[str, float | int | str]] = []
    for concurrency in (1, 8, 32):
        namespace = f"aether:b2:latency:{uuid4().hex}"
        store = RedisMemoryStore(
            args.redis_url,
            namespace=namespace,
            adaptive_initial=concurrency,
            adaptive_maximum=concurrency,
            adaptive_target_ms=1_000_000.0,
        )
        manager = MockWorkingMemoryManager(store=store)
        preloaded = [make_memory() for _ in range(args.preload)]
        try:
            for memory in preloaded:
                await manager.write(memory)
            for scenario in ("write", "read", "mixed"):
                results.append(await run_scenario(manager, preloaded, scenario, concurrency, args.samples))
        finally:
            keys: list[str] = []
            async for key in store._redis.scan_iter(match=f"{namespace}:*"):
                keys.append(key)
                if len(keys) >= 500:
                    await store._redis.delete(*keys)
                    keys.clear()
            if keys:
                await store._redis.delete(*keys)
            await store.close()
    report = {
        "text_bytes": len(TEXT_1KI.encode("utf-8")),
        "preloaded_working_memory": args.preload,
        "samples_per_scenario": args.samples,
        "scenarios": ["write", "read", "mixed_50_50"],
        "manager": "MockWorkingMemoryManager -> RedisMemoryStore",
        "results": results,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
