"""Verify the original B1 Sidecar embedding contract with OpenVINO."""

from __future__ import annotations

import argparse
import json
import math
import sys
import uuid
from typing import Any

try:
    import httpx
except ImportError as exc:  # pragma: no cover - depends on the runtime environment
    raise SystemExit(
        "httpx is required to test the Sidecar HTTP endpoint; install the project dependencies."
    ) from exc


def _request_item(*, input_type: str, index: int) -> dict[str, Any]:
    request_id = f"openvino-basic-{uuid.uuid4().hex[:12]}-{index}"
    return {
        "request_id": request_id,
        "trace_id": uuid.uuid4().hex,
        "tenant_id": "openvino-basic-test",
        "source_type": "b1_basic_test",
        "source_id": f"openvino-basic-source-{index}",
        "object_id": f"openvino-basic-object-{index}",
        "chunk_id": f"openvino-basic-chunk-{index}",
        "embedding_required": True,
        "input_type": input_type,
        "chunk_text": (
            "OpenVINO B1 basic contract test: the Sidecar must return a real normalized vector."
        ),
        "metadata": {"test": "openvino-basic", "case": input_type},
    }


def _check_result(
    result: Any,
    *,
    expected_backend: str,
    expected_dimension: int,
) -> dict[str, Any]:
    if not isinstance(result, dict):
        raise AssertionError("Sidecar result is not a JSON object")
    if result.get("status") != "success":
        raise AssertionError(
            f"embedding failed: {result.get('error_code')}: {result.get('error_message')}"
        )
    if result.get("backend") != expected_backend:
        raise AssertionError(
            f"unexpected backend: {result.get('backend')!r}; expected {expected_backend!r}"
        )
    if result.get("requested_backend") != expected_backend:
        raise AssertionError(
            f"unexpected requested backend: {result.get('requested_backend')!r}"
        )
    if result.get("normalized") is not True:
        raise AssertionError("Sidecar did not mark the vector as normalized")
    if result.get("chunk_count") != 1 or not isinstance(result.get("chunks"), list):
        raise AssertionError("basic test text did not produce exactly one chunk")
    chunks = result["chunks"]
    if len(chunks) != 1 or not isinstance(chunks[0], dict):
        raise AssertionError("Sidecar returned an invalid chunk list")
    vector = chunks[0].get("vector")
    if not isinstance(vector, list) or not vector:
        raise AssertionError("Sidecar returned an empty vector")
    if len(vector) != expected_dimension:
        raise AssertionError(
            f"vector dimension mismatch: got {len(vector)}, expected {expected_dimension}"
        )
    if not all(isinstance(value, (int, float)) and math.isfinite(float(value)) for value in vector):
        raise AssertionError("vector contains a non-finite value")
    norm = math.sqrt(math.fsum(float(value) ** 2 for value in vector))
    if not math.isclose(norm, 1.0, rel_tol=0.0, abs_tol=1e-3):
        raise AssertionError(f"vector is not L2-normalized: norm={norm:.8f}")
    metadata = chunks[0].get("metadata")
    if not isinstance(metadata, dict) or metadata.get("embedding_backend") != expected_backend:
        raise AssertionError("chunk metadata does not report the expected backend")
    return {
        "request_id": result.get("request_id"),
        "backend": result.get("backend"),
        "model": result.get("embedding_model"),
        "dimension": len(vector),
        "norm": norm,
        "latency_ms": result.get("latency_ms"),
        "vector_sample": [float(value) for value in vector[:8]],
    }


def run(args: argparse.Namespace) -> dict[str, Any]:
    base_url = args.base_url.rstrip("/")
    expected_backend = args.expected_backend.lower()
    with httpx.Client(base_url=base_url, timeout=args.timeout) as client:
        health_response = client.get("/health/ready")
        try:
            health = health_response.json()
        except ValueError as exc:
            raise AssertionError(
                f"/health/ready returned non-JSON HTTP {health_response.status_code}"
            ) from exc
        if health_response.status_code != 200 or health.get("status") != "ready":
            raise AssertionError(
                f"Sidecar is not ready: HTTP {health_response.status_code}: {health}"
            )
        if health.get("backend") != expected_backend:
            raise AssertionError(
                f"readiness backend is {health.get('backend')!r}; expected {expected_backend!r}"
            )
        dimension = health.get("dimension")
        if not isinstance(dimension, int) or dimension <= 0:
            raise AssertionError(f"readiness returned an invalid dimension: {dimension!r}")
        if args.expected_dimension is not None and dimension != args.expected_dimension:
            raise AssertionError(
                f"readiness dimension is {dimension}; expected {args.expected_dimension}"
            )
        expected_dimension = args.expected_dimension or dimension

        cases = [
            ("passage", [_request_item(input_type="passage", index=1)]),
            ("query", [_request_item(input_type="query", index=2)]),
            (
                "batch",
                [
                    _request_item(input_type="passage", index=3),
                    _request_item(input_type="query", index=4),
                ],
            ),
        ]
        results: dict[str, Any] = {}
        for case_name, items in cases:
            response = client.post("/v1/intercept", json={"items": items})
            try:
                body = response.json()
            except ValueError as exc:
                raise AssertionError(
                    f"/v1/intercept returned non-JSON HTTP {response.status_code}"
                ) from exc
            if response.status_code != 200:
                raise AssertionError(
                    f"/v1/intercept failed: HTTP {response.status_code}: {body}"
                )
            returned = body.get("results") if isinstance(body, dict) else None
            if not isinstance(returned, list) or len(returned) != len(items):
                raise AssertionError(f"unexpected result count for {case_name}: {body}")
            results[case_name] = [
                _check_result(
                    result,
                    expected_backend=expected_backend,
                    expected_dimension=expected_dimension,
                )
                for result in returned
            ]

    return {
        "status": "passed",
        "base_url": base_url,
        "backend": health.get("backend"),
        "engine": health.get("engine"),
        "model": health.get("model"),
        "dimension": expected_dimension,
        "cases": results,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Verify the B1 Sidecar HTTP embedding contract using OpenVINO."
    )
    parser.add_argument("--base-url", default="http://127.0.0.1:18081")
    parser.add_argument("--expected-backend", default="openvino")
    parser.add_argument("--expected-dimension", type=int)
    parser.add_argument("--timeout", type=float, default=120.0)
    args = parser.parse_args()
    if args.timeout <= 0:
        parser.error("--timeout must be positive")
    try:
        summary = run(args)
    except (AssertionError, httpx.HTTPError) as exc:
        print(json.dumps({"status": "failed", "error": str(exc)}, ensure_ascii=False, indent=2))
        return 1
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
