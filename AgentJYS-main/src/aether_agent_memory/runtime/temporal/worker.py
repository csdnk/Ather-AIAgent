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
        self.workflow_executor = ThreadPoolExecutor(max_workers=8, thread_name_prefix="p3-workflow")
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

    async def start(self) -> None:
        if self.workers:
            raise RuntimeError("Temporal Worker already running")
        classes = {self.ledger.tasks.handlers[kind][0] for kind in self.registry.routes}
        if self.periodic is not None:
            classes.add("periodic")
        for execution_class in sorted(classes):
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
                max_concurrent_workflow_tasks=8,
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
        await asyncio.gather(*(worker.shutdown() for worker in self.workers))
        await asyncio.gather(*self.runners)
        self.workers.clear()
        self.runners.clear()
        self.executor.shutdown(wait=False, cancel_futures=True)
        self.workflow_executor.shutdown(wait=False, cancel_futures=True)
