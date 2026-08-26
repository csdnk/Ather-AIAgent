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
DATASET_FILES = ("beam.jsonl", "locomo.jsonl", "longmemeval.jsonl")


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
    texts: list[str],
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
                await manager.write(make_memory(texts[index % len(texts)]))
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


def load_dataset_texts(dataset_root: Path) -> dict[str, tuple[Path, list[str]]]:
    datasets: dict[str, tuple[Path, list[str]]] = {}
    for filename in DATASET_FILES:
        path = dataset_root / filename
        if not path.is_file():
            raise FileNotFoundError(f"Missing latency dataset: {path}")
        texts: list[str] = []
        with path.open(encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                if not line.strip():
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise ValueError(f"Invalid JSON in {path}:{line_number}") from exc
                texts.append(json.dumps(record, ensure_ascii=False, sort_keys=True))
        if not texts:
            raise ValueError(f"Latency dataset is empty: {path}")
        datasets[path.stem] = (path, texts)
    return datasets


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--redis-url", default="redis://localhost:6379/0")
    parser.add_argument("--samples", type=int, default=10_000)
    parser.add_argument("--preload", type=int, default=256)
    parser.add_argument("--dataset-root", type=Path)
    parser.add_argument("--output", type=Path, default=Path("artifacts/p3_b2_working_latency.json"))
    args = parser.parse_args()
    results: list[dict[str, float | int | str]] = []
    if args.dataset_root:
        datasets = load_dataset_texts(args.dataset_root)
    else:
        datasets = {"synthetic_1ki": (Path("<synthetic>"), [TEXT_1KI])}

    for dataset_name, (_dataset_path, texts) in datasets.items():
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
            preloaded = [
                make_memory(texts[index % len(texts)])
                for index in range(min(args.preload, len(texts)))
            ]
            try:
                for memory in preloaded:
                    await manager.write(memory)
                for scenario in ("write", "read", "mixed"):
                    result = await run_scenario(
                        manager, preloaded, texts, scenario, concurrency, args.samples
                    )
                    result["dataset"] = dataset_name
                    results.append(result)
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
        "data_source": "jsonl" if args.dataset_root else "synthetic",
        "dataset_root": str(args.dataset_root) if args.dataset_root else None,
        "datasets": [
            {
                "dataset": name,
                "source_file": str(path),
                "records": len(texts),
                "preloaded_working_memory": min(args.preload, len(texts)),
                "text_bytes_min": min(len(text.encode("utf-8")) for text in texts),
                "text_bytes_max": max(len(text.encode("utf-8")) for text in texts),
                "text_bytes_mean": round(
                    sum(len(text.encode("utf-8")) for text in texts) / len(texts),
                    3,
                ),
            }
            for name, (path, texts) in datasets.items()
        ],
        "preloaded_working_memory_requested": args.preload,
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
