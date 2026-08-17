"""B2 record and collection mapping for the P2 storage boundary."""

from __future__ import annotations

from hashlib import sha256
from typing import Any

from aether_agent_memory.b1.models import EmbeddingRecord


def p2_collection_for_scope(
    base: str, tenant_id: str, user_id: str, agent_id: str, dimension: int
) -> str:
    if dimension <= 0:
        raise ValueError("P2 collection dimension must be positive")
    scope = "\0".join((tenant_id, user_id, agent_id)).encode("utf-8")
    return f"{base}-b2-d{dimension}-{sha256(scope).hexdigest()[:20]}"


def embedding_records(
    payload: dict[str, Any], b1_result: dict[str, Any], *, category: str, keywords: list[str]
) -> list[EmbeddingRecord]:
    common_metadata = {
        "task_id": str(payload["task_id"]),
        "memory_id": str(payload["memory_id"]),
        "tenant_id": str(payload["tenant_id"]),
        "user_id": str(payload["user_id"]),
        "agent_id": str(payload["agent_id"]),
        "session_id": str(payload["session_id"]),
        "content_ref": str(payload["content_ref"]),
        "category": category,
        "keywords": keywords,
    }
    return [
        EmbeddingRecord(
            request_id=str(b1_result["request_id"]),
            trace_id=str(b1_result["trace_id"]),
            source_id=str(b1_result["source_id"]),
            object_id=str(record.get("object_id") or payload["object_id"]),
            chunk_id=f"{payload['memory_id']}:{record['chunk_id']}",
            chunk_text=str(record["chunk_text"]),
            vector=[float(value) for value in record["vector"]],
            embedding_model=str(record["embedding_model"]),
            metadata={
                **dict(record.get("metadata", {})),
                **common_metadata,
                "b1_chunk_id": str(record["chunk_id"]),
            },
        )
        for record in b1_result["records"]
    ]
