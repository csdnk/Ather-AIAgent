"""Composition root for a single-host engineering runtime."""

from __future__ import annotations

import asyncio
import json
from contextlib import suppress
from pathlib import Path
from typing import Any

import yaml

from .common import now
from .diagnostics import Diagnostics
from .events import Events
from .identity import Identity
from .sample import Sample
from .storage import SQLiteUnitOfWork
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
    ) -> None:
        options: dict[str, Any] = {}
        if profile is not None:
            options = yaml.safe_load(Path(profile).read_text(encoding="utf-8"))["tasks"]
        self.uow = SQLiteUnitOfWork(database)
        if log_path and Path(log_path).resolve() == Path(database).resolve():
            self.uow.close()
            raise ValueError("business database and log database must be separate")
        self.telemetry = Telemetry(
            log_path or Path(database).with_suffix(".logs.db"),
            retention_days=log_retention_days,
            max_records=log_max_records,
        )
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
        self.sample = Sample(self.uow, self.identity, self.tasks, self.events)

    def close(self) -> None:
        self.uow.close()
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
        """SIGTERM stops new claims; in-flight work remains fenced by its lease."""
        try:
            while not stop.is_set():
                with self.uow.transaction() as tx:
                    tx.write(
                        "workers",
                        worker_id,
                        {
                            "worker_id": worker_id,
                            "execution_class": execution_class,
                            "last_seen": now(),
                            "state": "polling",
                        },
                    )
                worked = await self.tasks.run_once(worker_id, execution_class)
                delivered = self.events.dispatch_once(worker_id)
                if worked or delivered:
                    print(
                        json.dumps(
                            {
                                "worker_id": worker_id,
                                "stage": "iteration",
                                "task_processed": worked,
                                "delivery_processed": delivered,
                            }
                        ),
                        flush=True,
                    )
                if once:
                    return
                with suppress(TimeoutError):
                    await asyncio.wait_for(stop.wait(), timeout=poll_seconds)
        finally:
            with self.uow.transaction() as tx:
                tx.write(
                    "workers",
                    worker_id,
                    {
                        "worker_id": worker_id,
                        "execution_class": execution_class,
                        "last_seen": now(),
                        "state": "stopped",
                    },
                )
