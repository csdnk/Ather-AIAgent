"""Persistent HTTP facade for the unified P3 B1/B2/B3 service."""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from time import monotonic, perf_counter
from typing import Any
from uuid import uuid4

import httpx
from dashboard_page import DASHBOARD_HTML

from aether_agent_memory.b1 import EmbeddingRequest
from aether_agent_memory.b2 import MemoryEvent
from aether_agent_memory.b2.b1_client import B1EmbeddingServiceClient
from aether_agent_memory.b2.celery_app import (
    B1_ENDPOINT,
    TASK_STATUS_URL,
    submit_long_text,
)
from aether_agent_memory.b2.p2_bridge import p2_collection_for_scope
from aether_agent_memory.b2.task_status import RedisTaskStatusStore, TaskState
from aether_agent_memory.b3 import ScheduleRequest
from aether_agent_memory.context import ContextRequest
from aether_agent_memory.runtime import P3Runtime, P3RuntimeConfig

MAX_HISTORY = 100
STATE: dict[str, Any] = {
    "last_run": None,
    "last_status": "idle",
    "last_output": "",
    "last_report": None,
    "flow_history": [],
    "schedule_history": [],
}


def demo_enabled() -> bool:
    return os.getenv("AETHER_ENABLE_DEMO", "false").lower() in {"1", "true", "yes"}


async def with_runtime[T](operation: Callable[[P3Runtime], Awaitable[T]]) -> T:
    runtime = P3Runtime(P3RuntimeConfig.from_environment())
    try:
        return await operation(runtime)
    finally:
        await runtime.close()


def json_model(model: Any) -> dict[str, Any]:
    return model.model_dump(mode="json")


async def b1_runtime_details() -> dict[str, Any]:
    """Read the real Sidecar runtime used by the integration test."""
    base_url = B1_ENDPOINT.removesuffix("/v1/intercept").rstrip("/")
    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.get(f"{base_url}/health/ready")
        response.raise_for_status()
        payload = response.json()
    if not isinstance(payload, dict) or payload.get("status") != "ready":
        raise RuntimeError(f"B1 Sidecar is not ready: {payload}")
    return {
        "status": payload.get("status"),
        "backend": payload.get("backend"),
        "engine": payload.get("engine"),
        "model": payload.get("model"),
        "dimension": payload.get("dimension"),
        "provider": payload.get("provider"),
        "endpoint": base_url,
    }


async def wait_for_b2_task(
    task_id: str,
    *,
    status_store: RedisTaskStatusStore | None = None,
    timeout_seconds: float = 60,
    poll_interval_seconds: float = 0.25,
) -> tuple[dict[str, Any], list[str]]:
    """Wait for the existing Redis task record with a strict deadline."""
    if timeout_seconds <= 0:
        raise ValueError("B2 task timeout must be positive")
    if poll_interval_seconds <= 0:
        raise ValueError("B2 task poll interval must be positive")
    store = status_store or RedisTaskStatusStore(TASK_STATUS_URL)
    deadline = monotonic() + timeout_seconds
    transitions: list[str] = []
    last_record: dict[str, Any] | None = None
    while True:
        record = await asyncio.to_thread(store.get, task_id)
        if record is not None:
            last_record = record
            state = str(record.get("state", ""))
            if state and (not transitions or transitions[-1] != state):
                transitions.append(state)
            if state == TaskState.SUCCEEDED.value:
                return record, transitions
            if state == TaskState.FAILED.value:
                raise RuntimeError(
                    f"B2 async task {task_id} failed: {record.get('error', 'unknown error')}"
                )
        remaining = deadline - monotonic()
        if remaining <= 0:
            last_state = last_record.get("state") if last_record else "not found"
            raise TimeoutError(
                f"B2 async task {task_id} did not finish within {timeout_seconds:g}s "
                f"(last state: {last_state})"
            )
        await asyncio.sleep(min(poll_interval_seconds, remaining))


async def search_b2_vectors(
    runtime: P3Runtime,
    *,
    query: str,
    tenant_id: str,
    user_id: str,
    agent_id: str,
    limit: int,
    task_id: str | None = None,
) -> dict[str, Any]:
    """Use the existing B1 query and P2 E1 search path."""
    b1_result = await asyncio.to_thread(
        B1EmbeddingServiceClient(B1_ENDPOINT).process,
        {
            "text": query,
            "source_type": "document",
            "request_id": uuid4().hex,
            "tenant_id": tenant_id,
            "source_id": f"b2-query-{uuid4().hex}",
            "metadata": {"agent_id": agent_id},
            "input_type": "query",
        },
    )
    records = list(b1_result["records"])
    if len(records) != 1:
        raise ValueError("B1 returned more than one query vector")
    vector = list(records[0]["vector"])
    collection = p2_collection_for_scope(
        runtime.client.collection,
        tenant_id,
        user_id,
        agent_id,
        len(vector),
    )
    hits = await runtime.client.search_vectors(
        vector,
        top_k=max(limit, 100) if task_id else limit,
        collection=collection,
    )
    items = []
    for hit in hits:
        metadata = hit.metadata
        if (
            metadata.get("tenant_id") != tenant_id
            or metadata.get("user_id") != user_id
            or metadata.get("agent_id") != agent_id
            or (task_id is not None and metadata.get("task_id") != task_id)
        ):
            continue
        items.append(
            {
                "memory_id": metadata.get("memory_id"),
                "task_id": metadata.get("task_id"),
                "chunk_id": hit.id,
                "text": metadata.get("chunk_text", ""),
                "score": hit.score,
                "tenant_id": metadata.get("tenant_id"),
                "user_id": metadata.get("user_id"),
                "category": metadata.get("category", "other"),
                "keywords": metadata.get("keywords", []),
                "content_ref": metadata.get("content_ref"),
                "trace_id": metadata.get("trace_id"),
            }
        )
        if len(items) == limit:
            break
    return {
        "items": items,
        "backend": "p2-e1",
        "collection": collection,
        "query_model": records[0].get("embedding_model"),
        "query_dimension": len(vector),
    }


async def run_full_test(
    runtime: P3Runtime,
    *,
    title: str,
    content: str,
    user_message: str,
    session_id: str,
    agent_id: str = "knowledge-assistant",
    user_id: str = "dashboard-user",
    tenant_id: str = "dashboard-tenant",
    async_timeout_seconds: float = 60,
    async_poll_interval_seconds: float = 0.25,
) -> dict[str, Any]:
    """Extend the existing scenario with observable B1 and B2 async checks."""
    report = await runtime.run_knowledge_session(
        title=title,
        content=content,
        user_message=user_message,
        session_id=session_id,
        agent_id=agent_id,
        user_id=user_id,
        tenant_id=tenant_id,
    )
    events: list[dict[str, Any]] = report["events"]
    b1_details = await b1_runtime_details()
    b1_event = next(
        event for event in events if event["component"] == "P3 B1 EmbeddingPipeline"
    )
    b1_event["details"].update(b1_details)
    report["b1_runtime"] = b1_details

    submitted_at = perf_counter()
    submission = await asyncio.to_thread(
        submit_long_text,
        text=content,
        tenant_id=tenant_id,
        user_id=user_id,
        agent_id=agent_id,
        session_id=session_id,
        source_id=title,
        object_id=report["object_key"],
        content_ref=f"p2://{runtime.client.bucket}/{report['object_key']}",
        request_id=report["request_id"],
        trace_id=report["trace_id"],
    )
    events.append(
        {
            "step": len(events) + 1,
            "title": "Long-text task submitted",
            "component": "P3 B2 Redis Task Queue",
            "status": "success",
            "latency_ms": round((perf_counter() - submitted_at) * 1000, 2),
            "details": {
                "task_id": submission["task_id"],
                "state": submission["state"],
                "broker": "Redis",
                "worker": "Celery",
                "input_chars": len(content),
                "trace_id": submission["trace_id"],
            },
        }
    )

    processing_at = perf_counter()
    final_status, transitions = await wait_for_b2_task(
        submission["task_id"],
        timeout_seconds=async_timeout_seconds,
        poll_interval_seconds=async_poll_interval_seconds,
    )
    state_history = [submission["state"]]
    state_history.extend(state for state in transitions if state != state_history[-1])
    events.append(
        {
            "step": len(events) + 1,
            "title": "Long text processed asynchronously",
            "component": "P3 B2 Celery Worker",
            "status": "success",
            "latency_ms": round((perf_counter() - processing_at) * 1000, 2),
            "details": {
                "task_id": submission["task_id"],
                "state": final_status["state"],
                "state_history": state_history,
                "chunk_count": final_status.get("chunk_count", 0),
                "p2_vector_count": final_status.get("p2_vector_count", 0),
                "p2_collection": final_status.get("p2_collection"),
                "pipeline": "Redis -> Celery -> B1 Sidecar -> P2 E1",
            },
        }
    )

    search_at = perf_counter()
    search = await search_b2_vectors(
        runtime,
        query=content[:200],
        tenant_id=tenant_id,
        user_id=user_id,
        agent_id=agent_id,
        limit=5,
        task_id=submission["task_id"],
    )
    if not search["items"]:
        raise RuntimeError("B2 async vectors were written but could not be found in P2 E1")
    events.append(
        {
            "step": len(events) + 1,
            "title": "Asynchronous vectors searched",
            "component": "P2 E1 Async Vector Search",
            "status": "success",
            "latency_ms": round((perf_counter() - search_at) * 1000, 2),
            "details": {
                "backend": search["backend"],
                "collection": search["collection"],
                "query_model": search["query_model"],
                "dimension": search["query_dimension"],
                "match_count": len(search["items"]),
                "top_score": search["items"][0]["score"],
                "task_id": submission["task_id"],
            },
        }
    )
    report["b2_async"] = {
        **final_status,
        "state_history": state_history,
        "search_backend": search["backend"],
        "search_match_count": len(search["items"]),
        "search_top_score": search["items"][0]["score"],
    }
    return report


def prepend_history(key: str, record: dict[str, Any]) -> None:
    history: list[dict[str, Any]] = STATE[key]
    history.insert(0, record)
    del history[MAX_HISTORY:]


def record_schedule_result(source: str, result: dict[str, Any]) -> None:
    for entry in result.get("entries", []):
        action = entry["action"]
        feedback = entry["feedback"]
        prepend_history(
            "schedule_history",
            {
                "timestamp": feedback["timestamp"],
                "source": source,
                "action_id": action["action_id"],
                "request_id": action["request_id"],
                "trace_id": action["trace_id"],
                "object_id": action["object_id"],
                "action_type": action["action_type"],
                "source_tier": action["source_tier"],
                "target_tier": action["target_tier"],
                "priority": action["priority"],
                "score": action["score"],
                "score_frequency": action["score_frequency"],
                "score_semantic": action["score_semantic"],
                "score_decay": action["score_decay"],
                "score_cost": action["score_cost"],
                "reason": action["reason"],
                "policy_version": action["policy_version"],
                "execute_status": feedback["execute_status"],
                "execute_latency_ms": feedback["execute_latency_ms"],
            },
        )


def record_demo_run(source: str, report: dict[str, Any]) -> None:
    timestamp = datetime.now(UTC).isoformat()
    prepend_history(
        "flow_history",
        {
            "timestamp": timestamp,
            "request_id": report["request_id"],
            "trace_id": report["trace_id"],
            "events": report["events"],
        },
    )
    record_schedule_result(
        source,
        {
            "entries": [
                {
                    "action": report["action"],
                    "feedback": report["feedback"],
                }
            ]
        },
    )


class P3Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/":
            self._send(HTTPStatus.OK, "text/html; charset=utf-8", DASHBOARD_HTML.encode("utf-8"))
            return
        if self.path == "/api/flows":
            self._json(HTTPStatus.OK, {"items": STATE["flow_history"]})
            return
        if self.path == "/api/schedules":
            self._json(HTTPStatus.OK, {"items": STATE["schedule_history"]})
            return
        if self.path in {"/health", "/api/status"}:
            online = asyncio.run(with_runtime(lambda runtime: runtime.health()))
            self._json(
                HTTPStatus.OK,
                {
                    "p2_online": online,
                    "p2_endpoint": P3RuntimeConfig.from_environment().p2_endpoint,
                    "demo_enabled": demo_enabled(),
                    **STATE,
                },
            )
            return
        if self.path.startswith("/api/v1/b2/tasks/"):
            task_id = self.path.removeprefix("/api/v1/b2/tasks/")
            record = RedisTaskStatusStore(TASK_STATUS_URL).get(task_id)
            if record is None:
                self._json(HTTPStatus.NOT_FOUND, {"error": "task not found", "task_id": task_id})
            else:
                self._json(HTTPStatus.OK, record)
            return
        self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        try:
            body = self._body()
            if self.path == "/api/v1/embeddings":
                result = asyncio.run(
                    with_runtime(
                        lambda runtime: runtime.embed(EmbeddingRequest.model_validate(body))
                    )
                )
                self._json(HTTPStatus.OK, json_model(result))
                return
            if self.path == "/api/v1/b2/long-text":
                text = str(body.get("text", ""))
                tenant_id = str(body.get("tenant_id", "")).strip()
                user_id = str(body.get("user_id", "")).strip()
                agent_id = str(body.get("agent_id", "")).strip()
                session_id = str(body.get("session_id", "")).strip()
                source_id = str(body.get("source_id", "")).strip()
                if not all((text.strip(), tenant_id, user_id, agent_id, session_id, source_id)):
                    raise ValueError(
                        "text, tenant_id, user_id, agent_id, session_id, and source_id are required"
                    )
                object_key = f"b2/long-text/{uuid4().hex}.txt"
                object_meta = asyncio.run(
                    with_runtime(
                        lambda runtime: runtime.client.put_object(object_key, text.encode("utf-8"))
                    )
                )
                content_ref = f"p2://{object_meta.bucket}/{object_meta.key}"
                submission = submit_long_text(
                    text=text,
                    tenant_id=tenant_id,
                    user_id=user_id,
                    agent_id=agent_id,
                    session_id=session_id,
                    source_id=source_id,
                    object_id=object_meta.key,
                    content_ref=content_ref,
                    request_id=str(body.get("request_id") or "") or None,
                    trace_id=str(body.get("trace_id") or "") or None,
                )
                submission.update(
                    {
                        "p2_bucket": object_meta.bucket,
                        "object_key": object_meta.key,
                        "content_ref": content_ref,
                    }
                )
                self._json(
                    HTTPStatus.ACCEPTED,
                    submission,
                )
                return
            if self.path == "/api/v1/b2/search":
                query = str(body.get("query", "")).strip()
                tenant_id = str(body.get("tenant_id", "")).strip()
                user_id = str(body.get("user_id", "")).strip()
                agent_id = str(body.get("agent_id", "")).strip()
                if not all((query, tenant_id, user_id, agent_id)):
                    raise ValueError("query, tenant_id, user_id, and agent_id are required")
                limit = int(body.get("limit", 5))
                if not 1 <= limit <= 100:
                    raise ValueError("limit must be between 1 and 100")
                result = asyncio.run(
                    with_runtime(
                        lambda runtime: search_b2_vectors(
                            runtime,
                            query=query,
                            tenant_id=tenant_id,
                            user_id=user_id,
                            agent_id=agent_id,
                            limit=limit,
                        )
                    )
                )
                self._json(HTTPStatus.OK, result)
                return
            if self.path == "/api/v1/memory/events":
                result = asyncio.run(
                    with_runtime(
                        lambda runtime: runtime.ingest_memory(MemoryEvent.model_validate(body))
                    )
                )
                self._json(HTTPStatus.OK, json_model(result))
                return
            if self.path == "/api/v1/context":
                result = asyncio.run(
                    with_runtime(
                        lambda runtime: runtime.build_context(ContextRequest.model_validate(body))
                    )
                )
                self._json(HTTPStatus.OK, json_model(result))
                return
            if self.path == "/api/v1/schedules":
                result = asyncio.run(
                    with_runtime(
                        lambda runtime: runtime.schedule_once(ScheduleRequest.model_validate(body))
                    )
                )
                payload = json_model(result)
                record_schedule_result("api-v1-schedules", payload)
                self._json(HTTPStatus.OK, payload)
                return
            if self.path == "/api/demo/session":
                if not demo_enabled():
                    self._json(HTTPStatus.NOT_FOUND, {"error": "demo mode is disabled"})
                    return
                title = str(body.get("title", "")).strip()
                content = str(body.get("content", "")).strip()
                user_message = str(body.get("user_message", "")).strip()
                if not title or not content or not user_message:
                    raise ValueError("title, content, and user_message are required")
                report = asyncio.run(
                    with_runtime(
                        lambda runtime: run_full_test(
                            runtime,
                            title=title,
                            content=content,
                            user_message=user_message,
                            session_id=str(body.get("session_id", "dashboard-session")),
                        )
                    )
                )
                record_demo_run("knowledge-session", report)
                STATE.update(
                    {
                        "last_run": datetime.now(UTC).isoformat(),
                        "last_status": "passed",
                        "last_output": (
                            f"B1={report['b1_runtime']['backend']}/"
                            f"{report['b1_runtime']['model']}; "
                            f"B2 async={report['b2_async']['state']} "
                            f"chunks={report['b2_async']['chunk_count']}; "
                            f"P2 search={report['b2_async']['search_match_count']}; "
                            f"B3 action={report['action']['action_type']}"
                        ),
                        "last_report": report,
                    }
                )
                self._json(HTTPStatus.OK, report)
                return
            if self.path == "/api/run-smoke":
                if not demo_enabled():
                    self._json(HTTPStatus.NOT_FOUND, {"error": "demo mode is disabled"})
                    return
                report = asyncio.run(
                    with_runtime(
                        lambda runtime: run_full_test(
                            runtime,
                            title="Persistent service smoke test",
                            content=(
                                "Aether P3 writes a traceable semantic vector into the P2 engine."
                            ),
                            user_message="Which document is available for semantic recall?",
                            session_id="p3-service-smoke",
                            agent_id="p3-service-agent",
                            tenant_id="integration",
                        )
                    )
                )
                record_demo_run("integration-smoke", report)
                STATE.update(
                    {
                        "last_run": datetime.now(UTC).isoformat(),
                        "last_status": "passed",
                        "last_output": (
                            f"B1={report['b1_runtime']['backend']}/"
                            f"{report['b1_runtime']['model']}; "
                            f"B2 async={report['b2_async']['state']} "
                            f"chunks={report['b2_async']['chunk_count']}; "
                            f"P2 search={report['b2_async']['search_match_count']}; "
                            f"B3 action={report['action']['action_type']}"
                        ),
                        "last_report": report,
                    }
                )
                self._json(HTTPStatus.OK, STATE)
                return
            self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            if self.path in {"/api/demo/session", "/api/run-smoke"}:
                STATE.update(
                    {
                        "last_run": datetime.now(UTC).isoformat(),
                        "last_status": "failed",
                        "last_output": error,
                    }
                )
            self._json(HTTPStatus.UNPROCESSABLE_ENTITY, {"error": error})

    def log_message(self, _format: str, *_args: object) -> None:
        return

    def _body(self) -> dict[str, Any]:
        content_length = int(self.headers.get("Content-Length", "0"))
        return json.loads(self.rfile.read(content_length) or b"{}")

    def _json(self, status: HTTPStatus, payload: Any) -> None:
        self._send(status, "application/json; charset=utf-8", json.dumps(payload).encode("utf-8"))

    def _send(self, status: HTTPStatus, content_type: str, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


if __name__ == "__main__":
    port = int(os.getenv("AETHER_P3_PORT", "8080"))
    ThreadingHTTPServer(("0.0.0.0", port), P3Handler).serve_forever()
