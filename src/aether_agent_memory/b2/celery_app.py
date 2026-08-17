"""Celery worker entry point for B2 long-text ingestion."""

from __future__ import annotations

import asyncio
import os
from typing import Any
from uuid import uuid4

import grpc
from celery import Celery
from celery.exceptions import SoftTimeLimitExceeded

from aether_agent_memory.b1.models import EmbeddingRecord
from aether_agent_memory.b2.b1_client import (
    B1EmbeddingServiceClient,
    B1EmbeddingUnavailableError,
)
from aether_agent_memory.b2.milvus_store import MilvusMemoryStore
from aether_agent_memory.b2.p2_bridge import embedding_records, p2_collection_for_scope
from aether_agent_memory.b2.task_status import RedisTaskStatusStore, TaskState
from aether_agent_memory.b2.text_classifier import classify_text
from aether_agent_memory.p2 import P2GrpcClient, P2UnavailableError

REDIS_URL = os.getenv("AETHER_B2_REDIS_URL", "redis://localhost:6379/0")
BROKER_URL = os.getenv("AETHER_B2_BROKER_URL", REDIS_URL)
RESULT_BACKEND = os.getenv("AETHER_B2_RESULT_BACKEND", REDIS_URL)
TASK_STATUS_URL = os.getenv("AETHER_B2_TASK_STATUS_URL", REDIS_URL)
MILVUS_URI = os.getenv("AETHER_B2_MILVUS_URI", "http://localhost:19530")
MILVUS_COLLECTION = os.getenv("AETHER_B2_MILVUS_COLLECTION", "b2_memory_chunks_v3")
MILVUS_PROJECTION = os.getenv("AETHER_B2_MILVUS_PROJECTION", "false").lower() in {
    "1",
    "true",
    "yes",
}
VECTOR_DIMENSION = int(os.getenv("AETHER_B2_VECTOR_DIMENSION", "512"))
B1_ENDPOINT = os.getenv(
    "AETHER_B1_EMBEDDING_URL", "http://localhost:18081/v1/intercept"
)
P2_ENDPOINT = os.getenv("AETHER_P2_GRPC", "localhost:50052")
P2_BUCKET = os.getenv("AETHER_P2_BUCKET", "p3-memory")
P2_COLLECTION = os.getenv("AETHER_P2_COLLECTION", "p3")
P2_TIMEOUT_SECONDS = float(os.getenv("AETHER_P2_TIMEOUT_SECONDS", "10"))
TASK_SOFT_TIME_LIMIT_SECONDS = int(os.getenv("AETHER_B2_TASK_SOFT_TIME_LIMIT_SECONDS", "90"))
TASK_TIME_LIMIT_SECONDS = int(os.getenv("AETHER_B2_TASK_TIME_LIMIT_SECONDS", "120"))

if VECTOR_DIMENSION <= 0:
    raise RuntimeError("AETHER_B2_VECTOR_DIMENSION must be positive")
if TASK_SOFT_TIME_LIMIT_SECONDS <= 0 or TASK_TIME_LIMIT_SECONDS <= TASK_SOFT_TIME_LIMIT_SECONDS:
    raise RuntimeError("B2 task time limits must be positive and hard limit must exceed soft limit")

celery_app = Celery("aether_b2", broker=BROKER_URL, backend=RESULT_BACKEND)
celery_app.conf.task_track_started = True


async def _upsert_p2_vectors(records: list[EmbeddingRecord], *, collection: str) -> None:
    client = P2GrpcClient(
        P2_ENDPOINT,
        bucket=P2_BUCKET,
        collection=P2_COLLECTION,
        timeout_seconds=P2_TIMEOUT_SECONDS,
    )
    try:
        await client.upsert_vectors(records, collection=collection)
    finally:
        await client.close()


@celery_app.task(  # type: ignore[untyped-decorator]
    name="b2.process_long_text",
    bind=True,
    max_retries=3,
    soft_time_limit=TASK_SOFT_TIME_LIMIT_SECONDS,
    time_limit=TASK_TIME_LIMIT_SECONDS,
)
def process_long_text(self: Any, payload: dict[str, Any]) -> dict[str, object]:
    """Call B1, then persist B2's memory projection and task trace."""
    task_id = str(payload["task_id"])
    status = RedisTaskStatusStore(TASK_STATUS_URL)
    status.set(task_id, TaskState.PROCESSING)
    try:
        b1_result = B1EmbeddingServiceClient(B1_ENDPOINT).process(
            {
                "text": payload["text"],
                "source_type": payload["source_type"],
                "request_id": payload["request_id"],
                "trace_id": payload["trace_id"],
                "tenant_id": payload["tenant_id"],
                "source_id": payload["source_id"],
                "object_id": payload["object_id"],
                "memory_id": payload["memory_id"],
                "metadata": {
                    "session_id": payload["session_id"],
                    "agent_id": payload["agent_id"],
                },
            }
        )
        classification = classify_text(str(payload["text"]))
        p2_records = embedding_records(
            payload,
            b1_result,
            category=classification.category,
            keywords=classification.keywords,
        )
        if not p2_records:
            raise ValueError("B1 returned no P2 vector records")
        p2_collection = p2_collection_for_scope(
            P2_COLLECTION,
            str(payload["tenant_id"]),
            str(payload["user_id"]),
            str(payload["agent_id"]),
            len(p2_records[0].vector),
        )
        asyncio.run(_upsert_p2_vectors(p2_records, collection=p2_collection))
        milvus_count = 0
        if MILVUS_PROJECTION:
            milvus_count = MilvusMemoryStore(
                MILVUS_URI,
                collection_name=MILVUS_COLLECTION,
                dimension=VECTOR_DIMENSION,
            ).upsert_b1_records(
                task_id=task_id,
                memory_id=str(payload["memory_id"]),
                tenant_id=str(payload["tenant_id"]),
                user_id=str(payload["user_id"]),
                agent_id=str(payload["agent_id"]),
                session_id=str(payload["session_id"]),
                source_id=str(payload["source_id"]),
                content_ref=str(payload["content_ref"]),
                records=list(b1_result["records"]),
                category=classification.category,
                keywords=classification.keywords,
            )
        return status.set(
            task_id,
            TaskState.SUCCEEDED,
            chunk_count=len(p2_records),
            p2_vector_count=len(p2_records),
            p2_collection=p2_collection,
            milvus_projected=MILVUS_PROJECTION,
            milvus_vector_count=milvus_count,
            memory_id=payload["memory_id"],
            request_id=payload["request_id"],
            trace_id=payload["trace_id"],
            source_id=payload["source_id"],
            object_id=payload["object_id"],
            content_ref=payload["content_ref"],
            category=classification.category,
            keywords=classification.keywords,
        )
    except SoftTimeLimitExceeded as exc:
        status.set(task_id, TaskState.FAILED, error="task soft time limit exceeded")
        raise
    except (B1EmbeddingUnavailableError, P2UnavailableError, grpc.RpcError) as exc:
        if self.request.retries < self.max_retries:
            raise self.retry(exc=exc, countdown=2**self.request.retries) from exc
        status.set(task_id, TaskState.FAILED, error=f"{type(exc).__name__}: {exc}")
        raise
    except Exception as exc:
        if self.request.retries < self.max_retries:
            raise self.retry(exc=exc, countdown=2**self.request.retries) from exc
        status.set(task_id, TaskState.FAILED, error=f"{type(exc).__name__}: {exc}")
        raise


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
    """Queue a B2 long-text task and immediately return its identifiers."""
    task_id = uuid4().hex
    stored_object_id = object_id or source_id
    payload = {
        "task_id": task_id,
        "memory_id": uuid4().hex,
        "text": text,
        "tenant_id": tenant_id,
        "user_id": user_id,
        "agent_id": agent_id,
        "session_id": session_id,
        "source_id": source_id,
        "object_id": stored_object_id,
        "content_ref": content_ref or f"p2://{P2_BUCKET}/{stored_object_id}",
        "request_id": request_id or uuid4().hex,
        "trace_id": trace_id or uuid4().hex,
        "source_type": "document",
    }
    status = RedisTaskStatusStore(TASK_STATUS_URL)
    status.set(
        task_id,
        TaskState.PENDING,
        memory_id=payload["memory_id"],
        request_id=payload["request_id"],
        trace_id=payload["trace_id"],
        source_id=source_id,
        object_id=payload["object_id"],
        content_ref=payload["content_ref"],
    )
    try:
        process_long_text.delay(payload)
    except Exception as exc:
        status.set(task_id, TaskState.FAILED, error=f"{type(exc).__name__}: {exc}")
        raise
    return {
        "task_id": task_id,
        "memory_id": str(payload["memory_id"]),
        "state": TaskState.PENDING.value,
        "request_id": str(payload["request_id"]),
        "trace_id": str(payload["trace_id"]),
        "source_id": source_id,
        "object_id": str(payload["object_id"]),
        "content_ref": str(payload["content_ref"]),
    }
