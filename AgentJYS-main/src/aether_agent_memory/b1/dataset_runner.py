"""Feed common local dataset formats into a running B1 Sidecar."""

from __future__ import annotations

import argparse
import csv
import json
import math
import time
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any, Literal

import httpx

DatasetFormat = Literal["json", "jsonl", "csv", "tsv"]


def _detect_format(path: Path, configured: str) -> DatasetFormat:
    if configured != "auto":
        return configured  # type: ignore[return-value]
    suffix = path.suffix.lower()
    mapping: dict[str, DatasetFormat] = {
        ".json": "json",
        ".jsonl": "jsonl",
        ".ndjson": "jsonl",
        ".csv": "csv",
        ".tsv": "tsv",
    }
    try:
        return mapping[suffix]
    except KeyError as exc:
        raise ValueError(
            f"cannot detect dataset format from extension: {suffix or '<none>'}"
        ) from exc


def iter_records(path: Path, dataset_format: DatasetFormat) -> Iterator[tuple[int, dict[str, Any]]]:
    if dataset_format == "jsonl":
        with path.open("r", encoding="utf-8-sig") as stream:
            for line_number, line in enumerate(stream, 1):
                if not line.strip():
                    continue
                value = json.loads(line)
                if not isinstance(value, dict):
                    raise ValueError(f"line {line_number} must be a JSON object")
                yield line_number, value
        return

    if dataset_format == "json":
        value = json.loads(path.read_text(encoding="utf-8-sig"))
        if isinstance(value, dict) and set(value) == {"items"}:
            value = value["items"]
        if not isinstance(value, list):
            raise ValueError("JSON dataset must be an array or an object containing only 'items'")
        for index, record in enumerate(value, 1):
            if not isinstance(record, dict):
                raise ValueError(f"JSON item {index} must be an object")
            yield index, record
        return

    delimiter = "\t" if dataset_format == "tsv" else ","
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream, delimiter=delimiter)
        if not reader.fieldnames:
            raise ValueError("delimited dataset must contain a header row")
        for row_number, record in enumerate(reader, 2):
            yield row_number, dict(record)


def _chunks(values: list[dict[str, Any]], size: int) -> Iterator[list[dict[str, Any]]]:
    for start in range(0, len(values), size):
        yield values[start : start + size]


def _compact_result(result: dict[str, Any], include_vectors: bool) -> dict[str, Any]:
    if include_vectors:
        return result
    compact = dict(result)
    compact.pop("vector", None)
    chunks = []
    for chunk in compact.get("chunks", []):
        chunk_copy = dict(chunk)
        chunk_copy.pop("vector", None)
        chunks.append(chunk_copy)
    compact["chunks"] = chunks
    return compact


def _metric_line(metrics: dict[str, Any]) -> str:
    return (
        f"RPS={metrics.get('requests_per_second', 0):.3f} "
        f"items/s={metrics.get('items_per_second', 0):.3f} "
        f"P50/P95/P99={metrics.get('request_latency_p50_ms', 0):.2f}/"
        f"{metrics.get('request_latency_p95_ms', 0):.2f}/"
        f"{metrics.get('request_latency_p99_ms', 0):.2f} ms"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a local dataset through the B1 Sidecar.")
    parser.add_argument("input", type=Path)
    parser.add_argument("--format", choices=["auto", "json", "jsonl", "csv", "tsv"], default="auto")
    parser.add_argument("--base-url", default="http://127.0.0.1:18081")
    parser.add_argument("--text-field", default="text")
    parser.add_argument("--id-field", default="id")
    parser.add_argument("--tenant-field", default="tenant_id")
    parser.add_argument("--source-type-field", default="source_type")
    parser.add_argument("--input-type-field", default="input_type")
    parser.add_argument("--tenant-id", default="dataset-local")
    parser.add_argument("--source-type", default="dataset")
    parser.add_argument("--run-id", default=uuid.uuid4().hex[:8])
    parser.add_argument("--input-type", choices=["passage", "query"], default="passage")
    parser.add_argument("--batch-size", type=int, default=8)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--timeout", type=float, default=120.0)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--include-vectors", action="store_true")
    args = parser.parse_args()
    if args.batch_size <= 0:
        parser.error("--batch-size must be positive")
    if args.limit is not None and args.limit <= 0:
        parser.error("--limit must be positive")
    if not args.input.is_file():
        parser.error(f"dataset does not exist: {args.input}")

    dataset_format = _detect_format(args.input, args.format)
    requests: list[dict[str, Any]] = []
    local_errors: list[dict[str, Any]] = []
    for sequence, (row_number, record) in enumerate(
        iter_records(args.input, dataset_format),
        1,
    ):
        if args.limit is not None and sequence > args.limit:
            break
        text_value = record.get(args.text_field)
        if not isinstance(text_value, str) or not text_value.strip():
            local_errors.append(
                {
                    "row": row_number,
                    "error_code": "DATASET_INVALID_TEXT",
                    "message": f"field '{args.text_field}' must be a non-blank string",
                }
            )
            continue
        source_id = str(record.get(args.id_field) or f"dataset-{sequence:08d}")
        tenant_id = str(record.get(args.tenant_field) or args.tenant_id)
        source_type = str(record.get(args.source_type_field) or args.source_type)
        input_type = str(record.get(args.input_type_field) or args.input_type)
        field_error = None
        if len(source_id) > 256:
            field_error = f"field '{args.id_field}' exceeds 256 characters"
        elif not tenant_id.strip() or len(tenant_id) > 128:
            field_error = "tenant must contain 1 to 128 characters"
        elif not source_type.strip() or len(source_type) > 64:
            field_error = "source type must contain 1 to 64 characters"
        elif input_type not in {"passage", "query"}:
            field_error = f"input type must be passage or query, got '{input_type}'"
        if field_error is not None:
            local_errors.append(
                {
                    "row": row_number,
                    "error_code": "DATASET_INVALID_FIELD",
                    "message": field_error,
                }
            )
            continue
        requests.append(
            {
                "request_id": f"dataset-{args.run_id[:32]}-{sequence:08d}",
                "tenant_id": tenant_id,
                "source_type": source_type,
                "source_id": source_id,
                "text": text_value,
                "embedding_required": True,
                "input_type": input_type,
                "metadata": {
                    "dataset_file": args.input.name,
                    "dataset_row": row_number,
                    "expected": record.get("expected"),
                },
            }
        )

    started = time.perf_counter()
    results: list[dict[str, Any]] = []
    server_metrics: dict[str, Any] = {}
    with httpx.Client(base_url=args.base_url.rstrip("/"), timeout=args.timeout) as client:
        ready = client.get("/health/ready")
        ready.raise_for_status()
        for batch_number, batch in enumerate(_chunks(requests, args.batch_size), 1):
            response = client.post("/v1/intercept", json={"items": batch})
            response.raise_for_status()
            body = response.json()
            batch_results = body.get("results")
            if not isinstance(batch_results, list):
                raise RuntimeError("Sidecar response does not contain a results array")
            results.extend(
                _compact_result(result, args.include_vectors) for result in batch_results
            )
            server_metrics = client.get("/metrics").json()
            processed = min(batch_number * args.batch_size, len(requests))
            print(f"[{processed}/{len(requests)}] {_metric_line(server_metrics)}")

    elapsed = time.perf_counter() - started
    status_counts = {
        status: sum(result.get("status") == status for result in results)
        for status in ("success", "skipped", "failed")
    }
    summary = {
        "dataset": {
            "path": str(args.input.resolve()),
            "format": dataset_format,
            "text_field": args.text_field,
            "id_field": args.id_field,
            "input_type_field": args.input_type_field,
            "records_sent": len(requests),
            "local_validation_failures": len(local_errors),
        },
        "sidecar": ready.json(),
        "status_counts": status_counts,
        "elapsed_seconds": round(elapsed, 6),
        "items_per_second_client": round(len(results) / elapsed if elapsed else math.inf, 3),
        "vectors_returned": sum(int(result.get("vector_count", 0)) for result in results),
        "chunks_returned": sum(int(result.get("chunk_count", 0)) for result in results),
        "input_chars": sum(int(result.get("input_chars", 0)) for result in results),
        "server_metrics": server_metrics,
        "local_errors": local_errors,
        "results": results,
    }
    rendered = json.dumps(summary, ensure_ascii=False, indent=2)
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered)


if __name__ == "__main__":
    main()
