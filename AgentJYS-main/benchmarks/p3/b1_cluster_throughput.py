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

from benchmarks.p3.b1_throughput import (
    PROFILE_FIELDS,
    PROFILE_STAGES,
    SERVER_DYNAMIC_BATCH_KEYS,
    SERVER_METRIC_FIELDS,
    TEXTS,
    _flatten_profile_summary,
    _profile_summary,
    fetch_server_metrics,
    request_payload,
    validate_response,
)
from benchmarks.p3.common import latency_summary, print_json, write_csv, write_json

CSV_FIELDS = [
    "instance_count",
    "request_mode",
    "batch_size",
    "request_items",
    "concurrency",
    "repeat",
    "duration_s",
    "successful_requests",
    "failed_requests",
    "request_qps",
    "successful_items",
    "item_per_second",
    "successful_vectors",
    "effective_item_qps",
    "vector_qps",
    "p50_ms",
    "p95_ms",
    "p99_ms",
    "error_rate",
]
CSV_FIELDS.extend(SERVER_METRIC_FIELDS)
CSV_FIELDS.extend(PROFILE_FIELDS)


def _flatten_cluster_server_metrics(snapshots: list[dict[str, Any]]) -> dict[str, Any]:
    flattened: dict[str, Any] = {field: None for field in SERVER_METRIC_FIELDS}
    if not snapshots:
        return flattened
    errors = [str(item.get("metrics_error")) for item in snapshots if item.get("metrics_error")]
    flattened["server_metrics_error"] = "; ".join(errors) if errors else None
    flattened["server_http_request_qps"] = sum(
        float(item.get("http_request_qps") or 0.0) for item in snapshots
    )
    flattened["server_effective_item_qps"] = sum(
        float(item.get("effective_item_qps") or 0.0) for item in snapshots
    )
    flattened["server_vector_qps"] = sum(float(item.get("vector_qps") or 0.0) for item in snapshots)

    dynamic_snapshots: list[dict[str, Any]] = []
    for item in snapshots:
        dynamic = item.get("dynamic_batch")
        if isinstance(dynamic, dict):
            dynamic_snapshots.append(dynamic)
    if not dynamic_snapshots:
        return flattened

    sum_keys = {
        "queue_depth",
        "queue_capacity",
        "submitted_items",
        "completed_items",
        "dropped_items",
        "batch_count",
        "fallback_count",
        "queue_timeout_count",
        "backend_timeout_count",
        "backend_error_count",
    }
    max_keys = {
        "batch_items_max",
        "batch_items_p95",
        "batch_tokens_p95",
        "queue_wait_p95_ms",
        "queue_wait_p99_ms",
        "backend_inference_p95_ms",
        "backend_inference_p99_ms",
    }
    bool_keys = {"dynamic_batch_enabled"}
    for key in SERVER_DYNAMIC_BATCH_KEYS:
        values = [item.get(key) for item in dynamic_snapshots]
        field = f"server_{key}"
        if key in bool_keys:
            flattened[field] = any(bool(value) for value in values)
        elif key in sum_keys:
            flattened[field] = sum(float(value or 0.0) for value in values)
        elif key in max_keys:
            flattened[field] = max(float(value or 0.0) for value in values)
        else:
            flattened[field] = sum(float(value or 0.0) for value in values) / len(values)
    return flattened


async def exercise(
    clients: list[Any],
    *,
    batch_size: int,
    concurrency: int,
    duration: float,
    text: str,
    expected_dimension: int,
    record_latency: bool,
    request_mode: str,
) -> dict[str, Any]:
    stop_at = time.perf_counter() + duration
    sequence = itertools.count()
    latencies: list[float] = []
    profile_samples: dict[str, list[float]] = {stage: [] for stage in PROFILE_STAGES}
    successes = failures = successful_items = successful_vectors = 0
    per_instance = [
        {"successful_requests": 0, "failed_requests": 0, "successful_items": 0}
        for _ in clients
    ]
    run_id = uuid.uuid4().hex[:12]

    async def worker(worker_index: int) -> None:
        nonlocal successes, failures, successful_items, successful_vectors
        instance_index = worker_index % len(clients)
        client = clients[instance_index]
        counters = per_instance[instance_index]
        while time.perf_counter() < stop_at:
            current = next(sequence)
            started = time.perf_counter()
            response_profiling: dict[str, Any] = {}
            try:
                request_items = 1 if request_mode == "single-item" else batch_size
                response = await client.post(
                    "/v1/intercept",
                    json=request_payload(request_items, current, run_id, text),
                )
                response.raise_for_status()
                body = response.json()
                response_profiling = body.get("profiling") or {}
                items, vectors, _, _ = validate_response(body, request_items, expected_dimension)
                successes += 1
                successful_items += items
                successful_vectors += vectors
                counters["successful_requests"] += 1
                counters["successful_items"] += items
            except Exception:
                failures += 1
                counters["failed_requests"] += 1
            finally:
                if record_latency:
                    elapsed_ms = (time.perf_counter() - started) * 1000
                    latencies.append(elapsed_ms)
                    if response_profiling:
                        for stage in PROFILE_STAGES:
                            if stage == "network":
                                continue
                            value = response_profiling.get(f"{stage}_ms")
                            if value is not None:
                                profile_samples[stage].append(float(value))
                        server_total = response_profiling.get("server_total_ms")
                        if server_total is not None:
                            profile_samples["network"].append(
                                max(0.0, elapsed_ms - float(server_total))
                            )

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
        "successful_vectors": successful_vectors,
        "effective_item_qps": successful_items / elapsed,
        "vector_qps": successful_vectors / elapsed,
        **latency_summary(latencies),
        "profiling": _profile_summary(profile_samples),
        "error_rate": failures / max(successes + failures, 1),
        "per_instance": per_instance,
    }


async def run(args: argparse.Namespace) -> dict[str, Any]:
    try:
        import httpx
    except ImportError as exc:
        return {"status": "BLOCKED", "reason": f"httpx is required: {exc}"}

    timeout = httpx.Timeout(args.request_timeout)
    per_instance_connections = max(
        2,
        (args.concurrency + len(args.base_urls) - 1) // len(args.base_urls),
    )
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
            if (
                args.expected_backend
                and str(body.get("backend", "")).lower() != args.expected_backend.lower()
            ):
                raise RuntimeError(
                    f"unexpected backend at {url}: {body.get('backend')} != {args.expected_backend}"
                )
            if (
                args.expected_precision
                and str(body.get("precision", "")).upper() != args.expected_precision.upper()
            ):
                raise RuntimeError(
                    f"unexpected precision at {url}: "
                    f"{body.get('precision')} != {args.expected_precision}"
                )
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
                    request_mode=args.request_mode,
                )
            row = await exercise(
                clients,
                batch_size=args.batch_size,
                concurrency=args.concurrency,
                duration=args.measurement,
                text=text,
                expected_dimension=expected_dimension,
                record_latency=True,
                request_mode=args.request_mode,
            )
            row.update(
                {
                    "instance_count": len(clients),
                    "request_mode": args.request_mode,
                    "batch_size": args.batch_size,
                    "request_items": 1 if args.request_mode == "single-item" else args.batch_size,
                    "concurrency": args.concurrency,
                    "repeat": repeat,
                }
            )
            row.update(_flatten_profile_summary(row.get("profiling", {})))
            metrics = await asyncio.gather(*(fetch_server_metrics(client) for client in clients))
            row["server_metrics"] = metrics
            row.update(_flatten_cluster_server_metrics(metrics))
            rows.append(row)

        valid = [row for row in rows if row["error_rate"] <= args.max_error_rate]
        best = max(valid, key=lambda row: row["effective_item_qps"], default=None)
        primary_value = best["effective_item_qps"] if best else None
        return {
            "status": "COMPLETED",
            "method": "round-robin HTTP load across real B1 Sidecar replicas",
            "health": health,
            "text_class": args.text_class,
            "contract": {
                "target": args.target_qps,
                "unit": "effective embedding item QPS",
                "primary_metric": "effective_item_qps",
                "primary_value": primary_value,
                "status": (
                    "PASS"
                    if primary_value is not None and primary_value >= args.target_qps
                    else "FAIL"
                ),
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
    parser.add_argument("--batch-size", type=int, choices=[1, 2, 4, 8, 16, 32, 64], default=1)
    parser.add_argument(
        "--request-mode",
        choices=["batch", "single-item"],
        default="batch",
        help=(
            "single-item sends one item per HTTP request so dynamic batching must happen "
            "in the sidecar."
        ),
    )
    parser.add_argument("--concurrency", type=int, required=True)
    parser.add_argument("--warmup", type=float, default=10)
    parser.add_argument("--measurement", type=float, default=30)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--text-class", choices=sorted(TEXTS), default="medium")
    parser.add_argument("--expected-dimension", type=int, default=512)
    parser.add_argument("--request-timeout", type=float, default=120)
    parser.add_argument("--target-qps", type=float, default=2000)
    parser.add_argument("--max-error-rate", type=float, default=0.01)
    parser.add_argument("--expected-backend", default="")
    parser.add_argument("--expected-precision", default="")
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
