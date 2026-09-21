from __future__ import annotations

import asyncio
from typing import Any
from uuid import uuid4

from aether_agent_memory.b1.models import EmbeddingRecord
from aether_agent_memory.b2.b1_client import B1EmbeddingServiceClient
from aether_agent_memory.b2.p2_bridge import p2_collection_for_scope
from aether_agent_memory.core.enums import SourceType
from aether_agent_memory.core.memory import Memory
from aether_agent_memory.runtime.dtos import (
    MemorySearchHit,
    MemorySearchResult,
    ObjectReference,
)
from aether_agent_memory.runtime.request_context import RequestContext
from aether_agent_memory.runtime.status import ComponentHealth, ComponentStatus, RuntimeComponent


class P2ObjectStoreAdapter:
    def __init__(self, legacy_runtime: Any) -> None:
        self._legacy_runtime = legacy_runtime

    async def put_text(
        self,
        *,
        text: str,
        object_key: str,
        context: RequestContext,
    ) -> ObjectReference:
        ref = await self._legacy_runtime.client.put_object(object_key, text.encode("utf-8"))
        bucket = str(getattr(ref, "bucket", self._legacy_runtime.client.bucket))
        key = str(getattr(ref, "key", getattr(ref, "object_key", object_key)))
        return ObjectReference(
            provider="p2",
            namespace=bucket,
            object_key=key,
            content_ref=f"p2://{bucket}/{key}",
            metadata={"bucket": bucket},
        )

    async def read_text(self, *, content_ref: str, context: RequestContext) -> str | None:
        payload = await self.read_bytes(content_ref=content_ref, context=context)
        return payload.decode("utf-8") if payload is not None else None

    async def read_bytes(self, *, content_ref: str, context: RequestContext) -> bytes | None:
        prefix = "p2://"
        object_key = content_ref
        if content_ref.startswith(prefix):
            reference = content_ref.removeprefix(prefix)
            bucket, separator, object_key = reference.partition("/")
            if not separator or bucket != str(self._legacy_runtime.client.bucket):
                return None
        payload = await self._legacy_runtime.client.get_object(object_key)
        return bytes(payload) if payload is not None else None

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
        b1_endpoint: str,
    ) -> None:
        self._legacy_runtime = legacy_runtime
        self._b1_endpoint = b1_endpoint
        self._embedding_client = B1EmbeddingServiceClient(self._b1_endpoint)

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
    ) -> MemorySearchResult:
        b1_result = await asyncio.to_thread(
            self._embedding_client.process,
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
        items: list[MemorySearchHit] = []
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
                MemorySearchHit(
                    memory_id=metadata.get("memory_id"),
                    task_id=metadata.get("task_id"),
                    chunk_id=hit.id,
                    text=metadata.get("chunk_text", ""),
                    score=hit.score,
                    tenant_id=metadata.get("tenant_id"),
                    user_id=metadata.get("user_id"),
                    agent_id=metadata.get("agent_id"),
                    category=metadata.get("category", "other"),
                    keywords=metadata.get("keywords", []),
                    content_ref=metadata.get("content_ref"),
                    trace_id=metadata.get("trace_id"),
                    source_revision=metadata.get("source_revision"),
                )
            )
            if len(items) == limit:
                break
        return MemorySearchResult(
            items=items,
            backend="p2-e1",
            provider="p2",
            namespace=collection,
            query_model=records[0].get("embedding_model"),
            query_dimension=len(vector),
            metadata={"collection": collection},
        )


class P2VectorIndexAdapter:
    """Provider adapter for writing an already-derived Memory vector to P2."""

    def __init__(self, legacy_runtime: Any) -> None:
        self._legacy_runtime = legacy_runtime

    async def upsert_memory(self, memory: Memory, context: RequestContext) -> None:
        if memory.embedding is None:
            raise ValueError("cannot index a memory without an embedding")
        tenant_id = memory.tenant_id or ""
        user_id = memory.user_id or ""
        collection = p2_collection_for_scope(
            self._legacy_runtime.client.collection,
            tenant_id,
            user_id,
            memory.agent_id,
            len(memory.embedding),
        )
        record = EmbeddingRecord(
            request_id=context.request_id,
            trace_id=context.trace_id,
            source_id=memory.source_id or memory.id,
            object_id=memory.object_id,
            chunk_id=memory.id,
            chunk_text=memory.content,
            vector=memory.embedding,
            embedding_model=str(memory.metadata.get("embedding_model", "unknown")),
            metadata={
                "memory_id": memory.id,
                "tenant_id": tenant_id,
                "user_id": user_id,
                "agent_id": memory.agent_id,
                "session_id": memory.session_id,
                "task_id": memory.task_id,
                "category": memory.type.value,
                "keywords": memory.metadata.get("keywords", []),
                "content_ref": memory.metadata.get("content_ref"),
                "source_revision": memory.revision,
            },
        )
        await self._legacy_runtime.client.upsert_vectors(
            [record],
            collection=collection,
        )
