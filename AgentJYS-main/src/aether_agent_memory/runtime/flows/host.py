"""Basic three-flow host. Explicit local profile; optional providers are injected."""

import asyncio
import json
import os
import secrets
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Any

from aether_agent_memory.operate.basic.executor import LocalCacheExecutor
from aether_agent_memory.operate.basic.service import Operate
from aether_agent_memory.recall.basic.adapters import SPACE, LexicalEmbedding
from aether_agent_memory.recall.basic.config import RecallSettings
from aether_agent_memory.recall.basic.reranking import CrossEncoderReranker, Reranker
from aether_agent_memory.recall.basic.service import Recall
from aether_agent_memory.recall.basic.tokenization import ModelTokenizer, TokenCounter
from aether_agent_memory.recall.basic.vector_search import SearchAccess
from aether_agent_memory.recall.contracts.foundation import EmbeddingSpace
from aether_agent_memory.recall.contracts.ports import EmbeddingPort, GenerationSearchPort
from aether_agent_memory.remember.basic.extraction import LiteralExtraction
from aether_agent_memory.remember.basic.projection import ProjectionAccess
from aether_agent_memory.remember.basic.service import Remember
from aether_agent_memory.remember.contracts.models import ExtractionRequest, SourceRef
from aether_agent_memory.remember.contracts.ports import (
    ExtractionPort,
    MemoryContextGuardPort,
    MemoryFoundationPort,
    MemoryQualificationPort,
    MemoryReadPort,
)
from aether_agent_memory.runtime.contracts.models import TrustedContext
from aether_agent_memory.runtime.foundation.common import fingerprint, now
from aether_agent_memory.runtime.foundation.host import Foundation
from aether_agent_memory.runtime.foundation.requests import text_hash
from aether_agent_memory.runtime.foundation.telemetry import attach_provider

from .health import Health, Probe, sqlite_probe, storage_probe
from .vector_adapters import MilvusVectors, SQLiteVectors, VectorBackend

if TYPE_CHECKING:
    from aether_agent_memory.recall.embedding.p3 import NativeP3Embedding


class ThreeFlows:
    def __init__(
        self,
        database: str | Path,
        cache_root: str | Path,
        *,
        extraction: ExtractionPort | None = None,
        embedding: EmbeddingPort | None = None,
        vectors: VectorBackend | None = None,
        model_space: str | None = None,
        embedding_profile: str = "native",
        embedding_config: str | Path | None = None,
        recall_config: str | Path | None = None,
        recall_settings: RecallSettings | None = None,
        reranker: Reranker | None = None,
        tokenizer: TokenCounter | None = None,
        log_path: str | Path | None = None,
        log_retention_days: int = 14,
        log_max_records: int = 200_000,
        maintenance_principals: tuple[str, ...] = (),
        backup_root: str | Path | None = None,
        remember_factory: Any = None,
        operate_factory: Any = None,
        postgres_dsn: str | None = None,
    ) -> None:
        self.recall_settings = recall_settings or (
            RecallSettings.model_validate(
                json.loads(Path(recall_config).read_text(encoding="utf-8"))
            )
            if recall_config
            else RecallSettings()
        )
        selected_tokenizer = tokenizer or ModelTokenizer(
            self.recall_settings.tokenizer, self.recall_settings.tokenizer_path
        )
        self.foundation = Foundation(
            database,
            log_path=log_path,
            log_retention_days=log_retention_days,
            log_max_records=log_max_records,
            maintenance_principals=maintenance_principals,
            backup_root=backup_root,
            postgres_dsn=postgres_dsn,
        )
        self.owned_vectors = None
        self.owned_reranker = None
        self.worker_prefix = "flow_" + secrets.token_hex(6)
        self.closed = False
        self.execution: Any = None
        self.native_embedding: NativeP3Embedding | None = None
        if embedding_profile not in {"native", "lexical"}:
            self.foundation.close()
            raise ValueError("unknown embedding profile")
        try:
            if embedding is None and embedding_profile == "native":
                from aether_agent_memory.recall.embedding.p3 import (
                    NativeP3Embedding as NativeAdapter,
                )

                self.native_embedding = NativeAdapter.from_config(
                    self.foundation.uow, self.foundation.identity, embedding_config
                )
                embedding = self.native_embedding
            self.embedding = embedding or LexicalEmbedding()
            selected_space = getattr(self.embedding, "model_space", None)
            self.model_space = model_space or selected_space or SPACE
            if selected_space and self.model_space != selected_space:
                raise ValueError("configured model space differs from loaded provider")
            dimensions = getattr(self.embedding, "dimensions", None)
            if vectors is None and dimensions is None:
                raise ValueError(
                    "injected embedding needs dimensions or an explicit vector adapter"
                )
            if vectors is None and self.recall_settings.milvus_uri:
                self.owned_vectors = MilvusVectors(
                    self.foundation.uow,
                    self.foundation.identity,
                    self.model_space,
                    int(dimensions or 0),
                    uri=self.recall_settings.milvus_uri,
                    collection=self.recall_settings.milvus_collection,
                    token=os.environ.get(self.recall_settings.milvus_token_env, ""),
                    serialize_writes=self.recall_settings.milvus_serialize_writes,
                )
                vectors = self.owned_vectors
            self.vectors = vectors or SQLiteVectors(
                self.foundation.uow,
                self.foundation.identity,
                self.model_space,
                int(dimensions or 0),
            )
            with self.foundation.uow.transaction() as tx:
                prior = tx.read("settings", "p3_embedding_binding")
                if prior and prior["retrieval_space_ref"] != self.model_space:
                    raise ValueError("database is bound to another embedding model space")
                vector_binding = (
                    {
                        "provider": "milvus",
                        "endpoint_hash": fingerprint(self.recall_settings.milvus_uri),
                        "collection": self.recall_settings.milvus_collection,
                    }
                    if self.owned_vectors
                    else {"provider": "sqlite"}
                    if type(self.vectors) is SQLiteVectors
                    else {"provider": "injected"}
                )
                old_binding = tx.read("settings", "p3_vector_binding")
                if old_binding is None and tx.rows("recall_vectors"):
                    old_binding = {"provider": "sqlite"}
                if old_binding and old_binding != vector_binding:
                    raise ValueError(
                        "vector backend changed; explicit reindex or fresh database required"
                    )
                tx.write("settings", "p3_vector_binding", vector_binding)
        except BaseException:
            if self.owned_vectors:
                self.owned_vectors.close()
            if self.native_embedding:
                self.native_embedding.close()
            self.foundation.close()
            raise
        self.embedding_profile = (
            "native" if self.native_embedding else "injected" if embedding else "lexical"
        )
        self.projections = ProjectionAccess(self.vectors)
        self.vector_search = SearchAccess(self.vectors)
        self.remember = (remember_factory or Remember)(
            self.foundation.uow,
            self.foundation.identity,
            self.foundation.tasks,
            self.foundation.events,
            extraction or LiteralExtraction(),
            self.embedding,
            self.projections,
            self.model_space,
            **({"tokenizer": selected_tokenizer} if remember_factory else {}),
        )
        if reranker is None and self.recall_settings.rerank_policy != "disabled":
            self.owned_reranker = CrossEncoderReranker(
                str(self.recall_settings.reranker_model),
                revision=self.recall_settings.reranker_revision,
                cache=self.recall_settings.reranker_cache,
                max_length=self.recall_settings.rerank_max_length,
            )
            reranker = self.owned_reranker
        self.recall = Recall(
            self.foundation.uow,
            self.foundation.identity,
            self.foundation.events,
            self.remember,
            self.embedding,
            self.vector_search,
            self.model_space,
            settings=self.recall_settings,
            tokenizer=selected_tokenizer,
            reranker=reranker,
        )
        self.executor = LocalCacheExecutor(cache_root)
        self.operate = (operate_factory or Operate)(
            self.foundation.uow,
            self.foundation.identity,
            self.foundation.tasks,
            self.foundation.events,
            self.remember,
            self.remember,
            self.executor,
        )
        self.last_period = ""
        for provider, name in (
            (self.remember.extraction, "extraction"),
            (self.embedding, "embedding"),
            (self.vectors, "vectors"),
            (self.executor, "executor"),
        ):
            attach_provider(provider, self.foundation.telemetry, name)
        if reranker:
            attach_provider(reranker, self.foundation.telemetry, "reranker")
        self.health = Health(self)

        async def database_probe(ctx: TrustedContext) -> dict[str, object]:
            return await asyncio.to_thread(storage_probe, self.foundation.uow, write=True)

        async def log_probe(ctx: TrustedContext) -> dict[str, object]:
            return await asyncio.to_thread(storage_probe, self.foundation.telemetry, write=True)

        async def executor_probe(ctx: TrustedContext) -> dict[str, object]:
            def check() -> dict[str, object]:
                result = sqlite_probe(self.executor.root / "executor.db", write=True)
                # Dedicated temporary probe files; no action or memory is changed.
                for tier in ("cold", "warm", "hot"):
                    with tempfile.TemporaryFile(dir=self.executor.root / tier) as file:
                        file.write(b"p3-health")
                        file.flush()
                        os.fsync(file.fileno())
                        file.seek(0)
                        if file.read() != b"p3-health":
                            raise OSError("cache probe mismatch")
                return result

            return await asyncio.to_thread(check)

        async def extraction_probe(ctx: TrustedContext) -> dict[str, object]:
            if type(self.remember.extraction) is LiteralExtraction:
                result = await self.remember.extraction.extract(
                    ctx,
                    ExtractionRequest(
                        source=SourceRef(
                            source_id="health_probe",
                            source_version=1,
                            content_hash=text_hash("health probe"),
                            locator="health",
                        ),
                        text="health probe",
                        existing=(),
                        policy_version="health_v1",
                    ),
                )
                return {"state": "available" if len(result.candidates) == 1 else "unavailable"}
            return {"state": "unknown"}

        async def embedding_probe(ctx: TrustedContext) -> dict[str, object]:
            if self.native_embedding:
                return await self.native_embedding.health(ctx)
            if type(self.embedding) is LexicalEmbedding:
                vector = self.embedding.features("健康检测")
                return {"state": "available" if len(vector) == 256 else "unavailable"}
            return {"state": "unknown"}

        async def vector_probe(ctx: TrustedContext) -> dict[str, object]:
            if self.owned_vectors:
                return await self.owned_vectors.health(ctx)
            if type(self.vectors) is SQLiteVectors:
                if not self.vectors.available:
                    return {"state": "unavailable"}
                return await asyncio.to_thread(storage_probe, self.vectors.uow)
            return {"state": "unknown"}

        async def reranker_probe(ctx: TrustedContext) -> dict[str, object]:
            if self.owned_reranker:
                return await self.owned_reranker.health(ctx)
            return {
                "state": "disabled"
                if self.recall_settings.rerank_policy == "disabled"
                else "unknown"
            }

        async def tokenizer_probe(ctx: TrustedContext) -> dict[str, object]:
            return {
                "state": "available"
                if self.recall.tokenizer.count("健康检测") > 0
                else "unavailable",
                "tokenizer_id": self.recall.tokenizer.identifier,
            }

        for name, probe in (
            ("database", database_probe),
            ("logs", log_probe),
            ("executor", executor_probe),
            ("extraction", extraction_probe),
            ("embedding", embedding_probe),
            ("vectors", vector_probe),
            ("reranker", reranker_probe),
            ("tokenizer", tokenizer_probe),
        ):
            self.health.register(name, probe)

        if remember_factory is not None:
            remember_factory.attach(self)

    def enable_generation_recall(
        self,
        *,
        memories: MemoryReadPort,
        qualification: MemoryQualificationPort,
        bodies: MemoryFoundationPort,
        guards: MemoryContextGuardPort,
        space: EmbeddingSpace,
        search: GenerationSearchPort | None = None,
        probe: Probe | None = None,
    ) -> None:
        # 仅在启动装配时启用新流程：调用方必须提供完整 B 接口及匹配的模型空间。
        # 存在运行中请求时拒绝切换，防止同一请求跨越两套组包和授权策略。
        """Bootstrap-only opt-in. B providers are required; no fake compatibility stamps."""
        from aether_agent_memory.recall.basic.candidates import MemoryCandidates
        from aether_agent_memory.recall.basic.generation import GenerationRecall
        from aether_agent_memory.recall.basic.generation_search import SQLiteGenerationSearch
        from aether_agent_memory.recall.embedding.spaces import EmbeddingSpaces

        if isinstance(self.recall, GenerationRecall):
            raise ValueError("generation Recall is already configured")
        # 这里检查接口齐备性；B 的业务正确性仍需消费者契约测试与真实联调验证。
        required = (
            (memories, ("load", "working", "final_guard", "projection_readiness")),
            (qualification, ("qualify",)),
            (bodies, ("load_bodies",)),
            (guards, ("relations", "revalidate_context")),
        )
        if any(
            not callable(getattr(provider, name, None))
            for provider, names in required
            for name in names
        ):
            raise ValueError("new Recall requires complete B provider contracts")
        if space.model_space != self.model_space or (
            self.native_embedding is not None and space != self.native_embedding.space
        ):
            raise ValueError("new Recall space must match the configured embedding")
        with self.foundation.uow.transaction() as tx:
            if any(
                row["record"]["state"] in {"accepted", "running"}
                for _, row in tx.rows("recall_requests")
            ):
                raise ValueError("cannot switch Recall while requests are running")
        if search is None and isinstance(self.vectors, MilvusVectors):
            from aether_agent_memory.recall.basic.milvus_generation import MilvusGenerationSearch

            search = MilvusGenerationSearch(self.vectors)
        vectors = search or SQLiteGenerationSearch(self.foundation.uow, self.foundation.identity)
        candidates = MemoryCandidates(
            self.foundation.uow,
            self.foundation.identity,
            self.embedding,
            vectors,
            qualification,
            EmbeddingSpaces((space,)),
        )
        self.recall = GenerationRecall(self.recall, candidates, bodies, guards)
        self.recall.memories = memories
        for provider, name in (
            (vectors, "generation_search"),
            (candidates, "memory_candidates"),
            (self.recall.assembly, "context_assembly"),
        ):
            attach_provider(provider, self.foundation.telemetry, name)

        async def unknown_probe(ctx: TrustedContext) -> dict[str, object]:
            # 接口可调用不代表 B 集成健康；未提供真实探针时如实报告 unknown。
            return {"state": "unknown", "reason": "b_integration_probe_not_supplied"}

        self.health.register("recall_generation", probe or unknown_probe)

    async def tick(
        self,
        *,
        periodic: bool = False,
        maintenance_context: TrustedContext | None = None,
        run_tasks: bool = True,
        periodic_interval: float = 5,
    ) -> bool:
        execution = getattr(self, "execution", None)
        if execution is None:
            raise RuntimeError("configure the Temporal service before draining P3 work")
        await execution.drain()
        return True

    async def drain(self, timeout_seconds: float = 12) -> None:
        execution = getattr(self, "execution", None)
        if execution is None:
            raise RuntimeError("configure the Temporal service before draining P3 work")
        await execution.drain(timeout_seconds)

    def close(self) -> None:
        if self.closed:
            return
        failure: BaseException | None = None
        try:
            with self.foundation.uow.transaction() as tx:
                for flow in ("remember", "operate", "maintenance", "io", "model"):
                    worker = self.worker_prefix + "_" + flow
                    row = tx.read("workers", worker)
                    if row:
                        self.foundation.tasks.progress.heartbeat(tx, worker, flow, stopped=True)
                        tx.write("workers", worker, {**row, "state": "stopped", "last_seen": now()})
        except BaseException as exc:
            failure = exc
        # Closing must work when PG is down, and one failed provider must not
        # abandon the pool or other clients before Service releases ownership.
        for resource in (
            self.native_embedding,
            self.owned_reranker,
            self.owned_vectors,
            self.foundation,
        ):
            if resource is not None:
                try:
                    resource.close()
                except BaseException as exc:
                    if failure is None:
                        failure = exc
        self.closed = True
        if failure is not None:
            raise failure
