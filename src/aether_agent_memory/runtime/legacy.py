"""Composition root for the persistent P3 service."""

from __future__ import annotations

import os
from collections.abc import Awaitable
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from aether_agent_memory.b1 import (
    EmbeddingPipeline,
    EmbeddingRequest,
    EmbeddingResult,
    TextChunker,
)
from aether_agent_memory.b1.sidecar_client import SidecarEmbeddingClient
from aether_agent_memory.b2 import MemoryEvent, MemoryService
from aether_agent_memory.b2.milvus_store import MilvusMemoryStore
from aether_agent_memory.b3 import (
    HeuristicScheduler,
    P2MigrationExecutor,
    ScheduleRequest,
    ScheduleRunResult,
)
from aether_agent_memory.context import ContextPack, ContextRequest, MockContextPackBuilder
from aether_agent_memory.core.enums import SourceType, StorageTier
from aether_agent_memory.core.memory import Memory
from aether_agent_memory.episodic import MockEpisodicMemoryManager
from aether_agent_memory.mocks.embedding import MockEmbeddingClient
from aether_agent_memory.p2 import P2GrpcClient, P2StorageClient, P2VectorSink
from aether_agent_memory.persistence import RedisMemoryStore, SQLiteMemoryStore
from aether_agent_memory.semantic import MockSemanticMemoryManager
from aether_agent_memory.signal import MockSignalEmitter, RedisSignalEmitter
from aether_agent_memory.working import MockWorkingMemoryManager

if TYPE_CHECKING:
    from aether_agent_memory.config.app_settings import AppSettings


@dataclass(frozen=True)
class P3RuntimeConfig:
    p2_endpoint: str
    data_dir: Path
    p2_engine: str = "object/default"
    p2_bucket: str = "p3-memory"
    p2_collection: str = "p3"
    p2_timeout_seconds: float = 10.0
    memory_store: str = "redis"
    redis_url: str = "redis://localhost:6379/0"
    milvus_uri: str = ""
    milvus_collection: str = "b2_memory_chunks_v3"
    vector_dimension: int = 512
    b1_sidecar_url: str = ""
    b1_embedding_url: str = ""
    b1_tenant_id: str = "p3-runtime"
    b1_model_name: str = "BAAI/bge-small-zh-v1.5"
    b1_sidecar_timeout_seconds: float = 120.0
    b1_max_batch_items: int = 32
    b1_chunk_max_chars: int = 400
    b1_chunk_overlap_chars: int = 40
    broker_url: str = ""
    result_backend: str = ""
    task_status_url: str = ""
    b3_shadow_mode: bool = True

    @classmethod
    def from_environment(cls) -> P3RuntimeConfig:
        return cls(
            p2_endpoint=os.getenv("AETHER_P2_GRPC", "localhost:50052"),
            data_dir=Path(os.getenv("AETHER_P3_DATA_DIR", "/tmp/aether-p3")),
            p2_engine=os.getenv("AETHER_P2_ENGINE", "object/default"),
            p2_bucket=os.getenv("AETHER_P2_BUCKET", "p3-memory"),
            p2_collection=os.getenv("AETHER_P2_COLLECTION", "p3"),
            p2_timeout_seconds=float(os.getenv("AETHER_P2_TIMEOUT_SECONDS", "10")),
            memory_store=os.getenv("AETHER_B2_MEMORY_STORE", "redis").lower(),
            redis_url=os.getenv("AETHER_B2_REDIS_URL", "redis://localhost:6379/0"),
            milvus_uri=os.getenv("AETHER_B2_MILVUS_URI", ""),
            milvus_collection=os.getenv("AETHER_B2_MILVUS_COLLECTION", "b2_memory_chunks_v3"),
            vector_dimension=int(os.getenv("AETHER_B2_VECTOR_DIMENSION", "512")),
            b1_sidecar_url=os.getenv("AETHER_B1_SIDECAR_URL", "").strip(),
            b1_embedding_url=os.getenv("AETHER_B1_EMBEDDING_URL", "").strip(),
            b1_tenant_id=os.getenv("AETHER_B1_TENANT_ID", "p3-runtime"),
            b1_model_name=os.getenv("AETHER_B1_MODEL_NAME", "BAAI/bge-small-zh-v1.5"),
            b1_sidecar_timeout_seconds=float(
                os.getenv("AETHER_B1_SIDECAR_TIMEOUT_SECONDS", "120")
            ),
            b1_max_batch_items=int(os.getenv("AETHER_B1_MAX_BATCH_ITEMS", "32")),
            b1_chunk_max_chars=int(os.getenv("AETHER_B1_CHUNK_MAX_CHARS", "400")),
            b1_chunk_overlap_chars=int(os.getenv("AETHER_B1_CHUNK_OVERLAP_CHARS", "40")),
            broker_url=os.getenv("AETHER_B2_BROKER_URL", "").strip(),
            result_backend=os.getenv("AETHER_B2_RESULT_BACKEND", "").strip(),
            task_status_url=os.getenv("AETHER_B2_TASK_STATUS_URL", "").strip(),
            b3_shadow_mode=os.getenv("AETHER_B3_SHADOW_MODE", "true").lower()
            in {"1", "true", "yes"},
        )

    @classmethod
    def from_settings(cls, settings: AppSettings) -> P3RuntimeConfig:
        """Build the legacy runtime config from resolved host settings.

        This keeps ``AppSettings`` as the single source of truth while the
        legacy runtime is still used behind the P3 facade.
        """
        return cls(
            p2_endpoint=settings.p2_endpoint,
            data_dir=Path(settings.data_dir),
            p2_engine=settings.p2_engine,
            p2_bucket=settings.p2_bucket,
            p2_collection=settings.p2_collection,
            p2_timeout_seconds=settings.p2_timeout_seconds,
            memory_store=settings.memory_store,
            redis_url=settings.redis_url,
            milvus_uri=settings.milvus_uri,
            milvus_collection=settings.milvus_collection,
            vector_dimension=settings.vector_dimension,
            b1_sidecar_url=settings.b1_sidecar_url,
            b1_embedding_url=settings.b1_embedding_url,
            b1_tenant_id=settings.b1_tenant_id,
            b1_model_name=settings.b1_model_name,
            b1_sidecar_timeout_seconds=settings.b1_sidecar_timeout_seconds,
            b1_max_batch_items=settings.b1_max_batch_items,
            b1_chunk_max_chars=settings.b1_chunk_max_chars,
            b1_chunk_overlap_chars=settings.b1_chunk_overlap_chars,
            broker_url=settings.broker_url,
            result_backend=settings.result_backend,
            task_status_url=settings.task_status_url,
            b3_shadow_mode=settings.b3_shadow_mode,
        )


class P3Runtime:
    """B1/B2/B3 application composition backed by the P2 gRPC service."""

    def __init__(self, config: P3RuntimeConfig) -> None:
        self.config = config
        self.client = P2GrpcClient(
            config.p2_endpoint,
            bucket=config.p2_bucket,
            collection=config.p2_collection,
            timeout_seconds=config.p2_timeout_seconds,
        )
        sidecar_url = config.b1_sidecar_url.strip()
        if sidecar_url:
            self.embedder: Any = SidecarEmbeddingClient(
                sidecar_url,
                tenant_id=config.b1_tenant_id,
                timeout_seconds=config.b1_sidecar_timeout_seconds,
                max_batch_items=config.b1_max_batch_items,
            )
            self.embedding_model = config.b1_model_name
            self.pipeline = EmbeddingPipeline(
                embedder=self.embedder,
                sink=P2VectorSink(self.client),
                chunker=TextChunker(
                    max_chars=config.b1_chunk_max_chars,
                    overlap_chars=config.b1_chunk_overlap_chars,
                ),
                model_name=self.embedding_model,
            )
            self.embedding_path = "B1 Sidecar -> EmbeddingPipeline -> P2VectorSink"
        else:
            self.embedder = MockEmbeddingClient(dim=32)
            self.embedding_model = "mock-sha256-32"
            self.pipeline = EmbeddingPipeline(
                embedder=self.embedder,
                sink=P2VectorSink(self.client),
                model_name=self.embedding_model,
            )
            self.embedding_path = "EmbeddingPipeline -> MockEmbeddingClient -> P2VectorSink"
        if config.memory_store == "sqlite":
            store: Any = SQLiteMemoryStore(config.data_dir / "memory.db")
        else:
            store = RedisMemoryStore(config.redis_url)
        working = MockWorkingMemoryManager(store=store)
        episodic = MockEpisodicMemoryManager(embedder=self.embedder, store=store)
        vector_store = None
        embedder_dimension = int(getattr(self.embedder, "dimension", 0) or 0)
        if config.milvus_uri and embedder_dimension == config.vector_dimension:
            vector_store = MilvusMemoryStore(
                config.milvus_uri,
                collection_name=config.milvus_collection,
                dimension=config.vector_dimension,
            )
        semantic = MockSemanticMemoryManager(
            embedder=self.embedder,
            store=store,
            vector_store=vector_store,
        )
        self.signal_emitter = (
            RedisSignalEmitter(config.redis_url)
            if config.memory_store == "redis"
            else MockSignalEmitter()
        )
        self.memory = MemoryService(
            working=working,
            episodic=episodic,
            semantic=semantic,
            builder=MockContextPackBuilder(
                working=working,
                episodic=episodic,
                semantic=semantic,
            ),
            emitter=self.signal_emitter,
        )
        self.scheduler = HeuristicScheduler(
            executor=P2MigrationExecutor(
                self.client,
                default_engine=config.p2_engine,
                default_segment_id=config.p2_bucket,
            ),
            shadow_mode=config.b3_shadow_mode,
        )
        self.executor_name = type(self.scheduler.executor).__name__

    async def close(self) -> None:
        close_store = getattr(self.memory, "close_store", None)
        if close_store is not None:
            await close_store()
        close_emitter = getattr(self.signal_emitter, "close", None)
        if close_emitter is not None:
            await close_emitter()
        close_embedder = getattr(self.embedder, "close", None)
        if close_embedder is not None:
            await close_embedder()
        await self.client.close()

    async def health(self) -> bool:
        try:
            ensure_ready = getattr(self.embedder, "ensure_ready", None)
            if ensure_ready is not None:
                await ensure_ready()
            dimension = int(getattr(self.embedder, "dimension", None) or 32)
            await self.client.ensure_collection(dimension)
        except Exception:
            return False
        return True

    async def embed(self, request: EmbeddingRequest) -> EmbeddingResult:
        return await self.pipeline.process(request)

    async def ingest_memory(self, event: MemoryEvent) -> Memory:
        return await self.memory.ingest(event)

    async def build_context(self, request: ContextRequest) -> ContextPack:
        return await self.memory.before_inference(request)

    async def schedule_once(self, request: ScheduleRequest) -> ScheduleRunResult:
        return await self.scheduler.run_once(request)

    async def run_knowledge_session(
        self,
        *,
        title: str,
        content: str,
        user_message: str,
        session_id: str = "dashboard-session",
        agent_id: str = "knowledge-assistant",
        user_id: str = "dashboard-user",
        tenant_id: str = "dashboard-tenant",
    ) -> dict[str, Any]:
        """Execute one traceable knowledge-upload and conversation scenario."""
        request_id = uuid4().hex
        trace_id = uuid4().hex
        events: list[dict[str, Any]] = []

        async def measure(
            step: int,
            title: str,
            component: str,
            operation: Awaitable[Any],
            details: dict[str, Any],
        ) -> Any:
            started = perf_counter()
            result = await operation
            events.append(
                {
                    "step": step,
                    "title": title,
                    "component": component,
                    "status": "success",
                    "latency_ms": round((perf_counter() - started) * 1000, 2),
                    "details": details,
                }
            )
            return result

        storage = P2StorageClient(self.client)
        object_key = f"uploads/{uuid4().hex}.txt"
        content_bytes = content.encode("utf-8")
        reference = await measure(
            1,
            "Knowledge document stored",
            "P2 E2 ObjectService",
            storage.put(object_key, content_bytes),
            {
                "grpc_service": "ObjectService",
                "rpc": "CreateBucket -> PutObject",
                "bucket": self.client.bucket,
                "object_key": object_key,
                "segment_id": f"object/{self.client.bucket}",
                "tier": StorageTier.L3_OBJECT.value,
                "bytes": len(content_bytes),
            },
        )
        embedding = await measure(
            2,
            "Document embedded",
            "P3 B1 EmbeddingPipeline",
            self.embed(
                EmbeddingRequest(
                    text=content,
                    source_type=SourceType.DOCUMENT,
                    request_id=request_id,
                    trace_id=trace_id,
                    source_id=title,
                    object_id=reference.object_key,
                    tenant_id=tenant_id,
                )
            ),
            {
                "pipeline": self.embedding_path,
                "embedding_model": self.embedding_model,
                "source_id": title,
                "object_key": reference.object_key,
            },
        )
        if not embedding.records:
            raise RuntimeError(embedding.error_message or "B1 embedding failed")
        events[-1]["details"].update(
            {"vector_count": len(embedding.records), "dimension": len(embedding.records[0].vector)}
        )
        events.append(
            {
                "step": 3,
                "title": "Vectors indexed",
                "component": "P2 E1 VectorService",
                "status": "success",
                "latency_ms": embedding.latency_ms,
                "details": {
                    "grpc_service": "VectorService",
                    "rpc": "CreateCollection -> InsertVector",
                    "collection": self.client.collection,
                    "vector_count": len(embedding.records),
                    "dimension": len(embedding.records[0].vector),
                    "source_object": reference.object_key,
                },
            }
        )
        document_memory = await measure(
            4,
            "Knowledge memory created",
            "P3 B2 MemoryService",
            self.ingest_memory(
                MemoryEvent(
                    event_type="rag_result",
                    session_id=session_id,
                    agent_id=agent_id,
                    user_id=user_id,
                    tenant_id=tenant_id,
                    request_id=request_id,
                    trace_id=trace_id,
                    source_id=title,
                    object_id=reference.object_key,
                    source=SourceType.DOCUMENT,
                    content=f"Knowledge document {title}: {content}",
                    importance=0.9,
                )
            ),
            {
                "operation": "ingest RAG result",
                "memory_kind": "working",
                "persistence": "SQLiteMemoryStore",
                "object_key": reference.object_key,
            },
        )
        events[-1]["details"]["memory_id"] = document_memory.id
        user_memory = await measure(
            5,
            "User conversation remembered",
            "P3 B2 MemoryService",
            self.ingest_memory(
                MemoryEvent(
                    event_type="user_memory",
                    session_id=session_id,
                    agent_id=agent_id,
                    user_id=user_id,
                    tenant_id=tenant_id,
                    request_id=request_id,
                    trace_id=trace_id,
                    source=SourceType.USER,
                    content=user_message,
                    importance=0.95,
                    evidence_refs=[reference.object_key],
                )
            ),
            {
                "operation": "ingest user memory",
                "memory_kind": "semantic",
                "persistence": "SQLiteMemoryStore",
                "user_message": user_message,
            },
        )
        events[-1]["details"]["memory_id"] = user_memory.id
        context = await measure(
            6,
            "Memories recalled for conversation",
            "P3 B2 ContextPackBuilder",
            self.build_context(
                ContextRequest(
                    session_id=session_id,
                    agent_id=agent_id,
                    user_id=user_id,
                    tenant_id=tenant_id,
                    request_id=request_id,
                    trace_id=trace_id,
                    query=user_message,
                )
            ),
            {
                "operation": "before_inference",
                "builder": "MockContextPackBuilder",
                "query": user_message,
            },
        )
        events[-1]["details"].update(
            {"memory_count": len(context.memories), "memory_refs": context.memory_refs}
        )
        schedule = await measure(
            7,
            "Scheduling policy evaluated",
            f"P3 B3 HeuristicScheduler + {self.executor_name}",
            self.schedule_once(
                ScheduleRequest.model_validate(
                    {
                        "request_id": request_id,
                        "trace_id": trace_id,
                        "objects": [
                            {
                                "object_id": reference.object_key,
                                "object_type": "knowledge_document",
                                "current_tier": StorageTier.L3_OBJECT,
                                "size_bytes": len(content_bytes),
                                "tenant_id": tenant_id,
                                "access": {
                                    "access_frequency": 1.0,
                                    "recency_score": 1.0,
                                    "hit_rate": 1.0,
                                    "access_count": 1,
                                },
                                "semantic": {
                                    "semantic_relevance": 0.95,
                                    "importance": 0.9,
                                    "task_relevance": 0.95,
                                },
                                "business_priority": 0.85,
                                "metadata": {
                                    "p2_engine": self.config.p2_engine,
                                    "p2_segment_id": self.config.p2_bucket,
                                },
                            }
                        ],
                    }
                )
            ),
            {
                "scheduler": "HeuristicScheduler",
                "executor": self.executor_name,
                "p2_engine": self.config.p2_engine,
                "p2_segment_id": self.config.p2_bucket,
                "route_mode": "logical block route; physical byte mover remains external",
                "policy_version": "heuristic-v1",
                "trigger": "recent knowledge upload and relevant user question",
            },
        )
        action = schedule.actions[0]
        feedback = schedule.entries[0].feedback
        events[-1]["details"].update(
            {
                "action": action.action_type.value,
                "tier": (
                    f"{action.source_tier.value} -> "
                    f"{action.target_tier.value if action.target_tier else '-'}"
                ),
                "score": action.score,
                "execute_status": feedback.execute_status.value,
                "execute_latency_ms": feedback.execute_latency_ms,
                **feedback.metadata,
            }
        )
        return {
            "request_id": request_id,
            "trace_id": trace_id,
            "object_key": reference.object_key,
            "vector_count": len(embedding.records),
            "memories": [
                {"id": document_memory.id, "type": document_memory.type.value},
                {"id": user_memory.id, "type": user_memory.type.value},
            ],
            "context": context.model_dump(mode="json"),
            "action": action.model_dump(mode="json"),
            "feedback": feedback.model_dump(mode="json"),
            "events": events,
        }

    async def smoke(self) -> dict[str, Any]:
        return await self.run_knowledge_session(
            title="Persistent service smoke test",
            content="Aether P3 writes a traceable semantic vector into the P2 engine.",
            user_message="Which document is available for semantic recall?",
            session_id="p3-service-smoke",
            agent_id="p3-service-agent",
            tenant_id="integration",
        )
