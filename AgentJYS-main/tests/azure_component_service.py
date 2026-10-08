"""Build controlled HTTP tests with real Azure storage; production Service is unchanged."""

import asyncio
from contextlib import asynccontextmanager

import yaml

from aether_agent_memory.runtime.contracts.models import Permission
from aether_agent_memory.runtime.flows.application import Service as RealService
from aether_agent_memory.runtime.flows.config import IdentityConfiguration
from azure_test_runtime import owned, provider_options
from controlled_embedding import ControlledEmbedding


class Service(RealService):
    def __init__(self, config, **providers):
        if config.storage_mode == "azure":
            super().__init__(config, **providers)
            return
        options = dict(provider_options(config.data_dir / "state-anchor", config.remember))
        from aether_agent_memory.runtime.foundation.postgres import PostgresUnitOfWork
        from aether_agent_memory.runtime.foundation.postgres_telemetry import PostgresTelemetry

        dsn = providers.pop("postgres_dsn", options.pop("postgres_dsn"))
        uow = PostgresUnitOfWork(dsn, config.data_dir / "state-anchor")
        logs = PostgresTelemetry(dsn, config.data_dir / "log-anchor")
        owned().clients.extend((uow, logs))
        options.update(uow=uow, telemetry=logs)
        if config.embedding_profile != "native":
            options["embedding"] = ControlledEmbedding()
        options.update(providers)
        super().__init__(config, **options)
        if isinstance(self.runtime.embedding, ControlledEmbedding):
            self.runtime.health.register("embedding", self.runtime.embedding.health, replace=True)

    def app(self):
        app = super().app()
        if self.config.storage_mode == "azure":
            return app
        original = app.router.lifespan_context

        @asynccontextmanager
        async def lifespan(application):
            identities = IdentityConfiguration.model_validate(
                yaml.safe_load(self.config.identity_file.read_text(encoding="utf-8"))
            )
            principal = next(
                entry.principal
                for entry in identities.identities
                if Permission.READ in entry.principal.permissions
            )
            ctx = await asyncio.to_thread(
                self.runtime.foundation.identity.context_for_principal,
                principal,
                timeout_seconds=300,
            )
            # Provision this fixture's actual collection before readiness, with
            # the same backend and grant checks. Health probes remain read-only.
            await self.runtime.vectors.prepare(ctx)
            async with original(application):
                yield

        app.router.lifespan_context = lifespan
        return app
