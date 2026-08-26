"""Persistent HTTP facade for the unified P3 B1/B2/B3 service."""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler
from time import monotonic, perf_counter
from typing import Any
from uuid import uuid4

import httpx
from dashboard_page import DASHBOARD_HTML

from aether_agent_memory.b1 import EmbeddingRequest
from aether_agent_memory.b2 import MemoryEvent
from aether_agent_memory.b2.b1_client import B1EmbeddingServiceClient
from aether_agent_memory.b2.p2_bridge import p2_collection_for_scope
from aether_agent_memory.b2.task_status import RedisTaskStatusStore, TaskState
from aether_agent_memory.b3 import ScheduleRequest
from aether_agent_memory.context import ContextRequest
from aether_agent_memory.context.models import ContextPack
from aether_agent_memory.core.enums import MemoryType, StorageTier
from aether_agent_memory.core.memory import Memory
from aether_agent_memory.integration import request_context_from_payload, runtime_error_to_http
from aether_agent_memory.runtime import MemoryRuntime, P3RuntimeConfig, RuntimeErrorBase

try:
    from aether_agent_memory.b2.celery_app import (
        B1_ENDPOINT,
        TASK_STATUS_URL,
        submit_long_text,
    )
except ImportError as exc:
    B1_ENDPOINT = os.getenv(
        "AETHER_B1_EMBEDDING_URL",
        "http://localhost:18081/v1/intercept",
    )
    TASK_STATUS_URL = os.getenv(
        "AETHER_B2_TASK_STATUS_URL",
        os.getenv("AETHER_B2_REDIS_URL", "redis://localhost:6379/0"),
    )
    _CELERY_IMPORT_ERROR = exc

    def submit_long_text(
        *,
        text: str,
        tenant_id: str,
        user_id: str,
        agent_id: str,
        session_id: str,
        source_id: str,
        object_id: str | None = None,
        content_ref: str | None = None,
        request_id: str | None = None,
        trace_id: str | None = None,
    ) -> dict[str, str]:
        raise RuntimeError(f"B2 Celery path is unavailable: {_CELERY_IMPORT_ERROR}")

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


async def with_runtime[T](operation: Callable[[MemoryRuntime], Awaitable[T]]) -> T:
    runtime = MemoryRuntime.from_config(P3RuntimeConfig.from_environment())
    try:
        return await operation(runtime)
    finally:
        await runtime.close()


def json_model(model: Any) -> dict[str, Any]:
    payload = model.model_dump(mode="json")
    if not isinstance(payload, dict):
        raise TypeError("model_dump did not return a mapping")
    return dict(payload)


async def b1_runtime_details() -> dict[str, Any]:
    """Read the real Sidecar runtime used by the integration test."""
    base_url = B1_ENDPOINT.removesuffix("/v1/intercept").rstrip("/")
    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.get(f"{base_url}/health/ready")
        response.raise_for_status()
        raw_payload = response.json()
    if not isinstance(raw_payload, dict):
        raise RuntimeError(f"B1 Sidecar returned an invalid payload: {raw_payload}")
    payload: dict[str, Any] = raw_payload
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


async def b1_sidecar_status() -> dict[str, Any]:
    """Aggregate B1 Sidecar read-only runtime views for the web UI."""

    base_url = B1_ENDPOINT.removesuffix("/v1/intercept").rstrip("/")
    snapshot: dict[str, Any] = {
        "endpoint": base_url,
        "status": "UNAVAILABLE",
        "health": None,
        "metrics": None,
        "capabilities": None,
        "events": [],
        "error": None,
    }
    async with httpx.AsyncClient(timeout=10) as client:
        try:
            health_response = await client.get(f"{base_url}/health/ready")
            snapshot["health"] = health_response.json()
            snapshot["http_status"] = health_response.status_code
            if health_response.status_code < 500:
                snapshot["status"] = (
                    "HEALTHY"
                    if snapshot["health"].get("status") == "ready"
                    else "DEGRADED"
                )
        except Exception as exc:
            snapshot["error"] = f"{type(exc).__name__}: {exc}"
            return snapshot

        for key, path in (
            ("metrics", "/metrics"),
            ("capabilities", "/v1/capabilities"),
            ("events", "/v1/events?limit=20"),
        ):
            try:
                response = await client.get(f"{base_url}{path}")
                response.raise_for_status()
                payload = response.json()
                snapshot[key] = payload.get("items", []) if key == "events" else payload
            except Exception as exc:
                snapshot["status"] = "DEGRADED"
                snapshot[f"{key}_error"] = f"{type(exc).__name__}: {exc}"
    return snapshot


async def b1_sidecar_embedding(body: dict[str, Any]) -> dict[str, Any]:
    """Call the real B1 Sidecar through P3 while preserving query/passage mode."""

    text = str(body.get("text", "")).strip()
    if not text:
        raise ValueError("text is required")
    input_type = str(body.get("input_type", body.get("usage", "passage"))).strip().lower()
    if input_type not in {"query", "passage"}:
        raise ValueError("input_type must be query or passage")
    request_id = str(body.get("request_id") or uuid4().hex)
    trace_id = str(body.get("trace_id") or uuid4().hex)
    source_id = str(body.get("source_id") or f"web-b1-{request_id}")
    tenant_id = str(body.get("tenant_id") or "web-demo")
    metadata = body.get("metadata") if isinstance(body.get("metadata"), dict) else {}
    base_url = B1_ENDPOINT.removesuffix("/v1/intercept").rstrip("/")
    request_payload = {
        "items": [
            {
                "request_id": request_id,
                "trace_id": trace_id,
                "tenant_id": tenant_id,
                "source_type": str(body.get("source_type") or "web_embedding"),
                "source_id": source_id,
                "object_id": body.get("object_id"),
                "chunk_id": str(body.get("chunk_id") or f"{source_id}:0000"),
                "chunk_text": text,
                "embedding_required": True,
                "input_type": input_type,
                "metadata": {"web_source": "aetherbrain-web", **metadata},
            }
        ]
    }
    async with httpx.AsyncClient(timeout=60) as client:
        response = await client.post(f"{base_url}/v1/intercept", json=request_payload)
        payload = response.json()
    if response.status_code != 200:
        raise RuntimeError(f"B1 Sidecar returned HTTP {response.status_code}: {payload}")
    results = payload.get("results")
    if not isinstance(results, list) or not results:
        raise RuntimeError(f"B1 Sidecar returned no results: {payload}")
    result = results[0]
    chunks = result.get("chunks", []) if isinstance(result, dict) else []
    records = [
        {
            "request_id": result.get("request_id", request_id),
            "trace_id": result.get("trace_id", trace_id),
            "source_id": result.get("source_id", source_id),
            "object_id": result.get("object_id"),
            "chunk_id": chunk.get("chunk_id"),
            "chunk_text": chunk.get("chunk_text"),
            "vector": chunk.get("vector", []),
            "embedding_model": result.get("embedding_model"),
            "metadata": chunk.get("metadata", {}),
        }
        for chunk in chunks
        if isinstance(chunk, dict)
    ]
    return {
        "request_id": result.get("request_id", request_id),
        "trace_id": result.get("trace_id", trace_id),
        "source_id": result.get("source_id", source_id),
        "status": result.get("status", payload.get("overall_status")),
        "input_type": input_type,
        "records": records,
        "latency_ms": result.get("latency_ms", payload.get("latency_ms")),
        "backend": result.get("backend"),
        "engine": result.get("engine"),
        "model": result.get("embedding_model"),
        "dimension": result.get("embedding_dim"),
        "normalized": result.get("normalized"),
        "quantization_type": result.get("quantization_type"),
        "raw_status": payload.get("overall_status"),
    }


def _bounded_score(value: float) -> float:
    return round(min(max(value, 0.0), 1.0), 6)


def _recency_score(memory: Memory) -> float:
    timestamp = memory.last_accessed_at or memory.updated_at or memory.created_at
    age_seconds = max((datetime.now(UTC) - timestamp).total_seconds(), 0.0)
    return _bounded_score(1.0 / (1.0 + age_seconds / 3600.0))


def _tier_for_memory(memory: Memory) -> StorageTier:
    if memory.p2_ref is not None:
        return memory.p2_ref.tier
    if memory.type == MemoryType.WORKING:
        return StorageTier.L0_DRAM
    if memory.type == MemoryType.EPISODIC:
        return StorageTier.L2_HDD
    return StorageTier.L3_OBJECT


def b3_candidates_from_context(context: ContextPack) -> dict[str, Any]:
    """Adapt recalled Memory records into schedulable B3 objects without inventing heat scores."""

    objects: list[dict[str, Any]] = []
    for memory in context.memories:
        recall_score = _bounded_score(float(context.recall_scores.get(memory.id, 0.0)))
        frequency_score = _bounded_score(min(memory.access_count / 10.0, 1.0))
        objects.append(
            {
                "object_id": memory.object_id or memory.id,
                "object_type": f"{memory.type.value}_memory",
                "current_tier": _tier_for_memory(memory).value,
                "size_bytes": len(memory.content.encode("utf-8")),
                "tenant_id": memory.tenant_id,
                "namespace": memory.session_id,
                "access": {
                    "access_frequency": frequency_score,
                    "recency_score": _recency_score(memory),
                    "hit_rate": 1.0 if recall_score > 0 else 0.0,
                    "access_count": memory.access_count,
                    "last_access_time": (
                        memory.last_accessed_at.isoformat()
                        if memory.last_accessed_at is not None
                        else None
                    ),
                },
                "semantic": {
                    "semantic_relevance": recall_score,
                    "importance": _bounded_score(memory.importance),
                    "task_relevance": recall_score,
                },
                "business_priority": _bounded_score(memory.importance),
                "migratable": True,
                "pinned": False,
                "expired": memory.is_expired(),
                "metadata": {
                    "memory_id": memory.id,
                    "memory_type": memory.type.value,
                    "source": memory.source.value,
                    "storage_inference": "p2_ref" if memory.p2_ref else "memory_type_adapter",
                    "execution_type": "control-plane",
                    "physical_migration": "external",
                    "content_preview": memory.content[:160],
                },
            }
        )
    return {
        "request_id": context.request.request_id,
        "trace_id": context.trace_id or context.request.trace_id,
        "source": "context-recall",
        "objects": objects,
        "resource_state": {
            "tiers": {},
            "migration_cost_score": 0.5,
            "network_available": True,
        },
        "context": context.model_dump(mode="json"),
        "notes": [
            "Candidates are derived from the current ContextPack.",
            "Heat score and action are produced only after POST /api/v1/schedules.",
            "Physical Milvus/Redis migration is not asserted by this adapter.",
        ],
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
    runtime: MemoryRuntime,
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
    runtime: MemoryRuntime,
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
                "memory_id": final_status.get("memory_id", submission.get("memory_id")),
                "compression_artifact_id": final_status.get("compression_artifact_id"),
                "compression_status": final_status.get("compression_status"),
                "compression_ratio": final_status.get("compression_ratio"),
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
    def do_OPTIONS(self) -> None:  # noqa: N802
        self._send(HTTPStatus.NO_CONTENT, "text/plain; charset=utf-8", b"")

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
            health = asyncio.run(with_runtime(lambda runtime: runtime.health()))
            health_payload = health.to_dict()
            self._json(
                HTTPStatus.OK,
                {
                    "p2_online": health.ready,
                    "p2_endpoint": P3RuntimeConfig.from_environment().p2_endpoint,
                    "runtime_profile": health.runtime_profile,
                    "runtime_health": health_payload,
                    "demo_enabled": demo_enabled(),
                    **STATE,
                },
            )
            return
        if self.path == "/api/v1/b1/status":
            self._json(HTTPStatus.OK, asyncio.run(b1_sidecar_status()))
            return
        if self.path.startswith("/api/v1/b2/tasks/"):
            task_id = self.path.removeprefix("/api/v1/b2/tasks/")
            record = asyncio.run(
                with_runtime(
                    lambda runtime: runtime.get_task(
                        task_id,
                        request_context_from_payload({"task_id": task_id}),
                    )
                )
            )
            if record is None:
                self._json(HTTPStatus.NOT_FOUND, {"error": "task not found", "task_id": task_id})
            else:
                self._json(HTTPStatus.OK, record)
            return
        self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        try:
            body = self._body()
            if self.path == "/api/v1/b1/embeddings":
                self._json(HTTPStatus.OK, asyncio.run(b1_sidecar_embedding(body)))
                return
            if self.path == "/api/v1/embeddings":
                context = request_context_from_payload(body)
                result = asyncio.run(
                    with_runtime(
                        lambda runtime: runtime.embed(
                            EmbeddingRequest.model_validate(body),
                            context,
                        )
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
                context = request_context_from_payload(body)
                submission = asyncio.run(
                    with_runtime(
                        lambda runtime: runtime.submit_long_memory(
                            text=text,
                            tenant_id=tenant_id,
                            user_id=user_id,
                            agent_id=agent_id,
                            session_id=session_id,
                            source_id=source_id,
                            context=context,
                        )
                    )
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
                context = request_context_from_payload(body)
                search_result = asyncio.run(
                    with_runtime(
                        lambda runtime: runtime.search_memory(
                            query=query,
                            tenant_id=tenant_id,
                            user_id=user_id,
                            agent_id=agent_id,
                            limit=limit,
                            context=context,
                        )
                    )
                )
                self._json(HTTPStatus.OK, search_result)
                return
            if self.path == "/api/v1/memory/events":
                context = request_context_from_payload(body)
                memory_result = asyncio.run(
                    with_runtime(
                        lambda runtime: runtime.write_memory(
                            MemoryEvent.model_validate(body),
                            context,
                        )
                    )
                )
                self._json(HTTPStatus.OK, json_model(memory_result))
                return
            if self.path == "/api/v1/context":
                context = request_context_from_payload(body)
                context_result = asyncio.run(
                    with_runtime(
                        lambda runtime: runtime.build_context(
                            ContextRequest.model_validate(body),
                            context,
                        )
                    )
                )
                self._json(HTTPStatus.OK, json_model(context_result))
                return
            if self.path == "/api/v1/b3/candidates":
                context = request_context_from_payload(body)
                context_result = asyncio.run(
                    with_runtime(
                        lambda runtime: runtime.build_context(
                            ContextRequest.model_validate(body),
                            context,
                        )
                    )
                )
                self._json(HTTPStatus.OK, b3_candidates_from_context(context_result))
                return
            if self.path == "/api/v1/schedules":
                context = request_context_from_payload(body)
                schedule_result = asyncio.run(
                    with_runtime(
                        lambda runtime: runtime.schedule(
                            ScheduleRequest.model_validate(body),
                            context,
                        )
                    )
                )
                payload = json_model(schedule_result)
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
        except RuntimeErrorBase as exc:
            status, payload = runtime_error_to_http(exc)
            self._json(status, payload)
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
        payload = json.loads(self.rfile.read(content_length) or b"{}")
        if not isinstance(payload, dict):
            raise ValueError("request body must be a JSON object")
        return dict(payload)

    def _json(self, status: HTTPStatus, payload: Any) -> None:
        self._send(status, "application/json; charset=utf-8", json.dumps(payload).encode("utf-8"))

    def _send(self, status: HTTPStatus, content_type: str, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Access-Control-Allow-Origin", os.getenv("AETHER_WEB_CORS_ORIGIN", "*"))
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main() -> None:
    """Run the FastAPI application host (single long-lived runtime per process)."""
    import uvicorn

    from aether_agent_memory.config.app_settings import AppSettings

    settings = AppSettings()
    settings.validate_for_profile()
    uvicorn.run(
        "aether_agent_memory.app:create_app",
        factory=True,
        host=settings.host,
        port=settings.port,
    )


if __name__ == "__main__":
    main()
