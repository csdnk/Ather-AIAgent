from __future__ import annotations

import asyncio
import os
from typing import Any
from uuid import uuid4

from aether_agent_memory.b2.b1_client import B1EmbeddingServiceClient
from aether_agent_memory.b2.p2_bridge import p2_collection_for_scope
from aether_agent_memory.core.enums import SourceType
from aether_agent_memory.runtime.request_context import RequestContext
from aether_agent_memory.runtime.status import ComponentHealth, ComponentStatus, RuntimeComponent

B1_ENDPOINT = os.getenv(
    "AETHER_B1_EMBEDDING_URL",
    "http://localhost:18081/v1/intercept",
)


class P2ObjectStoreAdapter:
    def __init__(self, legacy_runtime: Any) -> None:
        self._legacy_runtime = legacy_runtime

    async def put_text(
        self,
        *,
        text: str,
        object_key: str,
        context: RequestContext,
    ) -> dict[str, str]:
        ref = await self._legacy_runtime.client.put_object(object_key, text.encode("utf-8"))
        bucket = str(getattr(ref, "bucket", self._legacy_runtime.client.bucket))
        key = str(getattr(ref, "key", getattr(ref, "object_key", object_key)))
        return {
            "p2_bucket": bucket,
            "object_key": key,
            "content_ref": f"p2://{bucket}/{key}",
        }

    async def health(self) -> ComponentHealth:
        try:
            dimension = int(
                getattr(self._legacy_runtime.embedder, "dimension", None)
                or self._legacy_runtime.config.vector_dimension
            )
            await self._legacy_runtime.client.ensure_collection(dimension)
            return ComponentHealth(
                component=RuntimeComponent.P2,
                status=ComponentStatus.HEALTHY,
                detail="P2 gRPC collection check succeeded",
                critical=True,
            )
        except Exception as exc:
            return ComponentHealth(
                component=RuntimeComponent.P2,
                status=ComponentStatus.UNAVAILABLE,
                detail=f"{type(exc).__name__}: {exc}",
                critical=True,
            )


class P2VectorSearchAdapter:
    def __init__(
        self,
        legacy_runtime: Any,
        *,
        b1_endpoint: str = B1_ENDPOINT,
    ) -> None:
        self._legacy_runtime = legacy_runtime
        self._b1_endpoint = b1_endpoint

    async def search_memory(
        self,
        *,
        query: str,
        tenant_id: str,
        user_id: str,
        agent_id: str,
        limit: int,
        context: RequestContext,
        task_id: str | None = None,
    ) -> dict[str, Any]:
        b1_result = await asyncio.to_thread(
            B1EmbeddingServiceClient(self._b1_endpoint).process,
            {
                "text": query,
                "source_type": SourceType.DOCUMENT.value,
                "request_id": context.request_id,
                "trace_id": context.trace_id,
                "tenant_id": tenant_id,
                "source_id": f"b2-query-{uuid4().hex}",
                "metadata": {"agent_id": agent_id, "session_id": context.session_id},
                "input_type": "query",
            },
        )
        records = list(b1_result["records"])
        if len(records) != 1:
            raise ValueError("B1 returned more than one query vector")
        vector = list(records[0]["vector"])
        collection = p2_collection_for_scope(
            self._legacy_runtime.client.collection,
            tenant_id,
            user_id,
            agent_id,
            len(vector),
        )
        hits = await self._legacy_runtime.client.search_vectors(
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
