"""One P3 process: authenticated admission, SDK Workers and intent transport."""

import asyncio
import time
from collections.abc import Callable, MutableMapping
from contextlib import suppress
from datetime import timedelta
from typing import Any

from pydantic import JsonValue
from temporalio.client import Client

from aether_agent_memory.operate.basic.maintenance import CacheMaintenance
from aether_agent_memory.recall.contracts.models import RecallRequest
from aether_agent_memory.remember.basic.pipeline import RememberPipeline
from aether_agent_memory.remember.contracts.models import CorrectionRequest, RememberRequest
from aether_agent_memory.runtime.contracts.client_admission import (
    ClientDocumentTarget,
    ClientOperationTargets,
)
from aether_agent_memory.runtime.contracts.http_evidence import HttpRequestEvidence
from aether_agent_memory.runtime.contracts.models import (
    ErrorCode,
    Permission,
    TaskRecord,
    TaskState,
    TrustedContext,
)
from aether_agent_memory.runtime.contracts.operation_lookup import HTTPCommandKind, OperationLookup
from aether_agent_memory.runtime.flows.config import ServiceConfiguration
from aether_agent_memory.runtime.flows.host import ThreeFlows
from aether_agent_memory.runtime.foundation.common import FoundationError, encode, fingerprint, now
from aether_agent_memory.runtime.foundation.requests import select_scope
from aether_agent_memory.runtime.foundation.tasks import TERMINAL
from aether_agent_memory.runtime.storage.ports import MetadataTransaction

from .bridge import IntentBridge
from .config import deployment_configuration
from .controls import ControlAdmission
from .events import register_events
from .gateway import TemporalGateway, connect_client
from .ingress import CommandAdmission, InputStore, register_ingress
from .ledger import ExecutionLedger
from .models import PeriodicState, StepRequest, WorkflowInput
from .operate import register_operate
from .periodic import PeriodicActivities, PeriodicController, register_p3_periodic
from .recall import RecallAdmission, register_recall
from .registry import StageRegistry
from .remember import register_remember
from .worker import WorkerHost


class PendingOperationError(FoundationError):
    def __init__(self, job_id: str) -> None:
        super().__init__(ErrorCode.REQUEST_IN_PROGRESS, "operation continues in Temporal")
        self.job_id = job_id


class TemporalService:
    def __init__(
        self,
        runtime: ThreeFlows,
        config: ServiceConfiguration,
        reload_identity: Callable[[], None],
        maintenance: CacheMaintenance,
    ) -> None:
        self.runtime, self.config, self.reload_identity = runtime, config, reload_identity
        if not isinstance(runtime.remember, RememberPipeline):
            raise ValueError("Temporal service requires the complete Remember pipeline")
        remember = runtime.remember
        isolated = deployment_configuration(config.temporal)
        self.ledger = ExecutionLedger(runtime.foundation.tasks, isolated)
        self.controls = ControlAdmission(self.ledger, config.maintenance_principals)
        runtime.foundation.tasks.on_recovery = self.controls.recovery
        self.inputs = InputStore(
            runtime.foundation.uow,
            runtime.foundation.identity,
            config.data_dir / "inputs",
            max_bytes=remember.policy.max_input_bytes,
            objects=remember.bodies.p2,
        )
        self.registry = StageRegistry()
        register_ingress(self.registry, self.ledger, self.inputs, remember)
        register_remember(self.registry, remember)
        self.recall = RecallAdmission(self.ledger, runtime.recall)
        register_recall(self.registry, runtime.recall)
        register_operate(self.registry, runtime.operate, maintenance)
        self.events = register_events(self.registry, self.ledger, runtime.foundation.events)
        self.registry.validate_catalog(set(runtime.foundation.tasks.handlers))
        self.commands = CommandAdmission(self.ledger, self.inputs, self.client_targets)
        self.maintenance = maintenance
        runtime.foundation.tasks.on_admitted = self.on_admitted
        runtime.execution = self
        with runtime.foundation.uow.transaction() as tx:
            marker = {
                "backend": "temporal",
                "deployment_id": isolated.deployment_id,
                "namespace": isolated.namespace,
                "task_queue_prefix": isolated.task_queue_prefix,
            }
            prior = tx.read("meta", "execution_backend")
            if prior is not None and prior != marker:
                raise ValueError("Temporal backend binding changed")
            tx.write("meta", "execution_backend", marker)
        self.client: Client | None = None
        self.gateway: TemporalGateway | None = None
        self.bridge: IntentBridge | None = None
        self.workers: WorkerHost | None = None
        self.periodic: PeriodicController | None = None
        self.runner: asyncio.Task[None] | None = None
        self.stopped = asyncio.Event()
        self.wakeup = asyncio.Event()
        self.dispatch_progress = False
        self.accepting = False
        self.checked = 0.0
        self.identity_checked = 0.0
        self._refresh_lock = asyncio.Lock()
        self.state: dict[str, Any] = {
            "worker": "starting",
            "identity": "starting",
            "lanes": {},
            "temporal": "starting",
            "reason_code": "STARTING",
        }

    def on_admitted(self, tx: Any, task: TaskRecord) -> None:
        if task.kind not in self.registry.routes:
            tx.abort(ErrorCode.CONTRACT_VIOLATION, "task kind has no Temporal stage plan")
        self.ledger.bind_admitted(tx, task)

    def ready(self) -> dict[str, Any]:
        fresh = time.monotonic() - self.checked <= max(
            2, self.config.temporal.connect_timeout_seconds * 2 + self.config.poll_seconds * 2
        )
        ready = self.accepting and self.state["worker"] == "running" and fresh
        return {
            "readiness": "ready" if ready else "not_ready",
            "reason_code": "READY"
            if ready
            else self.state["reason_code"]
            if fresh
            else "STATUS_STALE",
        }

    def require_ready(self) -> None:
        if self.ready()["readiness"] != "ready":
            raise FoundationError(
                ErrorCode.DEPENDENCY_UNAVAILABLE, "Temporal execution unavailable"
            )

    async def connect(self) -> None:
        self.client = await connect_client(self.config.temporal)
        self.gateway = TemporalGateway(self.client, self.ledger)
        self.bridge = IntentBridge(self.ledger, self.gateway)
        periodic = PeriodicActivities(self.ledger, self.gateway)
        await asyncio.to_thread(
            register_p3_periodic,
            periodic,
            self.runtime,
            self.maintenance,
            self.config.maintenance_principals if self.config.automatic_cache_repair else (),
        )
        self.workers = WorkerHost(
            self.client,
            self.ledger,
            self.registry,
            shutdown_seconds=self.config.shutdown_seconds,
            periodic=periodic,
        )
        try:
            await self.workers.start()
            await self.bridge.flush()
            self.periodic = PeriodicController(self.gateway)
            await self.periodic.start(
                PeriodicState(
                    deployment_id=self.config.temporal.deployment_id,
                    interval_seconds=self.config.periodic_seconds,
                )
            )
        except BaseException:
            await self.workers.stop()
            self.workers = None
            self.client = None
            raise

    async def refresh(self) -> None:
        async with self._refresh_lock:
            await self._refresh()

    async def _refresh(self) -> None:
        try:
            if time.monotonic() - self.identity_checked >= self.config.identity_reload_seconds:
                await asyncio.to_thread(self.reload_identity)
                self.identity_checked = time.monotonic()
            self.state["identity"] = "ready"
        except Exception:
            self.accepting = False
            self.state.update(
                worker="degraded", identity="degraded", reason_code="IDENTITY_UNAVAILABLE"
            )
            self.checked = time.monotonic()
            return
        try:
            if self.workers is not None and (
                not self.workers.runners or any(runner.done() for runner in self.workers.runners)
            ):
                self.accepting = False
                self.state.update(worker="restarting", reason_code="WORKER_RESTARTING")
                for runner in self.workers.runners:
                    if runner.done() and not runner.cancelled():
                        error = runner.exception()
                        if error is not None:
                            self.state["last_worker_error_type"] = type(error).__name__
                await self.workers.stop()
                self.workers = None
                self.client = None
                self.gateway = None
                self.bridge = None
                self.periodic = None
                self.state["worker_restarts"] = self.state.get("worker_restarts", 0) + 1
            if self.client is None:
                await self.connect()
            assert self.client is not None and self.bridge is not None and self.workers is not None
            if not await self.client.service_client.check_health(
                timeout=timedelta(seconds=self.config.temporal.connect_timeout_seconds)
            ):
                raise ConnectionError("Temporal health unavailable")
            for runner in self.workers.runners:
                if runner.done():
                    runner.result()
                    raise RuntimeError("Temporal Worker stopped")
            self.dispatch_progress = bool(await self.bridge.flush())
            self.state.update(
                worker="running",
                temporal="available",
                reason_code="READY",
                lanes={name: "running" for name in self.ledger.tasks.class_limits},
            )
            await asyncio.to_thread(self.publish_worker_heartbeats)
            self.accepting = not self.stopped.is_set()
        except Exception as exc:
            self.accepting = False
            self.state.update(
                worker="degraded",
                temporal="unavailable",
                reason_code="TEMPORAL_UNAVAILABLE",
                error_type=type(exc).__name__,
            )
        self.checked = time.monotonic()

    def publish_worker_heartbeats(self) -> None:
        with self.runtime.foundation.uow.transaction() as tx:
            for name in self.ledger.tasks.class_limits:
                worker_id = f"temporal_{self.config.temporal.deployment_id}_{name}"
                self.ledger.tasks.progress.heartbeat(tx, worker_id, name)
                tx.write(
                    "workers",
                    worker_id,
                    {
                        "state": "polling",
                        "worker_id": worker_id,
                        "execution_class": name,
                        "last_seen": now(),
                        "backend": "temporal",
                    },
                )

    async def start(self) -> None:
        if self.runner is not None:
            raise RuntimeError("Temporal service already started")
        self.stopped.clear()
        await self.refresh()
        self.runner = asyncio.create_task(self.coordinate(), name="p3-temporal-intents")

    async def coordinate(self) -> None:
        while not self.stopped.is_set():
            with suppress(TimeoutError):
                await asyncio.wait_for(
                    self.wakeup.wait(),
                    (0.05 if self.dispatch_progress else self.config.poll_seconds)
                    if self.accepting
                    else 1,
                )
            self.wakeup.clear()
            if not self.stopped.is_set():
                await self.refresh()

    async def stop(self) -> None:
        self.accepting = False
        self.stopped.set()
        self.wakeup.set()
        if self.runner is not None:
            self.runner.cancel()
            await asyncio.gather(self.runner, return_exceptions=True)
            self.runner = None
        if self.workers is not None:
            await self.workers.stop()
            self.workers = None
        self.client = None
        self.state.update(worker="stopped", reason_code="STOPPED")
        await asyncio.to_thread(self.publish_workers_stopped)

    def publish_workers_stopped(self) -> None:
        with self.runtime.foundation.uow.transaction() as tx:
            for name in self.ledger.tasks.class_limits:
                key = f"temporal_{self.config.temporal.deployment_id}_{name}"
                row = tx.read("workers", key)
                if row:
                    tx.write("workers", key, {**row, "state": "stopped"})

    async def drain(self, timeout_seconds: float = 30) -> None:
        """Wait for durable work through SDK Workers; never claim tasks locally."""
        started_here = self.runner is None
        try:
            if started_here:
                await self.start()
            self.require_ready()
            async with asyncio.timeout(timeout_seconds):
                quiet = 0
                while quiet < 3:
                    assert self.bridge is not None
                    await self.bridge.flush()

                    def busy() -> bool:
                        with self.runtime.foundation.uow.transaction() as tx:
                            return bool(tx.active_task_rows() or tx.pending_delivery_rows())

                    quiet = 0 if await asyncio.to_thread(busy) else quiet + 1
                    await asyncio.sleep(0.05)
        finally:
            if started_here:
                await self.stop()

    def client_targets(
        self, tx: MetadataTransaction, ctx: TrustedContext, kind: str, payload: JsonValue
    ) -> ClientOperationTargets:
        if kind == "remember.save":
            request = RememberRequest.model_validate(payload)
            return ClientOperationTargets(scopes=(select_scope(ctx, request.selection),))
        if kind == "remember.correct":
            memory_id = payload.get("memory_id") if isinstance(payload, dict) else None
            if not isinstance(payload, dict) or not isinstance(memory_id, str):
                raise FoundationError(ErrorCode.INVALID_ARGUMENT, "correction input invalid")
            CorrectionRequest.model_validate(payload.get("request"))
            ref = self.runtime.remember.current_ref(tx, ctx, memory_id)
            return ClientOperationTargets(scopes=(ref.scope,))
        if kind == "remember.document" and isinstance(payload, dict):
            target = ClientDocumentTarget.model_validate(
                {"scope": ctx.principal.home_scope, "document_id": payload.get("document_id")}
            )
            return ClientOperationTargets(documents=(target,))
        raise FoundationError(ErrorCode.INVALID_ARGUMENT, "unsupported HTTP command target")

    def check_client_admission(
        self,
        ctx: TrustedContext,
        kind: str,
        http_request: HttpRequestEvidence | None,
        payload: JsonValue,
    ) -> None:
        # Reject stale/changed inputs before immutable P2 staging can occupy their ID.
        # The accepting ledger transaction must check again after external I/O.
        if http_request is not None:
            with self.runtime.foundation.uow.transaction() as tx:
                self.ledger.clients.require(
                    tx, ctx, http_request, kind, targets=self.client_targets(tx, ctx, kind, payload)
                )

    def accept(
        self,
        ctx: TrustedContext,
        kind: str,
        payload: JsonValue,
        *,
        http_request: HttpRequestEvidence | None = None,
    ) -> WorkflowInput:
        self.require_ready()
        if kind == "recall.execute":
            return self.recall.accept(
                ctx, RecallRequest.model_validate(payload), http_request=http_request
            )
        self.check_client_admission(ctx, kind, http_request, payload)
        if kind == "remember.save":
            RememberRequest.model_validate(payload)
        elif kind == "remember.correct":
            if not isinstance(payload, dict) or not isinstance(payload.get("memory_id"), str):
                raise FoundationError(ErrorCode.INVALID_ARGUMENT, "correction input invalid")
            CorrectionRequest.model_validate(payload.get("request"))
        elif kind != "remember.document":
            raise FoundationError(ErrorCode.INVALID_ARGUMENT, "unsupported HTTP command")
        ref = self.inputs.persist(
            ctx, ctx.operation_id, encode(payload).encode("utf-8"), "application/json"
        )
        return self.commands.accept(ctx, kind, ref, http_request=http_request)

    def status(self, ctx: TrustedContext, job_id: str) -> TaskRecord:
        with self.runtime.foundation.uow.transaction() as tx:
            _, task = self.ledger.tasks.load(tx, job_id)
            self.runtime.foundation.identity.authorize(tx, ctx, Permission.READ, task.subject)
            return task

    def lookup_operation(
        self, ctx: TrustedContext, operation_id: str, kind: HTTPCommandKind
    ) -> OperationLookup:
        from .operation_lookup import lookup_operation

        return lookup_operation(self.ledger, self.inputs, ctx, operation_id, kind)

    def operation(self, ctx: TrustedContext, job_id: str) -> dict[str, Any]:
        task = self.status(ctx, job_id)
        with self.runtime.foundation.uow.transaction() as tx:
            self.runtime.foundation.identity.revalidate(tx, ctx)
            bound = tx.read("temporal_bindings", job_id)
            diagnostic = tx.read("temporal_diagnostics", bound["workflow_id"]) if bound else None
        return {
            **task.model_dump(mode="json"),
            "temporal": {
                "workflow_id": bound["workflow_id"] if bound else None,
                "binding": bound["binding"] if bound else None,
                "diagnostic": diagnostic,
            },
        }

    def result(self, ctx: TrustedContext, job_id: str) -> JsonValue:
        task = self.status(ctx, job_id)
        if task.kind == "recall.execute":
            try:
                return self.recall.read_result(ctx, job_id).model_dump(mode="json")
            except FoundationError as exc:
                if exc.code == ErrorCode.REQUEST_IN_PROGRESS:
                    raise PendingOperationError(job_id) from None
                raise
        if task.state not in TERMINAL:
            raise PendingOperationError(job_id)
        if task.state != TaskState.SUCCEEDED:
            raise FoundationError(
                task.error_code or ErrorCode.EXECUTION_INTERRUPTED,
                "operation has no successful result",
            )
        with self.runtime.foundation.uow.transaction() as tx:
            self.runtime.foundation.identity.authorize(tx, ctx, Permission.READ, task.subject)
            if fingerprint(tx.get(task.input_ref)) != task.input_hash:
                tx.abort(ErrorCode.VERSION_CONFLICT, "operation input changed")
            if task.kind in {"remember.save", "remember.correct", "remember.document"}:
                binding = tx.read("temporal_bindings", job_id)
                if not binding or "stage" not in binding:
                    tx.abort(ErrorCode.VERSION_CONFLICT, "command completion binding missing")
                receipt = self.ledger.load_step(
                    tx,
                    StepRequest(
                        job=WorkflowInput.model_validate(binding["job"]),
                        stage=binding["stage"],
                        ordinal=binding["ordinal"],
                        mode="execute",
                    ),
                )
                if receipt is None or receipt.result_ref != task.result_ref:
                    tx.abort(ErrorCode.VERSION_CONFLICT, "command completion receipt differs")
            if task.result_ref is None:
                tx.abort(ErrorCode.CONTRACT_VIOLATION, "result reference missing")
            self.runtime.foundation.identity.authorize(tx, ctx, Permission.READ, task.result_ref)
            result = tx.get(task.result_ref)
            if result is None:
                tx.abort(ErrorCode.CONTRACT_VIOLATION, "result missing")
            return result

    async def await_result(
        self, ctx: TrustedContext, job_id: str, timeout_seconds: float
    ) -> JsonValue:
        try:
            async with asyncio.timeout(timeout_seconds):
                while True:
                    try:
                        return await asyncio.to_thread(self.result, ctx, job_id)
                    except PendingOperationError:
                        await asyncio.sleep(0.025)
        except TimeoutError:
            raise PendingOperationError(job_id) from None

    async def execute(
        self,
        ctx: TrustedContext,
        kind: str,
        payload: JsonValue,
        headers: MutableMapping[str, str] | None = None,
        *,
        http_request: HttpRequestEvidence | None = None,
    ) -> JsonValue:
        job = await asyncio.to_thread(self.accept, ctx, kind, payload, http_request=http_request)
        self.wakeup.set()
        if headers is not None:
            headers["Location"] = f"/p3/operations/{job.job_id}"
            headers["X-P3-Job-ID"] = job.job_id
        return await self.await_result(ctx, job.job_id, self.config.http_wait_seconds)

    async def upload(
        self,
        ctx: TrustedContext,
        document_id: str,
        version: str,
        data: bytes,
        media_type: str,
        headers: MutableMapping[str, str] | None = None,
        *,
        http_request: HttpRequestEvidence | None = None,
    ) -> JsonValue:
        self.require_ready()
        await asyncio.to_thread(
            self.check_client_admission,
            ctx,
            "remember.document",
            http_request,
            {"document_id": document_id},
        )
        blob = await asyncio.to_thread(
            self.inputs.persist, ctx, ctx.operation_id + ":blob", data, media_type
        )
        return await self.execute(
            ctx,
            "remember.document",
            {
                "document_id": document_id,
                "version": version,
                "media_type": media_type,
                "blob_ref": blob.model_dump(mode="json"),
            },
            headers,
            http_request=http_request,
        )
