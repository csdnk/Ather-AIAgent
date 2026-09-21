"""Standalone long-text fault checks; no live broker or providers are used."""

from __future__ import annotations

import asyncio

import pytest

celery_app = pytest.importorskip("aether_agent_memory.b2.celery_app")

from aether_agent_memory.b2.b1_client import B1EmbeddingUnavailableError  # noqa: E402
from aether_agent_memory.b2.compression import HybridMemoryCompressor  # noqa: E402
from aether_agent_memory.memory.projection import MemoryProjectionReconciler  # noqa: E402
from aether_agent_memory.persistence import SQLiteMemoryStore  # noqa: E402


@pytest.fixture
def long_text_pipeline(tmp_path, monkeypatch):
    path = tmp_path / "long-text.db"
    payloads = []

    class StatusStore:
        def __init__(self, url):
            pass

        def set(self, task_id, state, **details):
            return {"task_id": task_id, "state": state.value, **details}

    monkeypatch.setattr(celery_app, "_memory_store", lambda: SQLiteMemoryStore(path))
    monkeypatch.setattr(celery_app, "RedisTaskStatusStore", StatusStore)
    monkeypatch.setattr(celery_app.process_long_text, "delay", payloads.append)
    return path, payloads


def _submit():
    return celery_app.submit_long_text(
        text="Remember the deployment decision. " * 20,
        tenant_id="tenant",
        user_id="user",
        agent_id="agent",
        session_id="session",
        source_id="source",
        request_id="request",
        trace_id="trace",
    )


def _read(path, memory_id):
    return asyncio.run(SQLiteMemoryStore(path).get(memory_id))


@pytest.mark.parametrize("broker_fails", [False, True])
def test_long_text_fact_exists_before_broker_call(long_text_pipeline, monkeypatch, broker_fails):
    path, payloads = long_text_pipeline

    def enqueue(payload):
        payloads.append(payload)
        fact = _read(path, payload["memory_id"])
        assert fact is not None and fact.content == payload["text"]
        assert fact.embedding_status == fact.vector_projection_status == "pending"
        if broker_fails:
            raise ConnectionError("broker unavailable")

    monkeypatch.setattr(celery_app.process_long_text, "delay", enqueue)
    if broker_fails:
        with pytest.raises(ConnectionError, match="broker unavailable"):
            _submit()
    else:
        _submit()
    fact = _read(path, payloads[0]["memory_id"])
    assert fact is not None
    assert fact.embedding_status != "succeeded"
    assert fact.vector_projection_status != "succeeded"
    assert asyncio.run(MemoryProjectionReconciler(SQLiteMemoryStore(path)).plan_for(fact))


def test_long_text_b1_failure_preserves_fact_and_worker_can_recover(
    long_text_pipeline, monkeypatch
):
    path, payloads = long_text_pipeline
    result = _submit()
    payload = payloads[0]
    available = False
    projected = []

    class B1Client:
        def __init__(self, endpoint):
            pass

        def process(self, request):
            fact = _read(path, payload["memory_id"])
            assert fact is not None and fact.content == payload["text"]
            if not available:
                raise B1EmbeddingUnavailableError("B1 unavailable")
            return {
                "request_id": request["request_id"],
                "trace_id": request["trace_id"],
                "source_id": request["source_id"],
                "records": [
                    {
                        "chunk_id": "chunk",
                        "chunk_text": request["text"],
                        "vector": [0.25, 0.75],
                        "embedding_model": "standalone-test",
                    }
                ],
            }

    def compress(source):
        return HybridMemoryCompressor().compress(
            source["text"],
            source_memory_id=source["memory_id"],
        )

    async def project(records, *, collection):
        projected.extend(records)

    monkeypatch.setattr(celery_app, "B1EmbeddingServiceClient", B1Client)
    monkeypatch.setattr(celery_app, "_compress_payload", compress)
    monkeypatch.setattr(celery_app, "_upsert_p2_vectors", project)
    monkeypatch.setattr(celery_app, "MILVUS_PROJECTION", False)
    monkeypatch.setattr(celery_app.process_long_text, "max_retries", 0)
    with pytest.raises(B1EmbeddingUnavailableError):
        celery_app.process_long_text.run(payload)
    fact = _read(path, result["memory_id"])
    assert fact is not None
    assert fact.embedding_status == fact.vector_projection_status == "failed"
    assert projected == []

    available = True
    celery_app.process_long_text.run(payload)
    fact = _read(path, result["memory_id"])
    assert fact.content == payload["text"]
    assert fact.embedding_status == fact.vector_projection_status == "succeeded"
    assert len(projected) == 1
