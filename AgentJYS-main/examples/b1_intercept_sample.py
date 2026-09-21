from __future__ import annotations

import argparse
import json
import math
import uuid

import httpx


def main() -> None:
    parser = argparse.ArgumentParser(description="Call the real B1 Sidecar interception endpoint")
    parser.add_argument("--url", default="http://127.0.0.1:18081/v1/intercept")
    parser.add_argument("--show-vector", action="store_true")
    args = parser.parse_args()
    request_id = f"sample-{uuid.uuid4().hex[:12]}"
    payload = {
        "request_id": request_id,
        "trace_id": uuid.uuid4().hex,
        "tenant_id": "tenant-demo",
        "source_type": "rag_document",
        "source_id": "doc-sidecar-001",
        "object_id": "object-sidecar-001",
        "chunk_id": "chunk-0001",
        "embedding_required": True,
        "input_type": "passage",
        "chunk_text": (
            "B1 Sidecar intercepts a write-path text chunk and returns a real CPU embedding."
        ),
        "metadata": {"namespace": "b1-demo", "intercept_mode": "application_hook"},
    }

    with httpx.Client(timeout=120.0) as client:
        response = client.post(args.url, json=payload)
        response.raise_for_status()
        body = response.json()

    result = body["results"][0]
    vector = result.get("vector")
    if result.get("status") != "success" or not isinstance(vector, list) or not vector:
        raise RuntimeError(f"Sidecar did not return a vector: {body}")
    if len(vector) != result.get("embedding_dim") or not all(
        math.isfinite(value) for value in vector
    ):
        raise RuntimeError("Sidecar returned an invalid vector")

    summary = {
        "http_status": response.status_code,
        "request_id": result["request_id"],
        "trace_id": result["trace_id"],
        "status": result["status"],
        "backend": result["backend"],
        "model": result["embedding_model"],
        "dimension": result["embedding_dim"],
        "normalized": result["normalized"],
        "l2_norm": math.sqrt(sum(value * value for value in vector)),
        "first_8_values": vector[:8],
        "vector": vector if args.show_vector else "omitted; use --show-vector for all values",
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
