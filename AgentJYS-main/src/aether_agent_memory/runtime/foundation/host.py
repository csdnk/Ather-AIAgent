"""Composition root for a single-host engineering runtime."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import yaml

from aether_agent_memory.runtime.storage.ports import MetadataUnitOfWork

from .diagnostics import Diagnostics
from .disposition import Dispositions
from .events import Events
from .identity import Identity
from .lifecycle import RuntimeLifecycle
from .monitoring import Monitoring
from .sample import Sample
from .tasks import Tasks
from .telemetry import Telemetry


class Foundation:
    def __init__(
        self,
        database: str | Path,
        profile: str | Path | None = None,
        *,
        log_path: str | Path | None = None,
        log_retention_days: int = 14,
        log_max_records: int = 200_000,
        maintenance_principals: tuple[str, ...] = (),
        backup_root: str | Path | None = None,
        engineering_profile: bool = False,
        postgres_dsn: str | None = None,
        uow: MetadataUnitOfWork | None = None,
        telemetry: Telemetry | None = None,
    ) -> None:
        if (uow is None) != (telemetry is None):
            raise ValueError("external metadata and telemetry must be supplied together")
        if uow is not None and (postgres_dsn is not None or log_path is not None):
            raise ValueError(
                "external providers cannot be mixed with database constructor settings"
            )
        self.uow: MetadataUnitOfWork
        options: dict[str, Any] = {}
        if profile is not None:
            options = yaml.safe_load(Path(profile).read_text(encoding="utf-8"))["tasks"]
        if uow is not None:
            self.uow = uow
        elif postgres_dsn is None:
            raise ValueError(
                "Foundation requires explicit PostgreSQL metadata "
                "or injected metadata and telemetry"
            )
        else:
            from .postgres import PostgresUnitOfWork

            self.uow = PostgresUnitOfWork(postgres_dsn, database)
        if log_path and Path(log_path).resolve() == Path(database).resolve():
            self.uow.close()
            raise ValueError("business database and log database must be separate")
        try:
            if telemetry is not None:
                self.telemetry = telemetry
            else:
                from .postgres_telemetry import PostgresTelemetry

                if postgres_dsn is None:
                    raise ValueError("injected metadata also requires explicit telemetry")
                self.telemetry = PostgresTelemetry(
                    postgres_dsn,
                    log_path or Path(database).with_suffix(".logs.db"),
                    retention_days=log_retention_days,
                    max_records=log_max_records,
                )
        except BaseException:
            self.uow.close()
            raise
        try:
            self.uow.telemetry = self.telemetry
            self.identity = Identity(self.uow)
            self.tasks = Tasks(
                self.uow,
                self.identity,
                lease_seconds=options.get("lease_seconds", 30),
                retry_seconds=options.get("retry_seconds", 1),
                max_attempts=options.get("max_attempts", 3),
                query_max_attempts=options.get("query_max_attempts", 6),
                pending_limit=options.get("pending_limit_per_scope", 100),
                class_limits=options.get("class_limits"),
                per_scope_running=options.get("running_limit_per_scope", 1),
                pending_limit_per_tenant=options.get("pending_limit_per_tenant", 300),
                per_tenant_running=options.get("running_limit_per_tenant_class", 1),
            )
            self.events = Events(self.uow, self.identity, lease_seconds=self.tasks.lease_seconds)
            self.diagnostics = Diagnostics(self.uow, self.identity)
            self.monitoring = Monitoring(self.uow, self.identity, self.telemetry)
            self.diagnostics.monitoring = self.monitoring
            self.lifecycle = RuntimeLifecycle(
                self.uow,
                self.identity,
                Path(backup_root) if backup_root else Path(database).parent / "backups",
                operators=maintenance_principals,
            )
            self.dispositions = Dispositions(self.tasks)
            self._sample = (
                Sample(self.uow, self.identity, self.tasks, self.events)
                if engineering_profile
                else None
            )
        except BaseException:
            self.close()
            raise

    @property
    def sample(self) -> Sample:
        if self._sample is None:
            raise RuntimeError("engineering sample requires an explicit test/demo profile")
        return self._sample

    def close(self) -> None:
        try:
            self.uow.close()
        finally:
            self.telemetry.close()

    async def worker(
        self,
        worker_id: str,
        stop: asyncio.Event,
        *,
        once: bool = False,
        execution_class: str = "engineering",
        poll_seconds: float = 0.25,
    ) -> None:
        raise RuntimeError("RF scheduling is retired; use the configured Temporal service")
