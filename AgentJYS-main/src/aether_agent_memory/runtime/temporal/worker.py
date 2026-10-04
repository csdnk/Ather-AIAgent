"""SDK Worker lifecycle; each queue uses the same single-host business services."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

from temporalio.client import Client
from temporalio.worker import Worker

from .activities import Activities
from .ledger import ExecutionLedger
from .locking import ExecutionLimits
from .periodic import PeriodicActivities
from .periodic_workflow import P3PeriodicWorkflow
from .registry import StageRegistry
from .workflows import EventDeliveryWorkflow, P3TaskWorkflow


class WorkerHost:
    def __init__(
        self,
        client: Client,
        ledger: ExecutionLedger,
        registry: StageRegistry,
        *,
        shutdown_seconds: float = 10,
        periodic: PeriodicActivities | None = None,
    ) -> None:
        self.client, self.ledger, self.registry = client, ledger, registry
        self.shutdown_seconds = shutdown_seconds
        self.periodic = periodic
        self.executor = ThreadPoolExecutor(
            max_workers=sum(ledger.tasks.class_limits.values()), thread_name_prefix="p3-io"
        )
        self.execution_classes = {ledger.tasks.handlers[kind][0] for kind in registry.routes}
        if periodic is not None:
            self.execution_classes.add("periodic")
        # Temporal reserves workflow slots per Worker, while this executor is shared.
        # Keep a thread for every reserved slot so another queue cannot starve replay.
        self.workflow_slots_per_queue = 2
        self.workflow_executor = ThreadPoolExecutor(
            max_workers=max(1, len(self.execution_classes) * self.workflow_slots_per_queue),
            thread_name_prefix="p3-workflow",
        )
        self.activities = Activities(
            ledger,
            registry,
            ExecutionLimits(
                ledger.tasks.class_limits,
                per_tenant=ledger.tasks.per_tenant_running,
                per_scope=ledger.tasks.per_scope_running,
            ),
            self.executor,
        )
        self.workers: list[Worker] = []
        self.runners: list[asyncio.Task[None]] = []
        self._stop_lock = asyncio.Lock()
        self._closed = False

    async def start(self) -> None:
        if self._closed or self.workers:
            raise RuntimeError("Temporal Worker host is closed or already running")
        for execution_class in sorted(self.execution_classes):
            periodic = self.periodic if execution_class == "periodic" else None
            worker = Worker(
                self.client,
                task_queue=f"{self.ledger.config.task_queue_prefix}.{execution_class}",
                workflows=[P3PeriodicWorkflow]
                if periodic
                else [P3TaskWorkflow, EventDeliveryWorkflow],
                activities=[periodic.run_periodic_batch, periodic.reconcile_execution_status]
                if periodic
                else [
                    self.activities.plan,
                    self.activities.execute_step,
                    self.activities.finish_workflow,
                ],
                activity_executor=self.executor,
                workflow_task_executor=self.workflow_executor,
                max_concurrent_activities=1
                if periodic
                else self.ledger.tasks.class_limits[execution_class] * 4,
                max_concurrent_workflow_tasks=self.workflow_slots_per_queue,
                max_heartbeat_throttle_interval=timedelta(seconds=1),
                default_heartbeat_throttle_interval=timedelta(seconds=1),
                graceful_shutdown_timeout=timedelta(seconds=self.shutdown_seconds),
            )
            self.workers.append(worker)
            self.runners.append(asyncio.create_task(worker.run()))
        await asyncio.sleep(0)
        for runner in self.runners:
            if runner.done():
                runner.result()

    async def stop(self) -> None:
        async with self._stop_lock:
            if self._closed:
                return
            try:
                await asyncio.gather(
                    *(worker.shutdown() for worker in self.workers), return_exceptions=True
                )
                # A failed runner must still be observed and all other queues stopped.
                await asyncio.gather(*self.runners, return_exceptions=True)
            finally:
                self.workers.clear()
                self.runners.clear()
                self.executor.shutdown(wait=False, cancel_futures=True)
                self.workflow_executor.shutdown(wait=False, cancel_futures=True)
                self._closed = True
