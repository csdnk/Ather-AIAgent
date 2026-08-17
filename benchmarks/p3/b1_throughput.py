# ruff: noqa: E501
from __future__ import annotations

import argparse
import asyncio
import math
import os
import sys
import time
import uuid
from pathlib import Path
from typing import Any

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from benchmarks.p3.common import DEFAULT_OUTPUT, latency_summary, print_json, write_csv, write_json

TEXTS = {
    "short": "P3 Sidecar performs real CPU embedding for every intercepted request.",
    "medium": (
        "Aether P3 provides an engineering path from request interception to semantic memory. "
        "The B1 Sidecar receives a traceable payload, chunks text under a fixed policy, and uses "
        "the configured FastEmbed ONNX CPU backend to produce real vectors. This benchmark keeps "
        "the input stable across every concurrency and batch experiment, validates every returned "
        "vector, and reports HTTP request QPS separately from embedded item throughput. The result "
        "is an engineering baseline for contract discussion, not an optimized or mocked score."
    ),
    "long": (
        "Aether P3 memory processing must remain observable and reproducible across the B1, B2, "
        "and B3 boundaries. Each request carries stable tenant, source, request, and trace fields. "
        "B1 performs interception, text chunking, and real CPU embedding; B2 manages working and "
        "long-term memory flows; B3 applies heuristic scheduling decisions and records feedback. "
        "The benchmark deliberately includes HTTP serialization, Sidecar queueing, model inference, "
        "and vector serialization so the measured result reflects the deployed boundary. "
    )
    * 4,
}

CSV_FIELDS = [
    "text_class",
    "batch_size",
    "concurrency",
    "repeat",
    "duration_s",
    "total_requests",
    "successful_requests",
    "failed_requests",
    "total_items",
    "successful_items",
    "request_qps",
    "item_per_second",
    "p50_ms",
    "p95_ms",
    "p99_ms",
    "error_rate",
    "embedding_model",
    "embedding_dimension",
    "text_bytes",
    "text_characters",
    "estimated_tokens",
    "cpu_threads",
    "response_includes_vectors",
]


def request_payload(batch_size: int, sequence: int, run_id: str, text: str) -> dict[str, Any]:
    return {
        "items": [
            {
                "request_id": f"p3-b1-{run_id}-{sequence}-{index}",
                "trace_id": uuid.uuid4().hex,
                "tenant_id": "p3-baseline",
                "source_type": "benchmark",
                "source_id": f"p3-b1-source-{run_id}",
                "chunk_id": f"p3-b1-{sequence}-{index}",
                "chunk_text": text,
                "embedding_required": True,
                "metadata": {"baseline": "v0.1"},
            }
            for index in range(batch_size)
        ]
    }


def validate_response(
    body: Any, batch_size: int, expected_dimension: int
) -> tuple[int, str | None, int]:
    if not isinstance(body, dict) or not isinstance(body.get("results"), list):
        raise ValueError("Sidecar response does not contain a results list")
    results = body["results"]
    if len(results) != batch_size:
        raise ValueError(f"expected {batch_size} results, received {len(results)}")
    model: str | None = None
    dimension = 0
    for result in results:
        if not isinstance(result, dict) or result.get("status") != "success":
            raise ValueError(f"Sidecar item failed: {result}")
        chunks = result.get("chunks")
        if not isinstance(chunks, list) or not chunks:
            raise ValueError("Sidecar item has no chunks")
        for chunk in chunks:
            vector = chunk.get("vector") if isinstance(chunk, dict) else None
            if not isinstance(vector, list) or not vector:
                raise ValueError("Sidecar returned an empty vector")
            if not all(
                isinstance(value, (int, float)) and math.isfinite(float(value)) for value in vector
            ):
                raise ValueError("Sidecar returned a non-finite vector")
            if expected_dimension > 0 and len(vector) != expected_dimension:
                raise ValueError(f"vector dimension {len(vector)} != expected {expected_dimension}")
            if dimension and len(vector) != dimension:
                raise ValueError("vector dimension changed within a response")
            dimension = len(vector)
        model = str(result.get("embedding_model") or model or "unknown")
    return len(results), model, dimension


async def exercise(
    client: Any,
    *,
    batch_size: int,
    concurrency: int,
    duration: float,
    text: str,
    expected_dimension: int,
    record_latency: bool,
) -> dict[str, Any]:
    stop_at = time.perf_counter() + duration
    sequence = 0
    lock = asyncio.Lock()
    latencies: list[float] = []
    successes = failures = successful_items = 0
    model: str | None = None
    dimension = 0
    run_id = uuid.uuid4().hex[:12]

    async def worker() -> None:
        nonlocal sequence, successes, failures, successful_items, model, dimension
        while time.perf_counter() < stop_at:
            async with lock:
                current = sequence
                sequence += 1
            started = time.perf_counter()
            try:
                response = await client.post(
                    "/v1/intercept",
                    json=request_payload(batch_size, current, run_id, text),
                )
                response.raise_for_status()
                items, response_model, response_dimension = validate_response(
                    response.json(), batch_size, expected_dimension
                )
                successes += 1
                successful_items += items
                model = response_model or model
                dimension = response_dimension or dimension
            except Exception:
                failures += 1
            finally:
                if record_latency:
                    latencies.append((time.perf_counter() - started) * 1000)

    measured_started = time.perf_counter()
    await asyncio.gather(*(worker() for _ in range(concurrency)))
    elapsed = time.perf_counter() - measured_started
    return {
        "duration_s": elapsed,
        "total_requests": successes + failures,
        "successful_requests": successes,
        "failed_requests": failures,
        "total_items": (successes + failures) * batch_size,
        "successful_items": successful_items,
        "request_qps": successes / elapsed,
        "item_per_second": successful_items / elapsed,
        **latency_summary(latencies),
        "error_rate": failures / max(successes + failures, 1),
        "embedding_model": model,
        "embedding_dimension": dimension,
        "response_includes_vectors": successes > 0 and dimension > 0,
    }


async def run(args: argparse.Namespace) -> dict[str, Any]:
    try:
        import httpx
    except ImportError as exc:
        return {"status": "BLOCKED", "reason": f"httpx is required: {exc}", "results": []}

    text = TEXTS[args.text_class]
    text_info = {
        "class": args.text_class,
        "utf8_bytes": len(text.encode("utf-8")),
        "characters": len(text),
        "estimated_tokens": None,
        "token_reason": "model tokenizer is not exposed by the Sidecar contract",
        "sha256": __import__("hashlib").sha256(text.encode("utf-8")).hexdigest(),
    }
    results: list[dict[str, Any]] = []
    timeout = httpx.Timeout(args.request_timeout)
    limits = httpx.Limits(
        max_connections=max(args.concurrency), max_keepalive_connections=max(args.concurrency)
    )
    async with httpx.AsyncClient(
        base_url=args.base_url, timeout=timeout, limits=limits, trust_env=False
    ) as client:
        try:
            ready = await client.get("/health/ready")
            ready.raise_for_status()
            health = ready.json()
            if health.get("status") != "ready":
                raise RuntimeError(f"Sidecar is not ready: {health}")
            backend = str(health.get("backend", "")).lower()
            if "mock" in backend:
                raise RuntimeError("formal B1 baseline refuses a Mock Embedding backend")
            expected_dimension = int(health.get("dimension") or args.expected_dimension)
        except Exception as exc:
            return {
                "status": "BLOCKED",
                "reason": f"B1 readiness failed: {type(exc).__name__}: {exc}",
                "results": [],
                "text": text_info,
            }

        for batch_size in args.batch_sizes:
            for concurrency in args.concurrency:
                for repeat in range(1, args.repeats + 1):
                    await exercise(
                        client,
                        batch_size=batch_size,
                        concurrency=concurrency,
                        duration=args.warmup,
                        text=text,
                        expected_dimension=expected_dimension,
                        record_latency=False,
                    )
                    row = await exercise(
                        client,
                        batch_size=batch_size,
                        concurrency=concurrency,
                        duration=args.measurement,
                        text=text,
                        expected_dimension=expected_dimension,
                        record_latency=True,
                    )
                    row.update(
                        {
                            "text_class": args.text_class,
                            "batch_size": batch_size,
                            "concurrency": concurrency,
                            "repeat": repeat,
                            "text_bytes": text_info["utf8_bytes"],
                            "text_characters": text_info["characters"],
                            "estimated_tokens": None,
                            "cpu_threads": int(os.getenv("AETHER_B1_THREADS", "1")),
                        }
                    )
                    results.append(row)

    primary = [row for row in results if row["batch_size"] == 1 and row["error_rate"] == 0]
    best_primary = max(primary, key=lambda row: row["request_qps"], default=None)
    best_secondary = max(results, key=lambda row: row["item_per_second"], default=None)
    primary_qps = best_primary["request_qps"] if best_primary else None
    contract_status = "PASS" if primary_qps is not None and primary_qps >= 2000 else "FAIL"
    return {
        "status": "COMPLETED",
        "method": "real HTTP /v1/intercept including Sidecar queueing, FastEmbed inference, and returned vectors",
        "health": health,
        "text": text_info,
        "contract": {
            "contract_target": 2000,
            "target_unit": "HTTP request QPS",
            "primary_metric": "batch1_request_qps",
            "primary_value": primary_qps,
            "status": contract_status,
            "note": "Batch=8 item throughput is secondary and cannot substitute for HTTP request QPS.",
        },
        "results": results,
        "best_primary": best_primary,
        "best_secondary": best_secondary,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Real P3 B1 Sidecar/FastEmbed throughput baseline")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--base-url", default=os.getenv("AETHER_B1_SIDECAR_URL", "http://127.0.0.1:18081")
    )
    parser.add_argument("--batch-sizes", nargs="+", type=int, default=[1, 8])
    parser.add_argument("--concurrency", nargs="+", type=int, default=[1, 4, 8, 16])
    parser.add_argument("--warmup", type=float, default=10)
    parser.add_argument("--measurement", type=float, default=30)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--text-class", choices=sorted(TEXTS), default="medium")
    parser.add_argument("--expected-dimension", type=int, default=512)
    parser.add_argument("--request-timeout", type=float, default=120)
    args = parser.parse_args()
    if (
        min(args.batch_sizes + args.concurrency) <= 0
        or args.warmup < 0
        or args.measurement <= 0
        or args.repeats <= 0
    ):
        parser.error(
            "batch, concurrency, measurement, and repeats must be positive; warmup must be non-negative"
        )

    result = asyncio.run(run(args))
    output = args.output / "b1"
    write_json(output / "results.json", result)
    write_csv(output / "results.csv", result.get("results", []), CSV_FIELDS)
    write_json(
        output / "best_result.json",
        {
            "status": result.get("status"),
            "contract": result.get("contract"),
            "best_primary": result.get("best_primary"),
            "best_secondary": result.get("best_secondary"),
        },
    )
    print_json(result)


if __name__ == "__main__":
    main()
