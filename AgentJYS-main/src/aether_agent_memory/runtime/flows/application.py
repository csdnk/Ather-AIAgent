"""One composition root for the complete persistent Remember/Recall/Operate service."""

import asyncio
import logging
import os
from functools import partial
from pathlib import Path
from typing import Any, cast

import yaml
from fastapi import FastAPI

from aether_agent_memory.operate.basic.body_cache import TieredBodyCache
from aether_agent_memory.operate.basic.continuous import ContinuousOperate
from aether_agent_memory.operate.standalone.policy import Settings
from aether_agent_memory.p2.client import P2GrpcClient
from aether_agent_memory.remember.basic.comparison import (
    LangMemComparison,
    ModelEquivalenceVerifier,
)
from aether_agent_memory.remember.basic.compression import ModelCompression
from aether_agent_memory.remember.basic.extraction import LangMemBatchExtraction
from aether_agent_memory.remember.documents import Documents
from aether_agent_memory.remember.local import create_runtime
from aether_agent_memory.remember.model_provider import (
    CompressionVerifier,
    ModelProvider,
    SupportVerifier,
)
from aether_agent_memory.runtime.contracts.models import TrustedContext

from .config import IdentityConfiguration, ServiceConfiguration
from .health import sqlite_probe
from .http import create_app
from .supervisor import Supervisor


class Service:
    def __init__(self, config: ServiceConfiguration, **providers: Any) -> None:
        self.config = config
        self.closers: list[Any] = []
        self.identity_hash: bytes | None = None
        self.closed = False
        # Validate local deployment inputs before allocating provider resources.
        IdentityConfiguration.model_validate(
            yaml.safe_load(config.identity_file.read_text("utf-8"))
        )
        if config.redis_url_env and not os.environ.get(config.redis_url_env):
            raise ValueError("configured Redis environment variable is missing")
        config.data_dir.mkdir(parents=True, exist_ok=True)
        if config.language_model:
            model = ModelProvider(config.language_model)
            verifier = ModelProvider(config.verifier_model) if config.verifier_model else model
            self.closers.append(model.close)
            if verifier is not model:
                self.closers.append(verifier.close)
            defaults = {
                "extraction": LangMemBatchExtraction(model, config.language_model.model),
                "comparison": LangMemComparison(model),
                "equivalence_verifier": ModelEquivalenceVerifier(verifier),
                "support_verifier": SupportVerifier(verifier),
                "compressor": ModelCompression(model),
                "compression_quality": CompressionVerifier(verifier),
                "summarizer": model,
            }
            providers = {**defaults, **providers}
        if config.p2_endpoint and "p2" not in providers:
            p2 = P2GrpcClient(config.p2_endpoint, bucket=config.p2_bucket)
            providers["p2"] = p2
            self.closers.append(p2.close)
        if config.redis_url_env and "redis" not in providers:
            from redis.asyncio import Redis

            url = os.environ.get(config.redis_url_env)
            if not url:
                raise ValueError("configured Redis environment variable is missing")
            redis = Redis.from_url(url, socket_timeout=3, socket_connect_timeout=3)
            providers["redis"] = redis
            self.closers.append(redis.aclose)
        self.runtime = create_runtime(
            config.data_dir / "p3.db",
            config.data_dir / "cache",
            body_root=config.data_dir / "bodies",
            remember_policy=config.remember,
            embedding_profile=config.embedding_profile,
            embedding_config=config.embedding_config,
            recall_config=config.recall_config,
            maintenance_principals=config.maintenance_principals,
            log_retention_days=config.log_retention_days,
            log_max_records=config.log_max_records,
            backup_root=config.data_dir / "backups",
            operate_factory=partial(
                ContinuousOperate,
                settings=Settings(
                    decay_seconds=config.operate_decay_seconds,
                    audit_seconds=config.operate_audit_seconds,
                    retry_seconds=config.operate_retry_seconds,
                    stats_retention_seconds=config.operate_stats_retention_seconds,
                ),
            ),
            **providers,
        )
        self.runtime.executor.capacity = config.cache_capacity_bytes
        self.documents = Documents(self.runtime.remember)
        remember = cast(Any, self.runtime.remember)
        remember.documents[self.documents.provider_id] = self.documents
        remember.bodies.cache = TieredBodyCache(self.runtime.executor, remember.bodies.cache)
        try:
            self.reload_identity()
        except Exception:
            self.runtime.close()
            raise
        self.supervisor = Supervisor(self.runtime, config, self.reload_identity)
        self.install_probes()

    def install_probes(self) -> None:
        remember = cast(Any, self.runtime.remember)

        async def bodies(ctx: TrustedContext) -> dict[str, object]:
            # A dedicated immutable sentinel exercises the configured provider's actual
            # read/write path, without opening another user's business object.
            key = "p3-health/provider-v1"
            data = b"aether-p3-provider-v1"
            await remember.bodies.p2_call("put_object", key, data)
            found = await remember.bodies.p2_call("get_object", key)
            return {"state": "available" if found == data else "unavailable"}

        async def generation(ctx: TrustedContext) -> dict[str, object]:
            return await asyncio.to_thread(sqlite_probe, self.runtime.foundation.uow.path)

        async def workers(ctx: TrustedContext) -> dict[str, object]:
            return {
                "state": "available"
                if self.supervisor.state["worker"] == "running"
                else "unavailable"
            }

        health = self.runtime.health
        health.register("bodies", bodies)
        health.register("recall_generation", generation, replace=True)
        health.register("workers", workers)
        required = [
            "database",
            "logs",
            "bodies",
            "embedding",
            "vectors",
            "recall_generation",
            "tokenizer",
            "executor",
            "workers",
        ]
        if self.config.language_model:

            async def model(ctx: TrustedContext) -> dict[str, object]:
                manager = cast(ModelProvider, remember.extraction.manager)
                return await manager.health()

            health.register("extraction", model, replace=True)
            required.append("extraction")
        if self.runtime.recall_settings.rerank_policy == "required":
            required.append("reranker")
        health.required_dependencies = tuple(required)
        self.runtime.foundation.monitoring.required += ("deployment_dependencies",)

    def reload_identity(self) -> None:
        raw = self.config.identity_file.read_bytes()
        if raw == self.identity_hash:
            return
        config = IdentityConfiguration.model_validate(yaml.safe_load(raw.decode("utf-8")))
        self.runtime.foundation.identity.provision(
            [(i.credential_sha256, i.principal) for i in config.identities],
            config.grants,
            tenants={t.tenant_id: t.enabled for t in config.tenants},
            configuration_revision=config.revision,
        )
        self.identity_hash = raw

    async def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        await self.supervisor.stop()
        try:
            for close in reversed(self.closers):
                try:
                    await close()
                except Exception as exc:
                    logging.getLogger(__name__).warning(
                        "provider_close_failed: %s", type(exc).__name__
                    )
        finally:
            self.runtime.close()

    def app(self) -> FastAPI:
        from .routes import attach

        app = create_app(self.runtime, supervisor=self.supervisor, close=self.close)
        app.state.service = self
        attach(app, self)
        return app


def application() -> FastAPI:
    path = os.environ.get("AETHER_SERVICE_CONFIG")
    if not path:
        raise ValueError("set AETHER_SERVICE_CONFIG to the explicit P3 service configuration")
    return Service(ServiceConfiguration.load(Path(path))).app()
