"""Basic three-flow host. Explicit local profile; optional providers are injected."""

import asyncio
import json
import os
import secrets
import tempfile
import time
from pathlib import Path
from typing import TYPE_CHECKING

from aether_agent_memory.operate.basic.executor import LocalCacheExecutor
from aether_agent_memory.operate.basic.service import Operate
from aether_agent_memory.recall.basic.adapters import SPACE, LexicalEmbedding, SQLiteVectors
from aether_agent_memory.recall.basic.config import RecallSettings
from aether_agent_memory.recall.basic.reranking import CrossEncoderReranker, Reranker
from aether_agent_memory.recall.basic.service import Recall
from aether_agent_memory.recall.basic.tokenization import ModelTokenizer, TokenCounter
from aether_agent_memory.recall.contracts.ports import EmbeddingPort, VectorPort
from aether_agent_memory.remember.basic.extraction import LiteralExtraction
from aether_agent_memory.remember.basic.service import Remember
from aether_agent_memory.remember.contracts.models import ExtractionRequest, SourceRef
from aether_agent_memory.remember.contracts.ports import ExtractionPort
from aether_agent_memory.runtime.contracts.models import TrustedContext
from aether_agent_memory.runtime.foundation.common import fingerprint, now
from aether_agent_memory.runtime.foundation.host import Foundation
from aether_agent_memory.runtime.foundation.requests import text_hash
from aether_agent_memory.runtime.foundation.telemetry import attach_provider

from .health import Health, sqlite_probe

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
        vectors: VectorPort | None = None,
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
        )
        self.owned_vectors = None
        self.owned_reranker = None
        self.worker_prefix = "flow_" + secrets.token_hex(6)
        self.closed = False
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
                from aether_agent_memory.recall.basic.milvus import MilvusVectors

                self.owned_vectors = MilvusVectors(
                    self.foundation.uow,
                    self.foundation.identity,
                    self.model_space,
                    int(dimensions or 0),
                    uri=self.recall_settings.milvus_uri,
                    collection=self.recall_settings.milvus_collection,
                    token=os.environ.get(self.recall_settings.milvus_token_env, ""),
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
        self.remember = Remember(
            self.foundation.uow,
            self.foundation.identity,
            self.foundation.tasks,
            self.foundation.events,
            extraction or LiteralExtraction(),
            self.embedding,
            self.vectors,
            self.model_space,
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
            self.vectors,
            self.model_space,
            settings=self.recall_settings,
            tokenizer=selected_tokenizer,
            reranker=reranker,
        )
        self.executor = LocalCacheExecutor(cache_root)
        self.operate = Operate(
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
            return await asyncio.to_thread(sqlite_probe, self.foundation.uow.path, write=True)

        async def log_probe(ctx: TrustedContext) -> dict[str, object]:
            return await asyncio.to_thread(sqlite_probe, self.foundation.telemetry.path, write=True)

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
                return await asyncio.to_thread(sqlite_probe, self.vectors.uow.path)
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

    async def tick(
        self, *, periodic: bool = False, maintenance_context: TrustedContext | None = None
    ) -> bool:
        with self.foundation.uow.transaction() as tx:
            for flow in ("remember", "operate"):
                worker = self.worker_prefix + "_" + flow
                self.foundation.tasks.progress.heartbeat(tx, worker, flow)
                tx.write(
                    "workers",
                    worker,
                    {
                        "worker_id": worker,
                        "execution_class": flow,
                        "last_seen": now(),
                        "state": "polling",
                    },
                )
        worked = self.recall.recover_expired() > 0
        for _ in range(100):
            if not self.foundation.events.dispatch_once("flow_dispatcher"):
                break
            worked = True
        for flow in ("remember", "operate", "maintenance", "io", "model"):
            worked = (
                await self.foundation.tasks.run_once(self.worker_prefix + "_" + flow, flow)
                or worked
            )
        if maintenance_context is not None:
            result = await self.foundation.dispositions.cycle(maintenance_context)
            worked = bool(result["sampled"] or result["reconciled"]) or worked
        period = str(int(time.time() // 5))
        if periodic and period != self.last_period:
            self.operate.periodic(period)
            self.last_period = period
        return worked

    async def drain(self, timeout_seconds: float = 12) -> None:
        end = time.monotonic() + timeout_seconds
        while time.monotonic() < end:
            worked = await self.tick()
            with self.foundation.uow.transaction() as tx:
                pending = any(
                    row["record"]["state"] in {"pending", "running", "retry_wait", "recovery_wait"}
                    for _, row in tx.rows("tasks")
                )
                pending |= any(
                    row["state"] not in {"acknowledged", "attention_required"}
                    for _, row in tx.rows("deliveries")
                )
            if not worked and not pending:
                return
            await asyncio.sleep(0.02)
        raise TimeoutError("flow drain has unresolved tasks; inspect foundation diagnostics")

    def close(self) -> None:
        if self.closed:
            return
        with self.foundation.uow.transaction() as tx:
            for flow in ("remember", "operate", "maintenance", "io", "model"):
                worker = self.worker_prefix + "_" + flow
                row = tx.read("workers", worker)
                if row:
                    self.foundation.tasks.progress.heartbeat(tx, worker, flow, stopped=True)
                    tx.write("workers", worker, {**row, "state": "stopped", "last_seen": now()})
        self.closed = True
        if self.native_embedding:
            self.native_embedding.close()
        if self.owned_reranker:
            self.owned_reranker.close()
        if self.owned_vectors:
            self.owned_vectors.close()
        self.foundation.close()
