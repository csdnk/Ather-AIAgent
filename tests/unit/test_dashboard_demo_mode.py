from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).parents[2]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))
SPEC = importlib.util.spec_from_file_location("p3_service_test", SCRIPTS / "p3_service.py")
assert SPEC is not None and SPEC.loader is not None
p3_service = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(p3_service)


class _TaskStatusStore:
    def __init__(self, records: list[dict[str, Any]]) -> None:
        self.records = records
        self.index = 0

    def get(self, _task_id: str) -> dict[str, Any]:
        record = self.records[min(self.index, len(self.records) - 1)]
        self.index += 1
        return record


def test_demo_mode_is_opt_in(monkeypatch) -> None:
    monkeypatch.delenv("AETHER_ENABLE_DEMO", raising=False)
    assert p3_service.demo_enabled() is False
    monkeypatch.setenv("AETHER_ENABLE_DEMO", "true")
    assert p3_service.demo_enabled() is True


def test_demo_run_records_full_flow_and_schedule() -> None:
    p3_service.STATE["flow_history"] = []
    p3_service.STATE["schedule_history"] = []
    report = {
        "request_id": "request-1",
        "trace_id": "trace-1",
        "events": [{"step": 1, "component": "P2 E2 ObjectService"}],
        "action": {
            "action_id": "action-1",
            "request_id": "request-1",
            "trace_id": "trace-1",
            "object_id": "uploads/example.txt",
            "action_type": "prefetch",
            "source_tier": "L3",
            "target_tier": "L2",
            "priority": 92,
            "score": 0.92,
            "score_frequency": 1.0,
            "score_semantic": 0.9,
            "score_decay": 1.0,
            "score_cost": 0.5,
            "reason": "high relevance",
            "policy_version": "heuristic-v1",
        },
        "feedback": {
            "timestamp": "2026-07-20T00:00:00+00:00",
            "execute_status": "success",
            "execute_latency_ms": 1.0,
        },
    }

    p3_service.record_demo_run("knowledge-session", report)

    assert p3_service.STATE["flow_history"][0]["trace_id"] == "trace-1"
    schedule = p3_service.STATE["schedule_history"][0]
    assert schedule["source"] == "knowledge-session"
    assert schedule["action_type"] == "prefetch"
    assert schedule["target_tier"] == "L2"


async def test_wait_for_b2_task_tracks_redis_state_progression() -> None:
    final, transitions = await p3_service.wait_for_b2_task(
        "task-1",
        status_store=_TaskStatusStore(
            [
                {"task_id": "task-1", "state": "PENDING"},
                {"task_id": "task-1", "state": "PROCESSING"},
                {
                    "task_id": "task-1",
                    "state": "SUCCEEDED",
                    "chunk_count": 3,
                    "p2_vector_count": 3,
                },
            ]
        ),
        timeout_seconds=1,
        poll_interval_seconds=0.001,
    )

    assert transitions == ["PENDING", "PROCESSING", "SUCCEEDED"]
    assert final["chunk_count"] == 3


async def test_wait_for_b2_task_reports_worker_failure() -> None:
    with pytest.raises(RuntimeError, match="CeleryError: worker failed"):
        await p3_service.wait_for_b2_task(
            "task-failed",
            status_store=_TaskStatusStore(
                [
                    {
                        "task_id": "task-failed",
                        "state": "FAILED",
                        "error": "CeleryError: worker failed",
                    }
                ]
            ),
            timeout_seconds=1,
            poll_interval_seconds=0.001,
        )


async def test_wait_for_b2_task_has_bounded_timeout(monkeypatch) -> None:
    ticks = iter([0.0, 1.0])
    monkeypatch.setattr(p3_service, "monotonic", lambda: next(ticks))

    with pytest.raises(TimeoutError, match="last state: PENDING"):
        await p3_service.wait_for_b2_task(
            "task-timeout",
            status_store=_TaskStatusStore(
                [{"task_id": "task-timeout", "state": "PENDING"}]
            ),
            timeout_seconds=0.5,
            poll_interval_seconds=0.001,
        )


async def test_full_test_report_exposes_b1_sidecar_and_b2_celery(monkeypatch) -> None:
    class _Client:
        bucket = "p3-memory"

    class _Runtime:
        client = _Client()

        async def run_knowledge_session(self, **_kwargs: Any) -> dict[str, Any]:
            return {
                "request_id": "request-1",
                "trace_id": "trace-1",
                "object_key": "uploads/example.txt",
                "events": [
                    {
                        "step": 1,
                        "title": "Document embedded",
                        "component": "P3 B1 EmbeddingPipeline",
                        "status": "success",
                        "latency_ms": 1.0,
                        "details": {
                            "pipeline": "B1 Sidecar -> EmbeddingPipeline -> P2VectorSink"
                        },
                    }
                ],
            }

    async def fake_b1_details() -> dict[str, Any]:
        return {
            "status": "ready",
            "backend": "onnx",
            "engine": "fastembed",
            "model": "BAAI/bge-small-zh-v1.5",
            "dimension": 512,
            "provider": "CPUExecutionProvider",
            "endpoint": "http://b1-sidecar:18081",
        }

    def fake_submit_long_text(**_kwargs: Any) -> dict[str, str]:
        return {
            "task_id": "task-1",
            "state": "PENDING",
            "trace_id": "trace-1",
        }

    async def fake_wait(*_args: Any, **_kwargs: Any) -> tuple[dict[str, Any], list[str]]:
        return (
            {
                "task_id": "task-1",
                "state": "SUCCEEDED",
                "chunk_count": 4,
                "p2_vector_count": 4,
                "p2_collection": "p3-b2-d512-test",
            },
            ["PROCESSING", "SUCCEEDED"],
        )

    async def fake_search(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return {
            "items": [{"task_id": "task-1", "score": 0.99}],
            "backend": "p2-e1",
            "collection": "p3-b2-d512-test",
            "query_model": "BAAI/bge-small-zh-v1.5",
            "query_dimension": 512,
        }

    monkeypatch.setattr(p3_service, "b1_runtime_details", fake_b1_details)
    monkeypatch.setattr(p3_service, "submit_long_text", fake_submit_long_text)
    monkeypatch.setattr(p3_service, "wait_for_b2_task", fake_wait)
    monkeypatch.setattr(p3_service, "search_b2_vectors", fake_search)

    report = await p3_service.run_full_test(
        _Runtime(),
        title="test document",
        content="test long-text content",
        user_message="what is in the document?",
        session_id="session-1",
    )

    assert report["b1_runtime"]["backend"] == "onnx"
    assert report["b2_async"]["state_history"] == [
        "PENDING",
        "PROCESSING",
        "SUCCEEDED",
    ]
    assert report["b2_async"]["search_match_count"] == 1
    components = [event["component"] for event in report["events"]]
    assert components == [
        "P3 B1 EmbeddingPipeline",
        "P3 B2 Redis Task Queue",
        "P3 B2 Celery Worker",
        "P2 E1 Async Vector Search",
    ]
