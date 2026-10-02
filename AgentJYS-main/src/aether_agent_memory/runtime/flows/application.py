"""One composition root for the complete persistent Remember/Recall/Operate service."""

import asyncio
import logging
import os
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import yaml
from fastapi import FastAPI

from aether_agent_memory.operate.basic.body_cache import TieredBodyCache
from aether_agent_memory.operate.basic.continuous import ContinuousOperate
from aether_agent_memory.operate.standalone.policy import Settings
from aether_agent_memory.remember.basic.comparison import (
    LangMemComparison,
    ModelEquivalenceVerifier,
)
from aether_agent_memory.remember.basic.compression import ModelCompression
from aether_agent_memory.remember.basic.extraction import LangMemBatchExtraction
from aether_agent_memory.remember.documents import Documents
from aether_agent_memory.remember.local import create_runtime
from aether_agent_memory.runtime.contracts.models import TrustedContext
from aether_agent_memory.runtime.temporal.locking import DirectoryLock
from aether_agent_memory.runtime.temporal.service import TemporalService

from .config import IdentityConfiguration, ServiceConfiguration
from .health import sqlite_probe
from .http import create_app

if TYPE_CHECKING:
    from aether_agent_memory.remember.model_provider import ModelProvider


class Service:
    def __init__(self, config: ServiceConfiguration, **providers: Any) -> None:
        self._close_task: asyncio.Task[None] | None = None
        self.directory_lock = DirectoryLock()
        self.directory_lock.acquire(config.data_dir)
        try:
            from aether_agent_memory.runtime.temporal.migration import check_service_backend

            if config.metadata_backend == "sqlite":
                check_service_backend(config.data_dir / "p3.db", config.temporal)
            self.initialize(config, **providers)
        except BaseException:
            self._cleanup_failed_initialization()
            raise

    def _cleanup_failed_initialization(self) -> None:
        """Constructor-owned clients have not begun async I/O before startup returns.

        A synchronous factory can also be invoked inside an event loop. Close its
        partially constructed, unused clients in a temporary loop on another thread
        in that case, completing cleanup before releasing directory ownership.
        """

        def cleanup() -> None:
            asyncio.run(self._release_resources())

        try:
            asyncio.get_running_loop()
        except RuntimeError:
            cleanup()
        else:
            with ThreadPoolExecutor(max_workers=1, thread_name_prefix="p3-startup-cleanup") as pool:
                pool.submit(cleanup).result()

    def initialize(self, config: ServiceConfiguration, **providers: Any) -> None:
        self.config = config
        self.closers: list[Any] = []
        self.identity_hash: bytes | None = None
        self.closed = False
        postgres_dsn = None
        if config.metadata_backend == "postgresql":
            postgres_dsn = os.environ.get(config.postgres_dsn_env)
            if not postgres_dsn:
                raise ValueError("configured PostgreSQL DSN environment variable is missing")
        # Validate local deployment inputs before allocating provider resources.
        IdentityConfiguration.model_validate(
            yaml.safe_load(config.identity_file.read_text("utf-8"))
        )
        if config.redis_url_env and not os.environ.get(config.redis_url_env):
            raise ValueError("configured Redis environment variable is missing")
        config.data_dir.mkdir(parents=True, exist_ok=True)
        if config.language_model:
            from aether_agent_memory.remember.model_provider import (
                CompressionVerifier,
                ModelProvider,
                SupportVerifier,
            )

            model = ModelProvider(config.language_model)
            self.closers.append(model.close)
            verifier = ModelProvider(config.verifier_model) if config.verifier_model else model
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
        if config.ceph and "p2" not in providers:
            from aether_agent_memory.remember.basic.ceph_p2 import CephP2

            ceph_p2 = CephP2(config.ceph)
            providers["p2"] = ceph_p2
            self.closers.append(ceph_p2.aclose)
        if config.p2_endpoint and "p2" not in providers:
            from aether_agent_memory.p2.client import P2GrpcClient

            grpc_p2 = P2GrpcClient(config.p2_endpoint, bucket=config.p2_bucket)
            providers["p2"] = grpc_p2
            self.closers.append(grpc_p2.close)
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
            postgres_dsn=postgres_dsn,
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
        if config.metadata_backend == "postgresql":
            self.check_postgres_execution_binding()
        self.runtime.executor.capacity = config.cache_capacity_bytes
        self.documents = Documents(self.runtime.remember)
        remember = cast(Any, self.runtime.remember)
        remember.documents[self.documents.provider_id] = self.documents
        remember.bodies.cache = TieredBodyCache(self.runtime.executor, remember.bodies.cache)
        self.reload_identity()
        from aether_agent_memory.operate.basic.maintenance import CacheMaintenance

        self.cache_maintenance = CacheMaintenance(self.runtime)
        self.execution = TemporalService(
            self.runtime, config, self.reload_identity, self.cache_maintenance
        )
        self.supervisor = self.execution
        self.install_probes()

    def install_probes(self) -> None:
        remember = cast(Any, self.runtime.remember)

        async def bodies(ctx: TrustedContext) -> dict[str, object]:
            # Read-only connectivity check. Probes never create an object in P2.
            key = "p3-health/provider-v1"
            await remember.bodies.p2_call("get_object", key)
            return {"state": "available"}

        async def generation(ctx: TrustedContext) -> dict[str, object]:
            if hasattr(self.runtime.foundation.uow, "probe"):
                return await asyncio.to_thread(self.runtime.foundation.uow.probe)
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
                manager = cast("ModelProvider", remember.extraction.manager)
                return await manager.health()

            health.register("extraction", model, replace=True)
            required.append("extraction")
        if self.runtime.recall_settings.rerank_policy == "required":
            required.append("reranker")
        health.required_dependencies = tuple(required)
        self.runtime.foundation.monitoring.required += ("deployment_dependencies",)

    def check_postgres_execution_binding(self) -> None:
        """Apply the same pre-admission transport checks to migrated PG records."""
        from aether_agent_memory.runtime.foundation.tasks import TERMINAL
        from aether_agent_memory.runtime.temporal.config import deployment_configuration

        config = self.config.temporal
        expected = deployment_configuration(config)
        with self.runtime.foundation.uow.transaction() as tx:
            marker = tx.read("meta", "execution_backend")
            if marker and marker != {
                "backend": "temporal",
                "deployment_id": config.deployment_id,
                "namespace": config.namespace,
                "task_queue_prefix": expected.task_queue_prefix,
            }:
                raise ValueError("Temporal backend binding changed; explicit migration required")
            bindings = dict(tx.rows("temporal_bindings"))
            for key, row in tx.rows("tasks"):
                if row["record"]["state"] not in TERMINAL and key not in bindings:
                    raise ValueError("historical tasks require explicit offline migration")
            for key, row in tx.rows("deliveries"):
                if (
                    row["state"] not in {"acknowledged", "attention_required"}
                    and key not in bindings
                ):
                    raise ValueError("historical deliveries require explicit offline migration")
            for key, row in tx.rows("recall_requests"):
                if row["record"]["state"] in {"accepted", "running"} and key not in bindings:
                    raise ValueError("historical Recall requires explicit offline migration")

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
        task = getattr(self, "_close_task", None)
        if task is None:
            task = asyncio.create_task(self._shutdown(), name="p3-service-shutdown")
            self._close_task = task
        # A caller disconnecting/cancelling must not abandon owned resource cleanup.
        await asyncio.shield(task)

    async def _shutdown(self) -> None:
        try:
            await self.supervisor.stop()
        finally:
            try:
                await self._release_resources()
            finally:
                self.closed = True

    async def _release_resources(self) -> None:
        try:
            for close in reversed(getattr(self, "closers", [])):
                try:
                    await close()
                except (Exception, asyncio.CancelledError) as exc:
                    logging.getLogger(__name__).warning(
                        "provider_close_failed: %s", type(exc).__name__
                    )
        finally:
            try:
                if hasattr(self, "runtime"):
                    self.runtime.close()
            except Exception as exc:
                logging.getLogger(__name__).warning("runtime_close_failed: %s", type(exc).__name__)
            finally:
                self.directory_lock.release()

    def app(self) -> FastAPI:
        from .routes import attach

        app = create_app(
            self.runtime, supervisor=self.execution, execution=self.execution, close=self.close
        )
        app.state.service = self
        attach(app, self)
        return app


def application() -> FastAPI:
    path = os.environ.get("AETHER_SERVICE_CONFIG")
    if not path:
        raise ValueError("set AETHER_SERVICE_CONFIG to the explicit P3 service configuration")
    return Service(ServiceConfiguration.load(Path(path))).app()
