"""Continuous independent worker lanes over the shared durable RF queue."""

import asyncio
import logging
import secrets
import time
from collections.abc import Callable
from contextlib import suppress
from typing import Any

from aether_agent_memory.runtime.contracts.models import Principal, TrustedContext
from aether_agent_memory.runtime.foundation.common import later, now

from .config import ServiceConfiguration
from .host import ThreeFlows


class Supervisor:
    def __init__(
        self,
        host: ThreeFlows,
        config: ServiceConfiguration,
        reload_identity: Callable[[], None],
    ) -> None:
        self.host, self.config, self.reload_identity = host, config, reload_identity
        self.stop_event = asyncio.Event()
        self.tasks: list[asyncio.Task[None]] = []
        self.state: dict[str, Any] = {"worker": "starting", "lanes": {}, "identity": "starting"}

    async def start(self) -> None:
        if self.tasks:
            raise RuntimeError("supervisor already started")
        self.stop_event.clear()
        self.reload_identity()
        self.state["identity"] = "ready"
        for lane in ("remember", "operate", "maintenance", "io", "model"):
            self.state["lanes"][lane] = "starting"
            self.tasks.append(asyncio.create_task(self.work(lane), name="p3-" + lane))
        self.tasks.append(asyncio.create_task(self.coordinate(), name="p3-coordinator"))
        self.tasks.append(asyncio.create_task(self.maintain(), name="p3-dispositions"))
        self.state["worker"] = "running"

    async def pause(self, seconds: float) -> None:
        with suppress(TimeoutError):
            await asyncio.wait_for(self.stop_event.wait(), seconds)

    async def work(self, lane: str) -> None:
        worker_id = self.host.worker_prefix + "_" + lane
        while not self.stop_event.is_set():
            try:
                worked = await self.host.foundation.tasks.run_once(worker_id, lane)
                self.state["lanes"][lane] = "running"
            except Exception as exc:
                worked = False
                self.error(lane, exc)
                self.state["lanes"][lane] = "degraded"
            # A busy lane still yields to HTTP, event delivery and other workers.
            await self.pause(0.001 if worked else self.config.poll_seconds)
        self.state["lanes"][lane] = "stopped"

    async def coordinate(self) -> None:
        reload_at = 0.0
        while not self.stop_event.is_set():
            try:
                if time.monotonic() >= reload_at:
                    reload_at = time.monotonic() + self.config.identity_reload_seconds
                    try:
                        self.reload_identity()
                        self.state["identity"] = "ready"
                    except Exception as exc:
                        self.error("identity", exc)
                        self.state["identity"] = "degraded"
                await self.host.tick(
                    periodic=True, run_tasks=False, periodic_interval=self.config.periodic_seconds
                )
                self.state["worker"] = (
                    "running"
                    if self.state["identity"] == "ready"
                    and all(v == "running" for v in self.state["lanes"].values())
                    else "degraded"
                )
            except Exception as exc:
                self.error("coordinator", exc)
                self.state["worker"] = "degraded"
            await self.pause(self.config.periodic_seconds)

    def error(self, component: str, exc: Exception) -> None:
        # Exception messages may contain inputs or credentials. Log only the type,
        # once per changed failure, and expose a bounded diagnostic state.
        errors = self.state.setdefault("errors", {})
        name = type(exc).__name__
        if errors.get(component) != name:
            logging.getLogger(__name__).warning("p3_%s_failed: %s", component, name)
        errors[component] = name

    async def maintain(self) -> None:
        while not self.stop_event.is_set():
            for principal_id in self.config.maintenance_principals:
                try:
                    with self.host.foundation.uow.transaction() as tx:
                        row = tx.read("identities", principal_id)
                        if not row or not row["enabled"]:
                            continue
                        ctx = TrustedContext(
                            principal=Principal.model_validate(row["principal"]),
                            request_id=secrets.token_hex(16),
                            operation_id=secrets.token_hex(16),
                            trace_id=secrets.token_hex(16),
                            span_id=secrets.token_hex(8),
                            deadline_at=later(now(), 60),
                        )
                        self.host.foundation.identity.revalidate(tx, ctx)
                    await self.host.foundation.dispositions.cycle(ctx)
                except Exception as exc:
                    self.error("maintenance", exc)
            await self.pause(max(1, self.config.periodic_seconds))

    async def stop(self) -> None:
        self.stop_event.set()
        if self.tasks:
            _, pending = await asyncio.wait(self.tasks, timeout=self.config.shutdown_seconds)
            for task in pending:
                task.cancel()
            await asyncio.gather(*self.tasks, return_exceptions=True)
            self.tasks.clear()
        self.state["worker"] = "stopped"
