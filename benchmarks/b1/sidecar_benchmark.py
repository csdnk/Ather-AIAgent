from __future__ import annotations

import argparse
import json
import statistics
import time
import uuid
from pathlib import Path

import httpx


def payload(batch_size: int, iteration: int, run_id: str) -> dict[str, object]:
    return {
        "items": [
            {
                "request_id": f"bench-{run_id}-{batch_size}-{iteration}-{index}",
                "trace_id": uuid.uuid4().hex,
                "tenant_id": "tenant-benchmark",
                "source_type": "rag_document",
                "source_id": f"bench-source-{batch_size}-{iteration}-{index}",
                "chunk_id": f"bench-chunk-{batch_size}-{iteration}-{index}",
                "chunk_text": (
                    "Aether B1 measures real Sidecar interception and CPU embedding throughput."
                ),
                "embedding_required": True,
                "metadata": {"benchmark": True},
            }
            for index in range(batch_size)
        ]
    }


def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    remainder = position - lower
    return ordered[lower] * (1 - remainder) + ordered[upper] * remainder


def main() -> None:
    parser = argparse.ArgumentParser(description="Sequential real B1 Sidecar benchmark")
    parser.add_argument("--base-url", default="http://127.0.0.1:18081")
    parser.add_argument("--batch-sizes", nargs="+", type=int, default=[1, 8])
    parser.add_argument("--warmup", type=int, default=3)
    parser.add_argument("--iterations", type=int, default=20)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.warmup < 0 or args.iterations <= 0 or any(size <= 0 for size in args.batch_sizes):
        parser.error("batch sizes and iterations must be positive; warmup must not be negative")

    run_id = uuid.uuid4().hex[:8]
    records = []
    with httpx.Client(base_url=args.base_url, timeout=120.0) as client:
        ready = client.get("/health/ready")
        ready.raise_for_status()
        for batch_size in args.batch_sizes:
            for iteration in range(args.warmup):
                response = client.post(
                    "/v1/intercept",
                    json=payload(batch_size, -iteration - 1, run_id),
                )
                response.raise_for_status()
            latencies = []
            started = time.perf_counter()
            for iteration in range(args.iterations):
                request_started = time.perf_counter()
                response = client.post(
                    "/v1/intercept",
                    json=payload(batch_size, iteration, run_id),
                )
                response.raise_for_status()
                body = response.json()
                if any(item["status"] != "success" for item in body["results"]):
                    raise RuntimeError(f"benchmark request failed: {body}")
                latencies.append((time.perf_counter() - request_started) * 1000)
            elapsed = time.perf_counter() - started
            item_count = args.iterations * batch_size
            records.append(
                {
                    "batch_size": batch_size,
                    "iterations": args.iterations,
                    "items": item_count,
                    "elapsed_seconds": elapsed,
                    "items_per_second": item_count / elapsed,
                    "request_p50_ms": statistics.median(latencies),
                    "request_p95_ms": percentile(latencies, 0.95),
                    "request_p99_ms": percentile(latencies, 0.99),
                    "response_includes_vectors": True,
                }
            )
        health = ready.json()

    result = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "base_url": args.base_url,
        "health": health,
        "method": (
            "sequential HTTP, one Sidecar worker, configured CPU threads, vectors returned as JSON"
        ),
        "results": records,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    rendered = json.dumps(result, ensure_ascii=False, indent=2)
    args.output.write_text(rendered, encoding="utf-8")
    print(rendered)


if __name__ == "__main__":
    main()
