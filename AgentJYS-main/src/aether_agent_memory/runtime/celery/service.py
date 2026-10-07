"""Production hybrid facade: new Remember uses Celery, existing bindings stay pinned."""

import asyncio
import time
from contextlib import suppress
from typing import Any, cast

from aether_agent_memory.runtime.contracts.models import ErrorCode
from aether_agent_memory.runtime.foundation.common import FoundationError, fingerprint, later
from aether_agent_memory.runtime.temporal.models import PeriodicState
from aether_agent_memory.runtime.temporal.periodic import PeriodicActivities, register_p3_periodic
from aether_agent_memory.runtime.temporal.service import TemporalService

from .controls import RoutedControls
from .dispatch import CeleryDispatcher
from .execution import CeleryExecution
from .migration import inspect_bindings


class CombinedBridge:
    def __init__(self, service: Any) -> None:
        self.service = service

    async def flush(self) -> int:
        count = 0
        with suppress(Exception):
            count = await self.service.dispatcher.flush()
        original = self.service.temporal_bridge
        if original is not None:
            count += await original.flush()
        return count


class ExecutionService(TemporalService):
    def __init__(self, runtime: Any, config: Any, reload_identity: Any, maintenance: Any) -> None:
        super().__init__(runtime, config, reload_identity, maintenance)
        celery_config = config.celery.model_copy(
            update={
                "queue_prefix": config.celery.queue_prefix
                + "."
                + fingerprint(config.temporal.deployment_id)[:16]
            }
        )
        self.celery = CeleryExecution(self.ledger, self.registry, celery_config)
        self.ledger.backend_admission = self.route
        self.controls = RoutedControls(self.ledger, config.maintenance_principals, self.celery)
        self.ledger.tasks.on_recovery = self.controls.recovery
        self.dispatcher = CeleryDispatcher(self.celery)
        self.temporal_bridge: Any = None
        self.combined_bridge = CombinedBridge(self)
        self.bridge = cast(Any, self.combined_bridge)
        self.celery_runner: asyncio.Task[None] | None = None
        self.temporal_start: asyncio.Task[None] | None = None
        self.celery_available = False
        self.worker_role = False
        self.celery_periodic = PeriodicActivities(self.ledger, None)
        register_p3_periodic(self.celery_periodic, runtime, maintenance, ())
        self.celery_periodic.routes = {
            key: route
            for key, route in self.celery_periodic.routes.items()
            if key.startswith("remember_")
        }
        self.celery_periodic.transport = "celery"
        inspect_bindings(self.ledger.tasks.uow, config, apply=True)

    def route(self, tx: Any, task: Any) -> Any:
        if task.kind.startswith("remember.") or task.kind == "runtime.event_delivery":
            return self.celery.bind(tx, task)
        return None

    def require_ready(self, kind: str | None = None) -> None:
        if kind is not None and (kind.startswith("recall") or kind.startswith("operate")):
            if not self.accepting:
                raise FoundationError(
                    ErrorCode.DEPENDENCY_UNAVAILABLE, "Temporal execution unavailable"
                )
            return None
        # Admission is durable even during a broker outage. Provider/identity checks
        # remain in admission and at each worker fence; readiness reports degradation.
        if self.stopped.is_set():
            raise FoundationError(ErrorCode.DEPENDENCY_UNAVAILABLE, "execution is stopping")

    def ready(self) -> dict[str, Any]:
        return {
            "readiness": "ready" if self.celery_available else "not_ready",
            "reason_code": "READY" if self.celery_available else "CELERY_UNAVAILABLE",
            "celery": "available" if self.celery_available else "unavailable",
            "temporal": self.state.get("temporal", "starting"),
        }

    def accept(self, ctx: Any, kind: str, payload: Any, **kwargs: Any) -> Any:
        if kind == "recall.execute" and not self.accepting:
            raise FoundationError(
                ErrorCode.DEPENDENCY_UNAVAILABLE, "Temporal execution unavailable"
            )
        return super().accept(ctx, kind, payload, **kwargs)

    async def connect(self) -> None:
        try:
            await super().connect()
            self.temporal_bridge = self.bridge
        finally:
            self.bridge = cast(Any, self.combined_bridge)

    async def start(self) -> None:
        self.stopped.clear()
        self.celery_runner = asyncio.create_task(self.coordinate_celery(), name="p3-celery-outbox")
        self.temporal_start = asyncio.create_task(super().start(), name="p3-temporal-start")

    async def coordinate_celery(self) -> None:
        while not self.stopped.is_set():
            try:
                await self.dispatcher.flush()

                def probe() -> bool:
                    with self.dispatcher.app.connection_for_write() as connection:
                        connection.ensure_connection(max_retries=0)
                    queues = self.dispatcher.app.control.inspect(timeout=0.5).active_queues() or {}
                    expected = self.dispatcher.app.conf.task_default_queue
                    return any(
                        queue["name"] == expected for names in queues.values() for queue in names
                    )

                self.celery_available = await asyncio.to_thread(probe)
            except Exception:
                self.celery_available = False
            with suppress(TimeoutError):
                await asyncio.wait_for(self.wakeup.wait(), self.config.poll_seconds)
            self.wakeup.clear()

    async def periodic_tick(self) -> None:
        # Beat is only a wakeup. A leased durable pointer resumes unfinished ticks.
        deployment = self.ledger.config.deployment_id
        with self.ledger.tasks.uow.transaction() as tx:
            control = tx.read("celery_periodic_control", deployment) or {}
            if not control.get("enabled", True):
                return
            row = tx.read("celery_periodic_schedule", deployment) or {}
            stamp = self.ledger.tasks.clock()
            if row.get("lease_until", "") > stamp or row.get("due_at", "") > stamp:
                return
            state = (
                PeriodicState.model_validate(row["state"])
                if row.get("state")
                else PeriodicState(
                    deployment_id=deployment,
                    last_tick=int(time.time() // self.config.periodic_seconds),
                    interval_seconds=self.config.periodic_seconds,
                )
            )
            generation = row.get("generation", 0) + 1
            tx.write(
                "celery_periodic_schedule",
                deployment,
                {
                    **row,
                    "state": state.model_dump(mode="json"),
                    "generation": generation,
                    "lease_until": later(stamp, 30),
                },
            )
        state = await self.celery_periodic.batch(state)
        with self.ledger.tasks.uow.transaction() as tx:
            row = tx.read("celery_periodic_schedule", deployment)
            if row["generation"] != generation:
                return
            tx.write(
                "celery_periodic_schedule",
                deployment,
                {
                    **row,
                    "lease_until": "",
                    "due_at": later(self.ledger.tasks.clock(), self.config.periodic_seconds)
                    if state.cursor is None
                    else self.ledger.tasks.clock(),
                    "state": None if state.cursor is None else state.model_dump(mode="json"),
                },
            )

    def operation(self, ctx: Any, job_id: str) -> dict[str, Any]:
        result = super().operation(ctx, job_id)
        with self.ledger.tasks.uow.transaction() as tx:
            binding = tx.read("temporal_bindings", job_id)
            if binding and binding.get("backend") == "celery":
                result.pop("temporal", None)
                result["execution_backend"] = "celery"
                result["execution_id"] = binding["workflow_id"]
                result["celery"] = tx.read("celery_jobs", job_id)
        return result

    async def stop(self) -> None:
        self.stopped.set()
        if self.worker_role:
            self.celery.close()
            return
        for pending in (self.celery_runner, self.temporal_start):
            if pending is not None:
                pending.cancel()
                await asyncio.gather(pending, return_exceptions=True)
        await super().stop()
        self.celery.close()
