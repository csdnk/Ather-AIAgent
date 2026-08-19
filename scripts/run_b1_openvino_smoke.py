"""Start the B1 Sidecar, send an intercept sample, and print its vector."""

from __future__ import annotations

import argparse
import json
import math
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any

try:
    import httpx
except ImportError as exc:  # pragma: no cover - depends on the runtime environment
    raise SystemExit(
        "httpx is required; install the project dependencies before running this smoke test."
    ) from exc


def _validate_model_path(model_path: Path) -> None:
    if not model_path.is_dir():
        raise ValueError(f"model path is not a directory: {model_path}")
    xml_files = sorted(model_path.glob("*.xml"))
    if not xml_files:
        raise ValueError(f"no OpenVINO .xml file found in: {model_path}")
    for xml_file in xml_files:
        if xml_file.with_suffix(".bin").is_file():
            break
    else:
        raise ValueError(f"no .bin weights file matches an OpenVINO .xml file in: {model_path}")
    if not (model_path / "tokenizer.json").is_file():
        raise ValueError(f"tokenizer.json is missing from: {model_path}")


def _sample_payload() -> dict[str, Any]:
    request_id = f"openvino-smoke-{uuid.uuid4().hex[:12]}"
    return {
        "items": [
            {
                "request_id": request_id,
                "trace_id": uuid.uuid4().hex,
                "tenant_id": "openvino-smoke",
                "source_type": "rag_document",
                "source_id": "openvino-smoke-document",
                "object_id": "openvino-smoke-object",
                "chunk_id": "openvino-smoke-chunk-0001",
                "embedding_required": True,
                "input_type": "passage",
                "chunk_text": (
                    "这是一个 B1 Sidecar 拦截样例，验证 OpenVINO 本地模型能够返回真实向量。"
                ),
                "metadata": {"scenario": "openvino-local-smoke"},
            }
        ]
    }


def _wait_ready(
    client: httpx.Client,
    process: subprocess.Popen[Any],
    timeout: float,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    last_error = "no readiness response"
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"Sidecar exited before readiness: exit_code={process.returncode}")
        try:
            response = client.get("/health/ready")
            payload = response.json()
            if response.status_code == 200 and payload.get("status") == "ready":
                return payload
            last_error = f"HTTP {response.status_code}: {payload}"
        except (httpx.HTTPError, ValueError) as exc:
            last_error = str(exc)
        time.sleep(0.25)
    raise TimeoutError(f"Sidecar did not become ready within {timeout:g}s: {last_error}")


def _extract_vector(
    body: Any,
    expected_backend: str,
    expected_dimension: int,
) -> tuple[dict[str, Any], list[float]]:
    if not isinstance(body, dict) or not isinstance(body.get("results"), list):
        raise AssertionError(f"invalid Sidecar response: {body}")
    results = body["results"]
    if len(results) != 1 or not isinstance(results[0], dict):
        raise AssertionError(f"expected one result: {body}")
    result = results[0]
    if result.get("status") != "success":
        raise AssertionError(
            f"intercept failed: {result.get('error_code')}: {result.get('error_message')}"
        )
    if result.get("backend") != expected_backend:
        raise AssertionError(f"unexpected backend: {result.get('backend')!r}")
    chunks = result.get("chunks")
    if not isinstance(chunks, list) or len(chunks) != 1 or not isinstance(chunks[0], dict):
        raise AssertionError("expected exactly one embedded chunk")
    vector = chunks[0].get("vector")
    if not isinstance(vector, list) or not vector:
        raise AssertionError("Sidecar returned no vector")
    if len(vector) != expected_dimension:
        raise AssertionError(
            f"vector dimension mismatch: got {len(vector)}, expected {expected_dimension}"
        )
    if not all(isinstance(value, (int, float)) and math.isfinite(float(value)) for value in vector):
        raise AssertionError("vector contains a non-finite value")
    norm = math.sqrt(math.fsum(float(value) ** 2 for value in vector))
    if not math.isclose(norm, 1.0, rel_tol=0.0, abs_tol=1e-3):
        raise AssertionError(f"vector is not normalized: L2 norm={norm:.8f}")
    return result, [float(value) for value in vector]


def run(args: argparse.Namespace) -> dict[str, Any]:
    model_path = args.model_path.resolve()
    _validate_model_path(model_path)
    host = "127.0.0.1"
    base_url = f"http://{host}:{args.port}"
    environment = os.environ.copy()
    environment.update(
        {
            "AETHER_B1_HOST": host,
            "AETHER_B1_PORT": str(args.port),
            "AETHER_B1_BACKEND": "openvino",
            "AETHER_B1_PRECISION": args.precision,
            "AETHER_B1_FALLBACK_BACKENDS": "",
            "AETHER_B1_ALLOW_BACKEND_FALLBACK": "false",
            "AETHER_B1_MODEL_NAME": args.model_name or model_path.name,
            "AETHER_B1_MODEL_PATH": str(model_path),
            "AETHER_B1_EAGER_LOAD": "true",
            "AETHER_B1_FAIL_MODE": "closed",
            "AETHER_B1_THREADS": str(args.threads),
            "AETHER_B1_INTER_OP_THREADS": str(args.threads),
            "AETHER_B1_MODEL_BATCH_SIZE": "8",
            "AETHER_B1_MAX_CONCURRENCY": "1",
            "PYTHONUTF8": "1",
        }
    )
    command = [args.python, "-m", "aether_agent_memory.b1.sidecar"]
    process = subprocess.Popen(
        command,
        cwd=Path(__file__).resolve().parents[1],
        env=environment,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        with httpx.Client(base_url=base_url, timeout=args.timeout) as client:
            health = _wait_ready(client, process, args.startup_timeout)
            expected_dimension = args.expected_dimension or health.get("dimension")
            if not isinstance(expected_dimension, int) or expected_dimension <= 0:
                raise AssertionError(f"invalid readiness dimension: {health.get('dimension')!r}")
            response = client.post("/v1/intercept", json=_sample_payload())
            body = response.json()
            if response.status_code != 200:
                raise AssertionError(f"intercept HTTP {response.status_code}: {body}")
            result, vector = _extract_vector(body, "openvino", expected_dimension)
            output: dict[str, Any] = {
                "status": "passed",
                "service": {
                    "base_url": base_url,
                    "backend": health.get("backend"),
                    "engine": health.get("engine"),
                    "model": health.get("model"),
                    "dimension": expected_dimension,
                },
                "intercept": {
                    "status": result.get("status"),
                    "request_id": result.get("request_id"),
                    "backend": result.get("backend"),
                    "embedding_model": result.get("embedding_model"),
                    "embedding_dim": result.get("embedding_dim"),
                    "normalized": result.get("normalized"),
                    "l2_norm": math.sqrt(math.fsum(value * value for value in vector)),
                    "vector": vector if not args.summary_only else vector[:8],
                    "vector_truncated": args.summary_only,
                },
            }
            return output
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Start a minimal OpenVINO B1 Sidecar and return an intercept vector."
    )
    parser.add_argument("--model-path", type=Path, required=True)
    parser.add_argument("--model-name")
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument("--port", type=int, default=18081)
    parser.add_argument("--threads", type=int, default=1)
    parser.add_argument("--precision", choices=("fp32", "int8"), default="fp32")
    parser.add_argument("--expected-dimension", type=int)
    parser.add_argument("--startup-timeout", type=float, default=120.0)
    parser.add_argument("--timeout", type=float, default=120.0)
    parser.add_argument(
        "--summary-only",
        action="store_true",
        help="print only the first eight vector values instead of the full vector",
    )
    args = parser.parse_args()
    if args.port < 1 or args.port > 65535:
        parser.error("--port must be between 1 and 65535")
    if args.threads <= 0 or args.startup_timeout <= 0 or args.timeout <= 0:
        parser.error("threads and timeouts must be positive")
    try:
        print(json.dumps(run(args), ensure_ascii=False, indent=2))
    except (AssertionError, OSError, TimeoutError, ValueError, httpx.HTTPError) as exc:
        print(json.dumps({"status": "failed", "error": str(exc)}, ensure_ascii=False, indent=2))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
