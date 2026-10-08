"""One composition root for the complete persistent Remember/Recall/Operate service."""

import asyncio
import inspect
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
    ModelEquivalenceVerifier,
)
from aether_agent_memory.remember.basic.compression import ModelCompression
from aether_agent_memory.remember.basic.official_langmem import OfficialLangMemConsolidation
from aether_agent_memory.remember.documents import Documents
from aether_agent_memory.remember.local import create_runtime
from aether_agent_memory.runtime.contracts.models import TrustedContext
from aether_agent_memory.runtime.temporal.locking import DirectoryLock
from aether_agent_memory.runtime.temporal.service import TemporalService

from .config import IdentityConfiguration, ServiceConfiguration
from .health import storage_probe
from .http import create_app
from .jwt_auth import JWTAuthenticator
from .keycloak_directory import KeycloakDirectory
from .observability import configure_tracing

if TYPE_CHECKING:
    from .ruoyi_auth import RuoyiAuthenticator


class Service:
    def __init__(self, config: ServiceConfiguration, **providers: Any) -> None:
        if config.storage_mode == "production_p2":
            raise ValueError(
                "production P2 transaction and cache adapters are not yet available; "
                "formal P2 integration is required before startup"
            )
        self._close_task: asyncio.Task[None] | None = None
        self.directory_lock = DirectoryLock()
        try:
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
        if config.storage_mode != "azure" and (
            providers.get("uow") is None or providers.get("telemetry") is None
        ):
            raise ValueError("explicit PostgreSQL metadata and telemetry are required")
        self.config = config
        self.closers: list[Any] = []
        self.identity_hash: bytes | None = None
        self.closed = False
        self.storage_providers = None
        # Missing credentials fail before creating the local ownership anchor.
        # The anchor still fences two processes using the same deployment.
        if config.storage_mode == "azure":
            assert config.azure_storage is not None
            config.azure_storage.require_credentials()
            config.azure_storage.postgres.resolve_dsn()
        self.directory_lock.acquire(config.data_dir)
        if config.storage_mode == "azure":
            from aether_agent_memory.runtime.storage.azure import StorageProviders

            if set(providers) & {
                "uow",
                "telemetry",
                "p2",
                "redis",
                "body_cache",
                "vectors",
                "vectors_factory",
                "cache_factory",
                "postgres_dsn",
            }:
                raise ValueError("Azure storage cannot be mixed with injected storage providers")
            assert config.azure_storage is not None
            self.storage_providers = StorageProviders(
                config.azure_storage,
                config.data_dir,
                config.remember,
                log_retention_days=config.log_retention_days,
                log_max_records=config.log_max_records,
            )
            providers.update(self.storage_providers.runtime_options())
        if config.language_model:
            from aether_agent_memory.remember.model_provider import (
                ModelProvider,
                SupportVerifier,
            )

            model = ModelProvider(config.language_model)
            self.closers.append(model.close)
            from aether_agent_memory.remember.langmem_model import create_langmem_chat_model

            memory_model = create_langmem_chat_model(config.language_model)
            self.closers.append(memory_model.aclose)
            verifier = ModelProvider(config.verifier_model) if config.verifier_model else model
            if verifier is not model:
                self.closers.append(verifier.close)
            defaults = {
                "extraction": OfficialLangMemConsolidation.from_model(
                    memory_model, config.language_model.model
                ),
                "equivalence_verifier": ModelEquivalenceVerifier(verifier),
                "support_verifier": SupportVerifier(verifier),
                "compressor": ModelCompression(model),
            }
            providers = {**defaults, **providers}
        if config.p2_endpoint and "p2" not in providers:
            from aether_agent_memory.p2.client import P2GrpcClient

            token = os.environ.get(config.p2_token_env or "", "")
            if config.p2_token_env and not token:
                raise ValueError("configured P2 authentication token is missing")
            p2 = P2GrpcClient(
                config.p2_endpoint,
                bucket=config.p2_bucket,
                collection=config.p2_collection,
                secure=config.p2_secure,
                ca_file=config.p2_ca_file,
                cert_file=config.p2_cert_file,
                key_file=config.p2_key_file,
                token=token,
            )
            providers["p2"] = p2
            self.closers.append(p2.close)
        if "p2" in providers and "vectors_factory" not in providers and "vectors" not in providers:
            from aether_agent_memory.p2.vectors import CurrentP2Vectors

            client = providers["p2"]
            providers["vectors_factory"] = lambda uow, identity, space, dimensions: (
                CurrentP2Vectors(uow, identity, space, dimensions, client)
            )
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
                    buffer_limit=config.operate_buffer_limit,
                    high_watermark=config.operate_high_watermark,
                    low_watermark=config.operate_low_watermark,
                    decay_seconds=config.operate_decay_seconds,
                    evaluation_window_seconds=config.operate_evaluation_window_seconds,
                    evaluation_timeout_seconds=config.operate_evaluation_timeout_seconds,
                    stats_retention_seconds=config.operate_stats_retention_seconds,
                ),
            ),
            **providers,
        )
        if self.storage_providers is not None:
            self.storage_providers.transfer_runtime_ownership()
        self.runtime.executor.capacity = config.cache_capacity_bytes
        self.documents = Documents(self.runtime.remember)
        remember = cast(Any, self.runtime.remember)
        remember.documents[self.documents.provider_id] = self.documents
        remember.bodies.cache = TieredBodyCache(self.runtime.executor, remember.bodies.cache)
        self.keycloak_directory = KeycloakDirectory(self.runtime.foundation.identity)
        self.closers.append(self.keycloak_directory.close)
        self.jwt_auth = JWTAuthenticator()
        self.ruoyi_auth: RuoyiAuthenticator | None = None
        self.closers.append(self.jwt_auth.close)
        self.tracing_provider = configure_tracing(
            self.runtime.foundation.telemetry,
            str(config.otlp_traces_endpoint) if config.otlp_traces_endpoint else None,
        )
        self.closers.append(self.tracing_provider.shutdown)
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
            return await asyncio.to_thread(storage_probe, self.runtime.foundation.uow)

        async def workers(ctx: TrustedContext) -> dict[str, object]:
            return {
                "state": "available"
                if self.supervisor.state["worker"] == "running"
                else "unavailable"
            }

        health = self.runtime.health
        health.probe_timeout_seconds = self.config.health_probe_timeout_seconds
        health.snapshot_ttl_seconds = self.config.health_snapshot_ttl_seconds
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
                return cast(dict[str, object], await remember.extraction.health())

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
                raise ValueError(
                    "Temporal backend binding differs; use the original environment "
                    "or a fresh development schema"
                )
            bindings = dict(tx.rows("temporal_bindings"))
            for key, row in tx.rows("tasks"):
                if row["record"]["state"] not in TERMINAL and key not in bindings:
                    raise ValueError(
                        "task is missing its Temporal binding; recover the original execution "
                        "or use a fresh development schema"
                    )
            for key, row in tx.rows("deliveries"):
                if (
                    row["state"] not in {"acknowledged", "attention_required"}
                    and key not in bindings
                ):
                    raise ValueError(
                        "delivery is missing its Temporal binding; "
                        "investigate the original execution"
                    )
            for key, row in tx.rows("recall_requests"):
                if row["record"]["state"] in {"accepted", "running"} and key not in bindings:
                    raise ValueError(
                        "Recall is missing its Temporal binding; investigate the original request"
                    )

    def reload_identity(self) -> None:
        from aether_agent_memory.runtime.foundation.common import fingerprint

        from .ruoyi_auth import RuoyiAuthenticator

        raw = self.config.identity_file.read_bytes()
        if raw == self.identity_hash:
            return
        config = IdentityConfiguration.model_validate(yaml.safe_load(raw.decode("utf-8")))
        if self.config.browser_identity and self.config.browser_identity.issuer not in {
            issuer.issuer for issuer in config.jwt_issuers
        }:
            raise ValueError("browser identity issuer must have a configured JWT verifier")
        self.runtime.foundation.identity.provision(
            [(i.credential_sha256, i.principal) for i in config.identities],
            config.grants,
            tenants={t.tenant_id: t.enabled for t in config.tenants},
            jwt_subjects=[
                (issuer.issuer, mapping.subject, mapping.principal_id)
                for issuer in config.jwt_issuers
                for mapping in issuer.subject_mappings
            ],
            jwt_issuers=[issuer.model_dump(mode="json") for issuer in config.jwt_issuers],
            configuration_revision=config.revision,
            ruoyi_policy=fingerprint(config.ruoyi) if config.ruoyi is not None else None,
        )
        old_ruoyi = self.ruoyi_auth
        self.ruoyi_auth = (
            RuoyiAuthenticator(self.runtime.foundation.identity, config.ruoyi)
            if config.ruoyi is not None
            else None
        )
        if self.ruoyi_auth is not None:
            self.closers.append(self.ruoyi_auth.close)
        else:
            self.runtime.foundation.identity.ruoyi_revalidate = None
        if old_ruoyi is not None:
            old_ruoyi.close()
        self.jwt_auth.configure(config.jwt_issuers)
        self.keycloak_directory.configure(config.jwt_issuers)
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
                    if inspect.iscoroutinefunction(close):
                        await close()
                    else:
                        result = await asyncio.to_thread(close)
                        if inspect.isawaitable(result):
                            await result
                except (Exception, asyncio.CancelledError) as exc:
                    logging.getLogger(__name__).warning(
                        "provider_close_failed: %s", type(exc).__name__
                    )
        finally:
            try:
                if hasattr(self, "runtime"):
                    await asyncio.to_thread(self.runtime.close)
            except Exception as exc:
                logging.getLogger(__name__).warning("runtime_close_failed: %s", type(exc).__name__)
            finally:
                try:
                    storage = getattr(self, "storage_providers", None)
                    if storage is not None:
                        await asyncio.to_thread(storage.close)
                finally:
                    self.directory_lock.release()

    def app(self) -> FastAPI:
        from .routes import attach

        app = create_app(
            self.runtime,
            supervisor=self.execution,
            execution=self.execution,
            close=self.close,
            jwt_auth=self.jwt_auth,
            ruoyi_auth=lambda: self.ruoyi_auth,
            browser_identity=self.config.browser_identity,
            request_timeout_seconds=getattr(self.config, "request_timeout_seconds", 60),
        )
        app.state.service = self
        attach(app, self)
        return app


def application() -> FastAPI:
    path = os.environ.get("AETHER_SERVICE_CONFIG")
    if not path:
        raise ValueError("set AETHER_SERVICE_CONFIG to the explicit P3 service configuration")
    return Service(ServiceConfiguration.load(Path(path))).app()
