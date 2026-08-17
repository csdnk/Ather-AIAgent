"""Milvus projection for B2 asynchronous memory chunks."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from threading import Lock
from uuid import NAMESPACE_URL, uuid5

from aether_agent_memory.core.memory import Memory


@dataclass(frozen=True)
class MemoryHit:
    memory_id: str
    chunk_id: str
    text: str
    score: float
    tenant_id: str
    user_id: str
    category: str
    keywords: list[str]


class MilvusMemoryStore:
    def __init__(
        self,
        uri: str,
        *,
        collection_name: str = "b2_memory_chunks_v3",
        dimension: int = 512,
    ) -> None:
        if dimension <= 0:
            raise ValueError("Milvus vector dimension must be positive")
        self._uri = uri
        self._collection_name = collection_name
        self._dimension = dimension
        self._cached_collection: Any | None = None
        self._collection_lock = Lock()

    def _collection(self) -> Any:
        if self._cached_collection is not None:
            return self._cached_collection
        with self._collection_lock:
            if self._cached_collection is not None:
                return self._cached_collection
            self._cached_collection = self._build_collection()
            return self._cached_collection

    def _build_collection(self) -> Any:
        from pymilvus import (
            Collection,
            CollectionSchema,
            DataType,
            FieldSchema,
            connections,
            utility,
        )

        connections.connect(alias="b2", uri=self._uri)
        if not utility.has_collection(self._collection_name, using="b2"):
            schema = CollectionSchema(
                [
                    FieldSchema("id", DataType.VARCHAR, is_primary=True, max_length=64),
                    FieldSchema("memory_id", DataType.VARCHAR, max_length=64),
                    FieldSchema("chunk_id", DataType.VARCHAR, max_length=256),
                    FieldSchema("task_id", DataType.VARCHAR, max_length=64),
                    FieldSchema("tenant_id", DataType.VARCHAR, max_length=128),
                    FieldSchema("user_id", DataType.VARCHAR, max_length=128),
                    FieldSchema("agent_id", DataType.VARCHAR, max_length=128),
                    FieldSchema("session_id", DataType.VARCHAR, max_length=128),
                    FieldSchema("source_id", DataType.VARCHAR, max_length=256),
                    FieldSchema("content_ref", DataType.VARCHAR, max_length=512),
                    FieldSchema("category", DataType.VARCHAR, max_length=64),
                    FieldSchema("keywords", DataType.VARCHAR, max_length=1024),
                    FieldSchema("text", DataType.VARCHAR, max_length=65535),
                    FieldSchema("embedding", DataType.FLOAT_VECTOR, dim=self._dimension),
                ],
                description="B2 asynchronous long-text memory chunks",
            )
            collection = Collection(self._collection_name, schema=schema, using="b2")
            collection.create_index(
                "embedding",
                {"index_type": "FLAT", "metric_type": "COSINE", "params": {}},
            )
        else:
            collection = Collection(self._collection_name, using="b2")
        collection.load()
        return collection

    def upsert_b1_records(
        self,
        *,
        task_id: str,
        memory_id: str,
        tenant_id: str,
        user_id: str,
        agent_id: str,
        session_id: str,
        source_id: str,
        content_ref: str,
        records: list[dict[str, Any]],
        category: str,
        keywords: list[str],
        flush: bool = True,
    ) -> int:
        if not records:
            raise ValueError("B1 returned no records")
        vectors = [list(record["vector"]) for record in records]
        if any(len(vector) != self._dimension for vector in vectors):
            raise ValueError(
                f"expected {self._dimension}-dimension B1 vectors; "
                "align AETHER_B2_VECTOR_DIMENSION with the B1 model"
            )
        collection = self._collection()
        rows = [
            [str(uuid5(NAMESPACE_URL, f"{task_id}:{record['chunk_id']}")) for record in records],
            [memory_id] * len(records),
            [str(record["chunk_id"]) for record in records],
            [task_id] * len(records),
            [tenant_id] * len(records),
            [user_id] * len(records),
            [agent_id] * len(records),
            [session_id] * len(records),
            [source_id] * len(records),
            [content_ref] * len(records),
            [category] * len(records),
            [",".join(keywords)] * len(records),
            [str(record["chunk_text"]) for record in records],
            vectors,
        ]
        collection.upsert(rows)
        if flush:
            collection.flush()
        return len(records)

    def search(
        self,
        *,
        vector: list[float],
        tenant_id: str,
        user_id: str,
        agent_id: str,
        limit: int = 5,
    ) -> list[MemoryHit]:
        if len(vector) != self._dimension:
            raise ValueError(f"expected a {self._dimension}-dimension query vector")
        collection = self._collection()
        expression = " && ".join(
            (
                f'tenant_id == "{self._escape(tenant_id)}"',
                f'user_id == "{self._escape(user_id)}"',
                f'agent_id == "{self._escape(agent_id)}"',
            )
        )
        result = collection.search(
            [vector],
            "embedding",
            {"metric_type": "COSINE", "params": {}},
            limit=max(1, min(limit, 100)),
            expr=expression,
            output_fields=[
                "memory_id",
                "chunk_id",
                "text",
                "tenant_id",
                "user_id",
                "category",
                "keywords",
            ],
        )
        return [
            MemoryHit(
                memory_id=str(hit.entity.get("memory_id")),
                chunk_id=str(hit.entity.get("chunk_id")),
                text=str(hit.entity.get("text")),
                score=float(hit.score),
                tenant_id=str(hit.entity.get("tenant_id")),
                user_id=str(hit.entity.get("user_id")),
                category=str(hit.entity.get("category")),
                keywords=[word for word in str(hit.entity.get("keywords")).split(",") if word],
            )
            for hit in result[0]
        ]

    def upsert_memory(self, memory: Memory) -> None:
        """Index one semantic memory while keeping its full payload in Redis."""
        if memory.embedding is None:
            raise ValueError("semantic memory must have an embedding before Milvus indexing")
        self.upsert_b1_records(
            task_id=memory.task_id or memory.id,
            memory_id=memory.id,
            tenant_id=memory.tenant_id or "",
            user_id=memory.user_id or "",
            agent_id=memory.agent_id,
            session_id=memory.session_id,
            source_id=memory.source_id or memory.id,
            content_ref=memory.object_id or memory.id,
            records=[{"chunk_id": memory.id, "chunk_text": memory.content, "vector": memory.embedding}],
            category="semantic_memory",
            keywords=memory.tags,
            flush=False,
        )

    def flush(self) -> None:
        self._collection().flush()

    def delete_memory(self, memory_id: str) -> None:
        collection = self._collection()
        collection.delete(f'memory_id == "{self._escape(memory_id)}"')
        collection.flush()

    def delete_memories(self, memory_ids: list[str]) -> None:
        if not memory_ids:
            return
        expression = " || ".join(
            f'memory_id == "{self._escape(memory_id)}"' for memory_id in memory_ids
        )
        self._collection().delete(expression)
        self._collection().flush()

    @staticmethod
    def _escape(value: str) -> str:
        return value.replace("\\", "\\\\").replace('"', '\\"')
