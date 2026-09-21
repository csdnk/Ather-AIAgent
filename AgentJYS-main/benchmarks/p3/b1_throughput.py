# ruff: noqa: E501
from __future__ import annotations

import argparse
import asyncio
import json
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
    "request_mode",
    "batch_size",
    "request_items",
    "concurrency",
    "repeat",
    "duration_s",
    "total_requests",
    "successful_requests",
    "failed_requests",
    "total_items",
    "successful_items",
    "successful_vectors",
    "request_qps",
    "item_per_second",
    "effective_item_qps",
    "vector_qps",
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

SERVER_DYNAMIC_BATCH_KEYS = (
    "dynamic_batch_enabled",
    "queue_depth",
    "queue_capacity",
    "submitted_items",
    "completed_items",
    "dropped_items",
    "batch_count",
    "batch_items_avg",
    "batch_items_p50",
    "batch_items_p95",
    "batch_items_max",
    "batch_tokens_avg",
    "batch_tokens_p95",
    "queue_wait_p50_ms",
    "queue_wait_p95_ms",
    "queue_wait_p99_ms",
    "backend_inference_p50_ms",
    "backend_inference_p95_ms",
    "backend_inference_p99_ms",
    "padding_efficiency",
    "fallback_count",
    "queue_timeout_count",
    "backend_timeout_count",
    "backend_error_count",
)
SERVER_METRIC_FIELDS = [
    "server_metrics_error",
    "server_http_request_qps",
    "server_effective_item_qps",
    "server_vector_qps",
    *[f"server_{key}" for key in SERVER_DYNAMIC_BATCH_KEYS],
]
CSV_FIELDS.extend(SERVER_METRIC_FIELDS)

PROFILE_STAGES = (
    "validation",
    "chunking",
    "queue_wait",
    "model_inference",
    "vector_postprocess",
    "response_postprocess",
    "server_total",
    "unaccounted",
    "network",
)
PROFILE_PERCENTILES = ("p50", "p95", "p99")
PROFILE_FIELDS = [
    f"profiling_{stage}_{percentile}_ms"
    for stage in PROFILE_STAGES
    for percentile in PROFILE_PERCENTILES
]
CSV_FIELDS.extend(PROFILE_FIELDS)


def load_dataset_texts(dataset_root: Path, limit: int) -> list[str]:
    """Extract bounded real text samples from normalized JSONL datasets."""
    if not dataset_root.is_dir() and not dataset_root.is_file():
        raise ValueError(f"B1 dataset root does not exist: {dataset_root}")
    texts: list[str] = []
    paths = [dataset_root] if dataset_root.is_file() else sorted(dataset_root.rglob("*.jsonl"))
    for path in paths:
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                if len(texts) >= limit:
                    return texts
                try:
                    sample = json.loads(line)
                except json.JSONDecodeError:
                    continue
                candidates: list[str] = []
                if isinstance(sample, dict):
                    for turn in sample.get("turns", []):
                        if isinstance(turn, dict):
                            candidates.append(str(turn.get("content") or turn.get("utterance") or ""))
                    for session in sample.get("sessions", []):
                        if isinstance(session, dict):
                            for turn in session.get("turns", []):
                                if isinstance(turn, dict):
                                    candidates.append(str(turn.get("content") or turn.get("utterance") or ""))
                    candidates.extend(str(sample.get(key) or "") for key in ("question", "content"))
                for text in candidates:
                    text = " ".join(text.split())
                    if len(text) >= 8 and text not in texts:
                        texts.append(text[:32768])
                        if len(texts) >= limit:
                            return texts
    if not texts:
        raise ValueError(f"no usable text samples found below {dataset_root}")
    return texts


def _profile_summary(samples: dict[str, list[float]]) -> dict[str, dict[str, float | None]]:
    return {
        stage: latency_summary(values)
        for stage, values in samples.items()
        if values
    }


def _flatten_profile_summary(
    summary: dict[str, dict[str, float | None]],
) -> dict[str, float | None]:
    flattened: dict[str, float | None] = {}
    for stage in PROFILE_STAGES:
        values = summary.get(stage, {})
        for percentile in PROFILE_PERCENTILES:
            flattened[f"profiling_{stage}_{percentile}_ms"] = values.get(
                f"{percentile}_ms"
            )
    return flattened


async def fetch_server_metrics(client: Any) -> dict[str, Any]:
    try:
        response = await client.get("/metrics")
        response.raise_for_status()
        body = response.json()
        if not isinstance(body, dict):
            return {"metrics_error": "metrics response is not a JSON object"}
        return body
    except Exception as exc:
        return {"metrics_error": f"{type(exc).__name__}: {exc}"}


def _flatten_server_metrics(metrics: dict[str, Any] | None) -> dict[str, Any]:
    flattened = {field: None for field in SERVER_METRIC_FIELDS}
    if not metrics:
        return flattened
    flattened["server_metrics_error"] = metrics.get("metrics_error")
    flattened["server_http_request_qps"] = metrics.get("http_request_qps")
    flattened["server_effective_item_qps"] = metrics.get("effective_item_qps")
    flattened["server_vector_qps"] = metrics.get("vector_qps")
    dynamic = metrics.get("dynamic_batch")
    if isinstance(dynamic, dict):
        for key in SERVER_DYNAMIC_BATCH_KEYS:
            flattened[f"server_{key}"] = dynamic.get(key)
    return flattened


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
) -> tuple[int, int, str | None, int]:
    if not isinstance(body, dict) or not isinstance(body.get("results"), list):
        raise ValueError("Sidecar response does not contain a results list")
    results = body["results"]
    if len(results) != batch_size:
        raise ValueError(f"expected {batch_size} results, received {len(results)}")
    model: str | None = None
    dimension = 0
    vector_count = 0
    for result in results:
        if not isinstance(result, dict) or result.get("status") != "success":
            raise ValueError(f"Sidecar item failed: {result}")
        chunks = result.get("chunks")
        if not isinstance(chunks, list) or not chunks:
            raise ValueError("Sidecar item has no chunks")
        vector_count += len(chunks)
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
    return len(results), vector_count, model, dimension


async def exercise(
    client: Any,
    *,
    batch_size: int,
    concurrency: int,
    duration: float,
    texts: list[str],
    expected_dimension: int,
    record_latency: bool,
    request_mode: str,
) -> dict[str, Any]:
    stop_at = time.perf_counter() + duration
    sequence = 0
    lock = asyncio.Lock()
    latencies: list[float] = []
    profile_samples: dict[str, list[float]] = {stage: [] for stage in PROFILE_STAGES}
    successes = failures = successful_items = successful_vectors = 0
    model: str | None = None
    dimension = 0
    run_id = uuid.uuid4().hex[:12]

    async def worker() -> None:
        nonlocal sequence, successes, failures, successful_items, successful_vectors
        nonlocal model, dimension
        while time.perf_counter() < stop_at:
            async with lock:
                current = sequence
                sequence += 1
            started = time.perf_counter()
            response_profiling: dict[str, Any] = {}
            try:
                request_items = 1 if request_mode == "single-item" else batch_size
                response = await client.post(
                    "/v1/intercept",
                    json=request_payload(
                        request_items,
                        current,
                        run_id,
                        texts[current % len(texts)],
                    ),
                )
                response.raise_for_status()
                body = response.json()
                response_profiling = body.get("profiling") or {}
                items, vectors, response_model, response_dimension = validate_response(
                    body, request_items, expected_dimension
                )
                successes += 1
                successful_items += items
                successful_vectors += vectors
                model = response_model or model
                dimension = response_dimension or dimension
            except Exception:
                failures += 1
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

    measured_started = time.perf_counter()
    await asyncio.gather(*(worker() for _ in range(concurrency)))
    elapsed = time.perf_counter() - measured_started
    return {
        "duration_s": elapsed,
        "total_requests": successes + failures,
        "successful_requests": successes,
        "failed_requests": failures,
        "total_items": (successes + failures)
        * (1 if request_mode == "single-item" else batch_size),
        "successful_items": successful_items,
        "successful_vectors": successful_vectors,
        "request_qps": successes / elapsed,
        "item_per_second": successful_items / elapsed,
        "effective_item_qps": successful_items / elapsed,
        "vector_qps": successful_vectors / elapsed,
        **latency_summary(latencies),
        "profiling": _profile_summary(profile_samples),
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

    dataset_root = args.dataset.resolve() if args.dataset else None
    texts = (
        load_dataset_texts(dataset_root, args.dataset_limit)
        if dataset_root is not None
        else [TEXTS[args.text_class]]
    )
    text_info = {
        "class": args.text_class if dataset_root is None else "real_dataset",
        "dataset_root": str(dataset_root) if dataset_root is not None else None,
        "dataset_sample_count": len(texts),
        "utf8_bytes": sum(len(text.encode("utf-8")) for text in texts),
        "characters": sum(len(text) for text in texts),
        "estimated_tokens": None,
        "token_reason": "model tokenizer is not exposed by the Sidecar contract",
        "sha256": __import__("hashlib").sha256(
            "\n".join(texts).encode("utf-8")
        ).hexdigest(),
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
                        texts=texts,
                        expected_dimension=expected_dimension,
                        record_latency=False,
                        request_mode=args.request_mode,
                    )
                    row = await exercise(
                        client,
                        batch_size=batch_size,
                        concurrency=concurrency,
                        duration=args.measurement,
                        texts=texts,
                        expected_dimension=expected_dimension,
                        record_latency=True,
                        request_mode=args.request_mode,
                    )
                    row.update(
                        {
                            "text_class": args.text_class,
                            "request_mode": args.request_mode,
                            "dataset_root": str(dataset_root) if dataset_root is not None else None,
                            "dataset_sample_count": len(texts),
                            "batch_size": batch_size,
                            "request_items": 1
                            if args.request_mode == "single-item"
                            else batch_size,
                            "concurrency": concurrency,
                            "repeat": repeat,
                            "text_bytes": text_info["utf8_bytes"],
                            "text_characters": text_info["characters"],
                            "estimated_tokens": None,
                            "cpu_threads": int(os.getenv("AETHER_B1_THREADS", "1")),
                        }
                    )
                    row.update(_flatten_profile_summary(row.get("profiling", {})))
                    metrics = await fetch_server_metrics(client)
                    row["server_metrics"] = metrics
                    row.update(_flatten_server_metrics(metrics))
                    results.append(row)

    primary = [row for row in results if row["error_rate"] == 0]
    best_primary = max(primary, key=lambda row: row["effective_item_qps"], default=None)
    best_secondary = max(results, key=lambda row: row["effective_item_qps"], default=None)
    primary_qps = best_primary["effective_item_qps"] if best_primary else None
    contract_status = "PASS" if primary_qps is not None and primary_qps >= 2000 else "FAIL"
    return {
        "status": "COMPLETED",
        "method": "real HTTP /v1/intercept including Sidecar queueing, configured backend inference, and returned vectors",
        "health": health,
        "text": text_info,
        "contract": {
            "contract_target": 2000,
            "target_unit": "effective embedding item QPS",
            "primary_metric": "effective_item_qps",
            "primary_value": primary_qps,
            "status": contract_status,
            "note": (
                "HTTP request QPS, effective item QPS, and vector QPS are reported separately."
            ),
            "effective_qps_definition": (
                "Effective Embedding QPS = successful independent embedding items per second."
            ),
        },
        "results": results,
        "best_primary": best_primary,
        "best_secondary": best_secondary,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Real P3 B1 Sidecar/backend throughput baseline")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--base-url", default=os.getenv("AETHER_B1_SIDECAR_URL", "http://127.0.0.1:18081")
    )
    parser.add_argument("--batch-sizes", nargs="+", type=int, default=[1, 8])
    parser.add_argument(
        "--request-mode",
        choices=["batch", "single-item"],
        default="batch",
        help="batch sends batch_size items per HTTP request; single-item sends one item per request so the server must cross-request batch.",
    )
    parser.add_argument("--concurrency", nargs="+", type=int, default=[1, 4, 8, 16])
    parser.add_argument("--warmup", type=float, default=10)
    parser.add_argument("--measurement", type=float, default=30)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--text-class", choices=sorted(TEXTS), default="medium")
    parser.add_argument(
        "--dataset",
        type=Path,
        help="Optional normalized JSONL dataset root; real samples replace synthetic text.",
    )
    parser.add_argument("--dataset-limit", type=int, default=12)
    parser.add_argument("--expected-dimension", type=int, default=512)
    parser.add_argument("--request-timeout", type=float, default=120)
    args = parser.parse_args()
    if (
        min(args.batch_sizes + args.concurrency) <= 0
        or args.warmup < 0
        or args.measurement <= 0
        or args.repeats <= 0
        or args.dataset_limit <= 0
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
