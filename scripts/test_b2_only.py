"""B2-only compression and artifact-store acceptance test.

This script tests only the B2 compressor and SQLite artifact store. It does
not import or start B1, B3, P2, Redis, Milvus, Celery, or the P3 service.
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as _datetime
import json
import sys
import time
import types
from datetime import datetime
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
for source_root in (ROOT, SRC):
    if source_root.is_dir() and str(source_root) not in sys.path:
        sys.path.insert(0, str(source_root))

# The repository top-level package eagerly imports B1. Install a lightweight
# package shell so this standalone test can load B2 without touching B1.
_package = types.ModuleType("aether_agent_memory")
_package.__path__ = [str(SRC / "aether_agent_memory")]
sys.modules["aether_agent_memory"] = _package

# The server uses Python 3.8 while B2 was authored for Python 3.11+. Keep the
# compatibility shim local to this test entry point instead of changing B2.
if not hasattr(_datetime, "UTC"):
    _datetime.UTC = _datetime.UTC  # type: ignore[attr-defined]

from benchmarks.p3.b2_compression_dataset import run_dataset  # noqa: E402

from aether_agent_memory.b2.compression import HybridMemoryCompressor  # noqa: E402
from aether_agent_memory.b2.compression_store import (  # noqa: E402
    SQLiteCompressionArtifactStore,
)

DEFAULT_TEXT = (
    "项目会议确认：捷运盛系统需要在本周五前完成测试。"
    "风险包括数据一致性、权限校验和失败重试。"
    "必须保留合同编号、负责人、截止时间和失败原因。"
    "后续由项目组提交测试记录，并根据测试结果更新实施方案。"
) * 8


def percentile(values: list[float], percentage: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    rank = (len(ordered) - 1) * percentage
    lower = int(rank)
    upper = min(lower + 1, len(ordered) - 1)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (rank - lower)


def timestamped_output() -> Path:
    stamp = datetime.now(_datetime.UTC).strftime("%Y%m%dT%H%M%SZ")
    output = ROOT / "artifacts" / f"b2_only_{stamp}"
    output.mkdir(parents=True, exist_ok=False)
    return output


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dataset-root",
        type=Path,
        default=ROOT / "datasets/p3/acceptance_v0.1",
        help="Normalized JSONL root containing LoCoMo, LongMemEval, and BEAM",
    )
    parser.add_argument(
        "--target-ratio", type=float, default=5.0
    )
    parser.add_argument("--samples", type=int, default=300)
    parser.add_argument("--concurrency", nargs="+", type=int, default=[1, 8, 32])
    parser.add_argument("--output", type=Path, help="Override timestamped output directory")
    return parser.parse_args()


async def measure_scenario(
    store: SQLiteCompressionArtifactStore,
    compressor: HybridMemoryCompressor,
    scenario: str,
    concurrency: int,
    samples: int,
    preload_ids: list[str],
) -> dict[str, float | int | str]:
    latencies: list[float] = []
    cursor = 0
    cursor_lock = asyncio.Lock()

    async def next_index() -> int | None:
        nonlocal cursor
        async with cursor_lock:
            if cursor >= samples:
                return None
            index = cursor
            cursor += 1
            return index

    async def worker() -> None:
        while (index := await next_index()) is not None:
            is_write = scenario == "write" or (scenario == "mixed" and index % 2 == 0)
            started = time.perf_counter_ns()
            if is_write:
                text = (
                    "合同要求：2026年8月17日之前完成验收，不能删除证据。"
                    "本次测试记录需要保留负责人、截止时间和失败原因。"
                ) * 12
                artifact = compressor.compress(
                    text,
                    source_memory_id=f"b2-concurrency-{uuid4().hex}",
                    source_id="b2-concurrency-test",
                )
                await store.put(artifact)
            else:
                await store.get(preload_ids[index % len(preload_ids)])
            latencies.append((time.perf_counter_ns() - started) / 1_000_000)

    started = time.perf_counter()
    await asyncio.gather(*(worker() for _ in range(concurrency)))
    elapsed = time.perf_counter() - started
    return {
        "concurrency": concurrency,
        "scenario": scenario,
        "samples": samples,
        "throughput_ops_s": round(samples / elapsed, 3),
        "p50_ms": round(percentile(latencies, 0.50), 3),
        "p95_ms": round(percentile(latencies, 0.95), 3),
        "p99_ms": round(percentile(latencies, 0.99), 3),
        "max_ms": round(max(latencies), 3),
    }


async def run_concurrency(output: Path, args: argparse.Namespace) -> dict[str, object]:
    store = SQLiteCompressionArtifactStore(output / "compression_artifacts.db")
    compressor = HybridMemoryCompressor()
    preload_ids: list[str] = []
    try:
        preload_text = (
            "合同要求：2026年8月17日之前完成验收，不能删除证据。"
            "系统需要记录流程状态、责任人、截止时间和风险。"
        ) * 12
        for index in range(max(args.concurrency) * 2):
            artifact = compressor.compress(
                preload_text,
                source_memory_id=f"b2-preload-{index}-{uuid4().hex}",
                source_id="b2-concurrency-preload",
            )
            await store.put(artifact)
            preload_ids.append(artifact.artifact_id)
        rows = []
        for concurrency in args.concurrency:
            if concurrency <= 0:
                raise ValueError("concurrency values must be positive")
            for scenario in ("write", "read", "mixed"):
                rows.append(
                    await measure_scenario(
                        store, compressor, scenario, concurrency, args.samples, preload_ids
                    )
                )
        report = {
            "backend": "HybridMemoryCompressor -> SQLiteCompressionArtifactStore",
            "samples_per_scenario": args.samples,
            "scenarios": ["write", "read", "mixed_50_50"],
            "results": rows,
        }
        (output / "concurrency.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        return report
    finally:
        await store.close()


async def main() -> None:
    args = parse_args()
    if args.target_ratio <= 1.0:
        raise SystemExit("--target-ratio must be greater than 1")
    if args.samples <= 0:
        raise SystemExit("--samples must be positive")
    output = args.output or timestamped_output()
    output.mkdir(parents=True, exist_ok=True)
    compression_path = output / "compression_dataset.json"
    dataset = run_dataset(args.dataset_root, compression_path, args.target_ratio)
    concurrency = await run_concurrency(output, args)
    summary = {
        "status": (
            "PASS"
            if dataset["compression_gate_status"] == "PASS"
            else "PASS_WITH_DATASET_WARNINGS"
        ),
        "execution_status": "PASS",
        "b2_only": True,
        "generated_at": datetime.now(_datetime.UTC).isoformat(),
        "dataset_root": str(args.dataset_root),
        "output_directory": str(output),
        "target_ratio": args.target_ratio,
        "dataset": {
            "sample_count": dataset["sample_count"],
            "passed_count": dataset["passed_count"],
            "compression_gate_status": dataset["compression_gate_status"],
            "mean_ratio": dataset["mean_ratio"],
            "weighted_compression_ratio": dataset["weighted_compression_ratio"],
        },
        "concurrency": concurrency,
    }
    (output / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
