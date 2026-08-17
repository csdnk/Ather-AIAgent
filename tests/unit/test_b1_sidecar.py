from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from fastapi.testclient import TestClient

from aether_agent_memory.b1.backends import BackendConfig, BackendUnavailableError, create_backend
from aether_agent_memory.b1.sidecar import B1Service, SidecarSettings, create_app


class FakeBackend:
    backend_key = "onnx"
    engine_name = "fake-onnx-cpu"
    model_name = "fake-bge"

    def __init__(
        self,
        *,
        fail: bool = False,
        delay: float = 0.0,
        invalid_vector: bool = False,
    ) -> None:
        self.dimension: int | None = 4
        self.fail = fail
        self.delay = delay
        self.invalid_vector = invalid_vector
        self.calls = 0

    def load(self) -> None:
        return None

    def embed(
        self,
        texts: list[str],
        input_types: list[str],
        batch_size: int,
    ) -> list[np.ndarray]:
        self.calls += 1
        if self.delay:
            time.sleep(self.delay)
        if self.fail:
            raise RuntimeError("injected backend failure")
        if self.invalid_vector:
            return [np.array([np.nan], dtype=np.float32) for _ in texts]
        return [
            np.array([len(text), len(input_type), batch_size, 1.0], dtype=np.float32)
            for text, input_type in zip(texts, input_types, strict=True)
        ]

    def runtime_details(self) -> dict[str, Any]:
        return {
            "backend_key": self.backend_key,
            "engine": self.engine_name,
            "provider": "CPUExecutionProvider",
        }


def settings(**overrides: object) -> SidecarSettings:
    values: dict[str, object] = {
        "cache_dir": Path("unused"),
        "chunk_max_chars": 12,
        "chunk_overlap_chars": 2,
        "max_input_chars": 100,
        "max_chunks_per_item": 20,
        "max_metadata_bytes": 64,
        "backend_timeout_seconds": 1.0,
        "queue_timeout_seconds": 0.05,
        "eager_load": True,
    }
    values.update(overrides)
    return SidecarSettings(**values)  # type: ignore[arg-type]


def item(**overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "request_id": "req-1",
        "tenant_id": "tenant-a",
        "source_type": "rag_document",
        "source_id": "doc-1",
        "chunk_id": "chunk-1",
        "chunk_text": "sidecar text",
        "embedding_required": True,
        "metadata": {},
    }
    value.update(overrides)
    return value


@pytest.mark.unit
def test_returns_normalized_vector_metrics_events_and_capabilities() -> None:
    with TestClient(create_app(settings(), FakeBackend())) as client:
        response = client.post("/v1/intercept", json=item())
        metrics = client.get("/metrics").json()
        events = client.get("/v1/events").json()["items"]
        capabilities = client.get("/v1/capabilities").json()

    assert response.status_code == 200
    result = response.json()["results"][0]
    assert result["status"] == "success"
    assert len(result["vector"]) == 4
    assert np.linalg.norm(result["vector"]) == pytest.approx(1.0, abs=1e-6)
    assert result["trace_id"]
    assert metrics["requests"] == 1
    assert metrics["total_vectors"] == 1
    assert events[0]["stages"] == [
        "intercepted",
        "validated",
        "chunked",
        "embedded",
        "normalized",
        "returned",
    ]
    assert capabilities["backend_strategy"]["selected"] == "onnx"
    assert capabilities["simd_kernel_strategy"]["reserved"] == [
        "explicit-avx512",
        "amx-bf16",
        "amx-int8",
    ]


@pytest.mark.unit
def test_batch_supports_partial_failure_skip_and_query_input() -> None:
    body = {
        "items": [
            item(request_id="ok", input_type="query"),
            item(request_id="skip", embedding_required=False),
            {"request_id": "bad"},
        ]
    }
    with TestClient(create_app(settings(), FakeBackend())) as client:
        response = client.post("/v1/intercept", json=body)

    assert response.status_code == 200
    assert response.json()["overall_status"] == "partial"
    assert [value["status"] for value in response.json()["results"]] == [
        "success",
        "skipped",
        "failed",
    ]


@pytest.mark.unit
def test_long_text_preserves_offsets_and_exact_substrings() -> None:
    text = "  First part. Second part. Third part.  "
    with TestClient(create_app(settings(max_input_chars=200), FakeBackend())) as client:
        result = client.post(
            "/v1/intercept",
            json=item(request_id="long", chunk_text=text),
        ).json()["results"][0]

    assert result["status"] == "success"
    assert len(result["chunks"]) >= 3
    assert "vector" not in result
    assert result["chunks"][0]["start_char"] == 2
    assert result["chunks"][-1]["end_char"] == len(text) - 2
    for chunk in result["chunks"]:
        expected = text[chunk["start_char"] : chunk["end_char"]]
        assert expected.strip()
        assert chunk["chunk_text"] == expected
        assert chunk["chunk_id"].startswith("chunk-1:")


@pytest.mark.unit
def test_short_boundary_maximum_and_over_limit_texts() -> None:
    body = {
        "items": [
            item(request_id="one", chunk_text="短"),
            item(request_id="hard-boundaries", chunk_text="x" * 100),
            item(request_id="unicode", chunk_text="中文😀English。" * 8),
            item(request_id="blank", chunk_text=" \t\r\n "),
            item(request_id="over-limit", chunk_text="z" * 101),
        ]
    }
    with TestClient(create_app(settings(), FakeBackend())) as client:
        results = client.post("/v1/intercept", json=body).json()["results"]

    assert results[0]["status"] == "success"
    assert results[0]["chunk_count"] == 1
    assert results[1]["status"] == "success"
    assert results[1]["chunks"][0]["start_char"] == 0
    assert results[1]["chunks"][-1]["end_char"] == 100
    assert results[1]["chunk_count"] > 1
    assert results[2]["status"] == "success"
    assert results[3]["error_code"] == "B1_EMPTY_TEXT"
    assert results[4]["error_code"] == "B1_TEXT_TOO_LONG"


@pytest.mark.unit
def test_chunk_count_limit_is_enforced() -> None:
    with TestClient(create_app(settings(max_chunks_per_item=2), FakeBackend())) as client:
        result = client.post(
            "/v1/intercept",
            json=item(request_id="too-many-chunks", chunk_text="x" * 100),
        ).json()["results"][0]

    assert result["error_code"] == "B1_TOO_MANY_CHUNKS"


@pytest.mark.unit
def test_rejects_mixed_payload_limits_and_invalid_fields() -> None:
    config = settings(max_batch_items=1, max_body_bytes=1024)
    with TestClient(create_app(config, FakeBackend())) as client:
        mixed = client.post("/v1/intercept", json={"items": [item()], "other": True})
        batch = client.post(
            "/v1/intercept",
            json={"items": [item(), item(request_id="req-2")]},
        )
        long_text = client.post("/v1/intercept", json=item(chunk_text="x" * 101))
        metadata = client.post(
            "/v1/intercept",
            json=item(request_id="metadata", metadata={"value": "x" * 100}),
        )
        both_text_fields = client.post(
            "/v1/intercept",
            json=item(request_id="both", text="text", chunk_text="chunk"),
        )
        body = client.post(
            "/v1/intercept",
            content=b"x" * 1025,
            headers={"content-type": "application/json"},
        )

    assert mixed.status_code == 422
    assert batch.status_code == 413
    assert long_text.json()["results"][0]["error_code"] == "B1_TEXT_TOO_LONG"
    assert metadata.json()["results"][0]["error_code"] == "B1_METADATA_TOO_LARGE"
    assert both_text_fields.json()["results"][0]["error_code"] == "B1_INVALID_REQUEST"
    assert body.status_code == 413


@pytest.mark.unit
def test_completed_and_same_batch_idempotency_and_conflict() -> None:
    app = create_app(settings(), FakeBackend())
    with TestClient(app) as client:
        first = client.post("/v1/intercept", json=item()).json()["results"][0]
        replay = client.post("/v1/intercept", json=item()).json()["results"][0]
        batch = client.post(
            "/v1/intercept",
            json={"items": [item(request_id="batch"), item(request_id="batch")]},
        ).json()["results"]
        conflict = client.post(
            "/v1/intercept",
            json=item(chunk_text="different payload"),
        ).json()["results"][0]

    assert first["status"] == "success"
    assert replay["idempotent_replay"] is True
    assert batch[0]["status"] == "success"
    assert batch[1]["idempotent_replay"] is True
    assert conflict["error_code"] == "B1_IDEMPOTENCY_CONFLICT"


@pytest.mark.unit
async def test_concurrent_idempotent_requests_execute_backend_once() -> None:
    backend = FakeBackend(delay=0.05)
    service = B1Service(settings(eager_load=False), backend)
    service.load()

    (first, first_status), (second, second_status) = await asyncio.gather(
        service.process(item()),
        service.process(item()),
    )

    assert first_status == second_status == 200
    assert backend.calls == 1
    replays = [
        first["results"][0].get("idempotent_replay", False),
        second["results"][0].get("idempotent_replay", False),
    ]
    assert sorted(replays) == [False, True]


@pytest.mark.unit
async def test_queue_and_backend_timeouts_have_distinct_errors() -> None:
    queue_service = B1Service(settings(eager_load=False), FakeBackend())
    queue_service.load()
    await queue_service._semaphore.acquire()
    try:
        queue_body, queue_status = await queue_service.process(item(request_id="queue"))
    finally:
        queue_service._semaphore.release()

    timeout_backend = FakeBackend(delay=0.1)
    timeout_service = B1Service(
        settings(eager_load=False, backend_timeout_seconds=0.01),
        timeout_backend,
    )
    timeout_service.load()
    timeout_body, timeout_status = await timeout_service.process(item(request_id="timeout"))

    assert queue_status == timeout_status == 200
    assert queue_body["results"][0]["error_code"] == "B1_BUSY"
    assert timeout_body["results"][0]["error_code"] == "B1_EMBEDDING_TIMEOUT"
    assert timeout_service.metrics.backend_timeouts == 1
    await asyncio.sleep(0.11)


@pytest.mark.unit
@pytest.mark.parametrize("fail_mode, expected_status", [("open", 200), ("closed", 503)])
def test_backend_failure_and_invalid_vector_obey_fail_mode(
    fail_mode: str,
    expected_status: int,
) -> None:
    with TestClient(
        create_app(settings(fail_mode=fail_mode), FakeBackend(invalid_vector=True))
    ) as client:
        response = client.post("/v1/intercept", json=item())

    assert response.status_code == expected_status
    assert response.json()["results"][0]["error_code"] == "B1_EMBEDDING_BACKEND_ERROR"


@pytest.mark.unit
def test_reserved_backend_is_explicitly_unavailable() -> None:
    config = BackendConfig(
        model_name="unused",
        cache_dir=Path("unused"),
        model_path=None,
        threads=1,
    )
    with pytest.raises(BackendUnavailableError, match="reserved-not-implemented"):
        create_backend("openvino", config)
    with pytest.raises(BackendUnavailableError, match="unknown backend"):
        create_backend("not-a-backend", config)


@pytest.mark.unit
def test_settings_reject_non_positive_limits_and_timeouts() -> None:
    with pytest.raises(ValueError, match="backend_timeout_seconds"):
        settings(backend_timeout_seconds=0)
    with pytest.raises(ValueError, match="max_batch_items"):
        settings(max_batch_items=0)
    with pytest.raises(ValueError, match="CHUNK_OVERLAP"):
        settings(chunk_overlap_chars=-1)
