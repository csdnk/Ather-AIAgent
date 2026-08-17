from __future__ import annotations

import argparse
import asyncio
import itertools
import sys
import time
import uuid
from pathlib import Path
from typing import Any

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from benchmarks.p3.b1_throughput import TEXTS, request_payload, validate_response
from benchmarks.p3.common import latency_summary, print_json, write_csv, write_json


CSV_FIELDS = [
    "instance_count",
    "batch_size",
    "concurrency",
    "repeat",
    "duration_s",
    "successful_requests",
    "failed_requests",
    "request_qps",
    "successful_items",
    "item_per_second",
    "p50_ms",
    "p95_ms",
    "p99_ms",
    "error_rate",
]


async def exercise(
    clients: list[Any],
    *,
    batch_size: int,
    concurrency: int,
    duration: float,
    text: str,
    expected_dimension: int,
    record_latency: bool,
) -> dict[str, Any]:
    stop_at = time.perf_counter() + duration
    sequence = itertools.count()
    latencies: list[float] = []
    successes = failures = successful_items = 0
    per_instance = [
        {"successful_requests": 0, "failed_requests": 0, "successful_items": 0}
        for _ in clients
    ]
    run_id = uuid.uuid4().hex[:12]

    async def worker(worker_index: int) -> None:
        nonlocal successes, failures, successful_items
        instance_index = worker_index % len(clients)
        client = clients[instance_index]
        counters = per_instance[instance_index]
        while time.perf_counter() < stop_at:
            current = next(sequence)
            started = time.perf_counter()
            try:
                response = await client.post(
                    "/v1/intercept",
                    json=request_payload(batch_size, current, run_id, text),
                )
                response.raise_for_status()
                items, _, _ = validate_response(
                    response.json(), batch_size, expected_dimension
                )
                successes += 1
                successful_items += items
                counters["successful_requests"] += 1
                counters["successful_items"] += items
            except Exception:
                failures += 1
                counters["failed_requests"] += 1
            finally:
                if record_latency:
                    latencies.append((time.perf_counter() - started) * 1000)

    started = time.perf_counter()
    await asyncio.gather(*(worker(index) for index in range(concurrency)))
    elapsed = time.perf_counter() - started
    return {
        "duration_s": elapsed,
        "successful_requests": successes,
        "failed_requests": failures,
        "request_qps": successes / elapsed,
        "successful_items": successful_items,
        "item_per_second": successful_items / elapsed,
        **latency_summary(latencies),
        "error_rate": failures / max(successes + failures, 1),
        "per_instance": per_instance,
    }


async def run(args: argparse.Namespace) -> dict[str, Any]:
    try:
        import httpx
    except ImportError as exc:
        return {"status": "BLOCKED", "reason": f"httpx is required: {exc}"}

    timeout = httpx.Timeout(args.request_timeout)
    per_instance_connections = max(2, (args.concurrency + len(args.base_urls) - 1) // len(args.base_urls))
    clients = [
        httpx.AsyncClient(
            base_url=url,
            timeout=timeout,
            limits=httpx.Limits(
                max_connections=per_instance_connections,
                max_keepalive_connections=per_instance_connections,
            ),
            trust_env=False,
        )
        for url in args.base_urls
    ]
    health: list[dict[str, Any]] = []
    try:
        for url, client in zip(args.base_urls, clients, strict=True):
            response = await client.get("/health/ready")
            response.raise_for_status()
            body = response.json()
            if body.get("status") != "ready" or "mock" in str(body.get("backend", "")).lower():
                raise RuntimeError(f"unready or mock Sidecar at {url}: {body}")
            health.append({"base_url": url, **body})

        expected_dimension = int(health[0].get("dimension") or args.expected_dimension)
        if any(int(item.get("dimension") or 0) != expected_dimension for item in health):
            raise RuntimeError("Sidecar embedding dimensions do not match")

        rows: list[dict[str, Any]] = []
        text = TEXTS[args.text_class]
        for repeat in range(1, args.repeats + 1):
            if args.warmup:
                await exercise(
                    clients,
                    batch_size=args.batch_size,
                    concurrency=args.concurrency,
                    duration=args.warmup,
                    text=text,
                    expected_dimension=expected_dimension,
                    record_latency=False,
                )
            row = await exercise(
                clients,
                batch_size=args.batch_size,
                concurrency=args.concurrency,
                duration=args.measurement,
                text=text,
                expected_dimension=expected_dimension,
                record_latency=True,
            )
            row.update(
                {
                    "instance_count": len(clients),
                    "batch_size": args.batch_size,
                    "concurrency": args.concurrency,
                    "repeat": repeat,
                }
            )
            rows.append(row)

        valid = [row for row in rows if row["error_rate"] <= args.max_error_rate]
        best = max(valid, key=lambda row: row["request_qps"], default=None)
        primary_value = best["request_qps"] if best else None
        return {
            "status": "COMPLETED",
            "method": "round-robin HTTP load across real B1 Sidecar replicas",
            "health": health,
            "text_class": args.text_class,
            "contract": {
                "target": args.target_qps,
                "unit": "successful HTTP request QPS",
                "primary_value": primary_value,
                "status": "PASS" if primary_value is not None and primary_value >= args.target_qps else "FAIL",
                "max_error_rate": args.max_error_rate,
            },
            "results": rows,
            "best_result": best,
        }
    except Exception as exc:
        return {"status": "BLOCKED", "reason": f"{type(exc).__name__}: {exc}"}
    finally:
        await asyncio.gather(*(client.aclose() for client in clients))


def main() -> None:
    parser = argparse.ArgumentParser(description="B1 multi-replica scaling benchmark")
    parser.add_argument("--base-urls", nargs="+", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--batch-size", type=int, choices=[1, 8], default=1)
    parser.add_argument("--concurrency", type=int, required=True)
    parser.add_argument("--warmup", type=float, default=10)
    parser.add_argument("--measurement", type=float, default=30)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--text-class", choices=sorted(TEXTS), default="medium")
    parser.add_argument("--expected-dimension", type=int, default=512)
    parser.add_argument("--request-timeout", type=float, default=120)
    parser.add_argument("--target-qps", type=float, default=2000)
    parser.add_argument("--max-error-rate", type=float, default=0.01)
    args = parser.parse_args()
    if args.concurrency <= 0 or args.warmup < 0 or args.measurement <= 0 or args.repeats <= 0:
        parser.error("concurrency, measurement, and repeats must be positive")

    result = asyncio.run(run(args))
    args.output.mkdir(parents=True, exist_ok=True)
    write_json(args.output / "results.json", result)
    write_csv(args.output / "results.csv", result.get("results", []), CSV_FIELDS)
    print_json(result)


if __name__ == "__main__":
    main()
