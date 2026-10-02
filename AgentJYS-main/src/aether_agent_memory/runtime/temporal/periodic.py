"""Bounded periodic items, committed cursors and technical/business diagnostics."""

import asyncio
import json
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

from temporalio import activity
from temporalio.common import WorkflowIDConflictPolicy, WorkflowIDReusePolicy
from temporalio.exceptions import ApplicationError, WorkflowAlreadyStartedError

from aether_agent_memory.runtime.contracts.models import (
    EffectStatus,
    ErrorCode,
    Principal,
    TaskRecord,
    TaskState,
    TrustedContext,
)
from aether_agent_memory.runtime.foundation.common import FoundationError, fingerprint, later
from aether_agent_memory.runtime.foundation.storage import SQLiteTransaction
from aether_agent_memory.runtime.foundation.tasks import TERMINAL

from .gateway import TemporalGateway
from .ledger import ExecutionLedger
from .models import ExecutionStatus, PeriodicState, WorkflowBinding
from .periodic_workflow import P3PeriodicWorkflow as P3PeriodicWorkflow

Commit = Callable[[SQLiteTransaction, str, int, Any], Any]
Prepare = Callable[[str], Awaitable[Any]]
_TERMINAL_STATUS_RECHECK_SECONDS = 900
_TERMINAL_STATUS_REFRESH_BUDGET = 16

if TYPE_CHECKING:
    from aether_agent_memory.operate.basic.maintenance import CacheMaintenance
    from aether_agent_memory.runtime.flows.host import ThreeFlows


class PeriodicActivities:
    def __init__(self, ledger: ExecutionLedger, gateway: TemporalGateway | None) -> None:
        self.ledger, self.gateway = ledger, gateway
        self.routes: dict[str, tuple[Commit, Prepare | None]] = {}

    def register(self, table: str, commit: Commit, prepare: Prepare | None = None) -> None:
        if table in self.routes:
            raise ValueError("periodic route already registered")
        self.routes[table] = commit, prepare

    @staticmethod
    def _terminal_status_proof(
        binding: WorkflowBinding, task: TaskRecord, diagnostic: dict[str, Any]
    ) -> str | None:
        expected = {
            TaskState.SUCCEEDED: "completed",
            TaskState.FAILED: "failed",
            TaskState.CANCELLED: "cancelled",
        }.get(task.state)
        if (
            expected is None
            or task.effect_status == EffectStatus.UNKNOWN
            or diagnostic.get("reason_code") != "IN_SYNC"
            or diagnostic.get("technical_state") != expected
            or diagnostic.get("run_id") != binding.current_run_id
        ):
            return None
        return fingerprint(
            [binding.model_dump(mode="json"), task.model_dump(mode="json"), diagnostic]
        )

    async def prepare_status(self, key: str, *, tick: int | None = None) -> Any:
        refresh = None
        with self.ledger.tasks.uow.transaction() as tx:
            row = tx.read("temporal_bindings", key)
            binding = (
                WorkflowBinding.model_validate(row["binding"]) if row and row["binding"] else None
            )
            if binding is None:
                return None
            _, task = self.ledger.tasks.load(tx, key)
            diagnostic = tx.read("temporal_diagnostics", binding.workflow_id) or {}
            proof = self._terminal_status_proof(binding, task, diagnostic)
            checked = tx.read("temporal_status_checks", binding.workflow_id) or {}
            stamp = self.ledger.tasks.clock()
            if (
                proof is not None
                and checked.get("proof") == proof
                and checked.get("checked_at", "") <= stamp < checked.get("recheck_at", "")
            ):
                return None
            # Cold caches and simultaneous expiry must not turn one periodic
            # sweep into thousands of remote calls. Known inconsistencies and
            # unresolved/active work bypass this historical terminal budget.
            historical = (
                task.state in {TaskState.SUCCEEDED, TaskState.FAILED, TaskState.CANCELLED}
                and task.effect_status != EffectStatus.UNKNOWN
                and (not diagnostic or proof is not None)
                and (not checked or checked.get("proof") == proof)
            )
            if tick is not None and historical:
                deployment = self.ledger.config.deployment_id
                budget = tx.read("temporal_status_refresh", deployment) or {
                    "cursor": "",
                    "tick": tick,
                    "used": 0,
                }
                if budget["tick"] != tick:
                    budget = {**budget, "tick": tick, "used": 0}
                if key <= budget["cursor"] or budget["used"] >= _TERMINAL_STATUS_REFRESH_BUDGET:
                    return None
                refresh = {"cursor": key, "tick": tick, "used": budget["used"] + 1}
        if self.gateway is None:
            raise ConnectionError("Temporal status unavailable")
        return binding, await self.gateway.describe(binding), refresh

    def _finish_status_sweep(self, tx: SQLiteTransaction, tick: int) -> None:
        deployment = self.ledger.config.deployment_id
        budget = tx.read("temporal_status_refresh", deployment)
        # Rewind only after the tail has been inspected without exhausting the
        # quota. Otherwise retain the last checked key for the next whole sweep.
        if budget is not None and (
            budget["tick"] != tick or budget["used"] < _TERMINAL_STATUS_REFRESH_BUDGET
        ):
            tx.write("temporal_status_refresh", deployment, {"cursor": "", "tick": tick, "used": 0})

    @activity.defn(name="p3.periodic_batch")
    async def run_periodic_batch(self, state: PeriodicState) -> PeriodicState:
        try:
            return await self.batch(state)
        except Exception:
            if not activity.in_activity():
                raise
            raise ApplicationError(
                "P3 periodic batch interrupted", type="P3_PERIODIC_INTERRUPTED"
            ) from None

    async def batch(self, state: PeriodicState) -> PeriodicState:
        if state.deployment_id != self.ledger.config.deployment_id or state.last_tick < 0:
            raise ValueError("periodic deployment or tick differs")
        uow = self.ledger.tasks.uow
        tick_key = fingerprint([state.deployment_id, state.last_tick])
        route_names = list(self.routes)
        with uow.transaction() as tx:
            prior = tx.read("temporal_ticks", tick_key)
            if prior:
                if (
                    prior["routes"] != route_names
                    or prior["interval_seconds"] != state.interval_seconds
                ):
                    tx.abort(ErrorCode.VERSION_CONFLICT, "periodic plan changed mid-batch")
                state = state.model_copy(update={"cursor": prior["cursor"]})
                if state.cursor is None:
                    return state
            epoch = (tx.read("temporal_periodic_epoch", state.deployment_id) or 0) + 1
            tx.write("temporal_periodic_epoch", state.deployment_id, epoch)
        route_index, cursor = json.loads(state.cursor) if state.cursor else (0, "")

        def save(tx: SQLiteTransaction, next_cursor: str | None) -> None:
            if tx.read("temporal_periodic_epoch", state.deployment_id) != epoch:
                tx.abort(ErrorCode.VERSION_CONFLICT, "periodic batch superseded")
            tx.write(
                "temporal_ticks",
                tick_key,
                {
                    "cursor": next_cursor,
                    "routes": route_names,
                    "interval_seconds": state.interval_seconds,
                },
            )

        remaining = state.batch_size
        while route_index < len(route_names):
            table = route_names[route_index]
            commit, prepare = self.routes[table]
            with uow.transaction() as tx:
                rows = tx.rows_after(table, cursor, limit=remaining)
            for key, _ in rows:
                next_cursor = json.dumps([route_index, key])
                try:
                    prepared = (
                        await self.prepare_status(key, tick=state.last_tick)
                        if prepare == self.prepare_status
                        else await prepare(key)
                        if prepare
                        else None
                    )
                    with uow.transaction() as tx:
                        save(tx, next_cursor)
                        # Cursor and local facts share a transaction; exceptions roll both back.
                        if tx.read(table, key) is not None:
                            commit(tx, key, state.last_tick, prepared)
                except FoundationError as exc:
                    if exc.code not in {
                        ErrorCode.FORBIDDEN,
                        ErrorCode.UNAUTHENTICATED,
                        ErrorCode.NOT_FOUND,
                    }:
                        raise
                    with uow.transaction() as tx:
                        save(tx, next_cursor)
                        tx.write(
                            "temporal_periodic_attention",
                            fingerprint([table, key]),
                            {"reason_code": exc.code.value, "tick": state.last_tick},
                        )
                cursor, remaining = key, remaining - 1
                # Local routes and status-cache hits may never suspend. Yield
                # only after item facts and cursor commit, so activity heartbeats
                # and other work can run without an open transaction.
                await asyncio.sleep(0)
                if remaining == 0:
                    return state.model_copy(update={"cursor": next_cursor})
            route_index, cursor = route_index + 1, ""
            with uow.transaction() as tx:
                save(tx, json.dumps([route_index, cursor]))
                if prepare == self.prepare_status:
                    self._finish_status_sweep(tx, state.last_tick)
        with uow.transaction() as tx:
            save(tx, None)
        return state.model_copy(update={"cursor": None})

    def diagnose(
        self,
        tx: SQLiteTransaction,
        binding: WorkflowBinding,
        status: ExecutionStatus,
        refresh: dict[str, Any] | None = None,
    ) -> None:
        job_id = binding.workflow_id.rsplit("/", 1)[-1]
        stored = tx.read("temporal_bindings", job_id)
        task = None
        reason = "IN_SYNC"
        if stored is None:
            reason = "BINDING_MISSING"
        else:
            if (
                not stored["binding"]
                or WorkflowBinding.model_validate(stored["binding"]).first_run_id
                != binding.first_run_id
            ):
                tx.abort(ErrorCode.VERSION_CONFLICT, "status belongs to another execution chain")
            task_row, task = self.ledger.tasks.load(tx, job_id)
            if status.state == "not_found":
                reason = "BOUND_HISTORY_MISSING"
            elif status.state != "running" and task.effect_status == EffectStatus.UNKNOWN:
                reason = "TECHNICAL_END_WITH_UNKNOWN_EFFECT"
            elif (task.state in TERMINAL) != (status.state != "running"):
                reason = "BUSINESS_TECHNICAL_STATE_MISMATCH"
            elif task.state == "succeeded" and status.state != "completed":
                reason = "BUSINESS_COMPLETED_TECHNICAL_END"
            elif status.state == "completed" and task.state != "succeeded":
                reason = "BUSINESS_OUTCOME_REQUIRES_ATTENTION"
            if task.state in TERMINAL:
                self.ledger.tasks.project_terminal(tx, task)
            elif status.state != "running":
                # A technical end never establishes a business success or no-effect fact.
                state = (
                    TaskState.ATTENTION
                    if task.effect_status == EffectStatus.UNKNOWN
                    or status.state in {"not_found", "completed"}
                    else TaskState.FAILED
                )
                if task.state == TaskState.PENDING:
                    state = TaskState.FAILED
                self.ledger.tasks.change(
                    tx,
                    {**task_row, "terminal_reason": reason},
                    task,
                    state=state,
                    error_code=ErrorCode.EXECUTION_INTERRUPTED,
                )
        diagnostic = {
            "reason_code": reason,
            "technical_state": status.state,
            "run_id": status.run_id,
        }
        tx.write("temporal_diagnostics", binding.workflow_id, diagnostic)
        # Keep the public diagnostic contract unchanged. The private proof is
        # committed with the verified facts, never on an unverified cache miss.
        proof = (
            self._terminal_status_proof(binding, task, diagnostic)
            if task is not None
            and stored is not None
            and stored["binding"] == binding.model_dump(mode="json")
            else None
        )
        if proof is not None:
            stamp = self.ledger.tasks.clock()
            tx.write(
                "temporal_status_checks",
                binding.workflow_id,
                {
                    "proof": proof,
                    "checked_at": stamp,
                    "recheck_at": later(stamp, _TERMINAL_STATUS_RECHECK_SECONDS),
                },
            )
        elif tx.read("temporal_status_checks", binding.workflow_id):
            tx.write("temporal_status_checks", binding.workflow_id, {})
        if refresh is not None:
            # Commit the refresh cursor with the diagnostic and batch cursor.
            # Failed RPCs or rolled-back diagnoses must remain eligible for retry.
            tx.write("temporal_status_refresh", self.ledger.config.deployment_id, refresh)

    @activity.defn(name="p3.reconcile_execution_status")
    async def reconcile_execution_status(self, binding: WorkflowBinding) -> ExecutionStatus:
        try:
            if self.gateway is None:
                raise RuntimeError("Temporal status gateway unavailable")
            status = await self.gateway.describe(binding)
            with self.ledger.tasks.uow.transaction() as tx:
                self.diagnose(tx, binding, status)
            return status
        except Exception:
            if not activity.in_activity():
                raise
            raise ApplicationError(
                "P3 execution status unavailable", type="P3_STATUS_UNAVAILABLE"
            ) from None


def register_p3_periodic(
    runner: PeriodicActivities,
    host: "ThreeFlows",
    maintenance: "CacheMaintenance",
    principal_ids: tuple[str, ...],
) -> None:
    """Explicit P3-only allowlist. No generic repair registry or P2/database recovery."""
    from aether_agent_memory.operate.basic.continuous import ContinuousOperate
    from aether_agent_memory.remember.basic.pipeline import RememberPipeline

    if not isinstance(host.remember, RememberPipeline) or not isinstance(
        host.operate, ContinuousOperate
    ):
        raise ValueError("periodic P3 requires the full domain implementations")
    remember, operate = host.remember, host.operate
    runner.register("remember_pending", lambda tx, key, tick, _: remember.periodic_pending(tx, key))
    runner.register(
        "remember_retention_enrollment",
        lambda tx, key, tick, _: remember.retention.periodic_item(tx, key),
    )
    runner.register(
        "remember_reflection_policies",
        lambda tx, key, tick, _: remember.reflection.periodic_item(tx, key),
    )
    runner.register(
        "operate_views", lambda tx, key, tick, _: operate.periodic_item(tx, key, str(tick))
    )

    async def sample(key: str) -> Any:
        with host.foundation.uow.transaction() as tx:
            row = tx.read("identities", key)
            if key not in principal_ids or not row or not row["enabled"]:
                tx.abort(ErrorCode.FORBIDDEN, "maintenance identity unavailable")
            operation = fingerprint(["cache_sample", key])
            ctx = TrustedContext(
                principal=Principal.model_validate(row["principal"]),
                request_id=operation,
                operation_id=operation,
                trace_id=operation[:32],
                span_id=operation[:16],
                deadline_at=later(host.foundation.tasks.clock(), 30),
            )
            host.foundation.identity.revalidate(tx, ctx)
        return ctx, await maintenance.sample_batch(ctx)

    def observed(tx: SQLiteTransaction, key: str, tick: int, prepared: Any) -> None:
        ctx, (observations, cursor_key, cursor) = prepared
        host.foundation.identity.revalidate(tx, ctx)
        for subject, observation in observations:
            host.foundation.dispositions.observe_in(tx, ctx, subject, observation)
        tx.write("cache_sample_cursors", cursor_key, cursor)

    with host.foundation.uow.transaction() as tx:
        for principal_id in principal_ids:
            tx.write("temporal_maintenance_principals", principal_id, {"enabled": True})
    runner.register("temporal_maintenance_principals", observed, sample)

    def diagnostic(tx: SQLiteTransaction, key: str, tick: int, prepared: Any) -> None:
        if prepared is not None:
            runner.diagnose(tx, *prepared)

    runner.register("temporal_bindings", diagnostic, runner.prepare_status)


class PeriodicController:
    def __init__(self, gateway: TemporalGateway) -> None:
        self.gateway, self.ledger = gateway, gateway.ledger

    async def start(self, state: PeriodicState) -> WorkflowBinding:
        self.gateway.outside_transaction()
        if state.deployment_id != self.ledger.config.deployment_id:
            raise ValueError("periodic deployment differs")
        deployment = state.deployment_id
        plan = state.model_dump(
            mode="json", exclude={"last_tick", "cursor", "acknowledged_controls"}
        )
        digest = fingerprint(plan)
        workflow_id = f"p3/{deployment}/periodic"
        queue = f"{self.ledger.config.task_queue_prefix}.periodic"
        with self.ledger.tasks.uow.transaction() as tx:
            stored = tx.read("temporal_periodic_binding", deployment)
            if stored and stored["plan"] != plan:
                tx.abort(ErrorCode.VERSION_CONFLICT, "running periodic plan changed")
            if stored is None:
                stored = {"plan": plan, "binding": None}
                tx.write("temporal_periodic_binding", deployment, stored)
        if stored["binding"]:
            binding = WorkflowBinding.model_validate(stored["binding"])
        else:
            try:
                handle = await self.gateway.client.start_workflow(
                    P3PeriodicWorkflow.run,
                    state,
                    id=workflow_id,
                    task_queue=queue,
                    memo={"p3_periodic": digest},
                    id_reuse_policy=WorkflowIDReusePolicy.REJECT_DUPLICATE,
                    id_conflict_policy=WorkflowIDConflictPolicy.FAIL,
                    rpc_timeout=self.gateway.timeout,
                )
                run_id = handle.first_execution_run_id
            except WorkflowAlreadyStartedError as exc:
                run_id = exc.run_id
            description = await self.gateway.client.get_workflow_handle(
                workflow_id, run_id=run_id
            ).describe(rpc_timeout=self.gateway.timeout)
            if (
                await description.memo_value("p3_periodic", None) != digest
                or description.task_queue != queue
                or description.workflow_type != "P3PeriodicWorkflow"
            ):
                raise FoundationError(
                    ErrorCode.VERSION_CONFLICT, "periodic execution binding differs"
                )
            binding = WorkflowBinding(
                namespace=self.ledger.config.namespace,
                workflow_id=workflow_id,
                first_run_id=description.raw_info.first_run_id or description.run_id,
                current_run_id=description.run_id,
                input_hash=digest,
                plan_version="1",
            )
        if binding.input_hash != digest:
            raise FoundationError(ErrorCode.VERSION_CONFLICT, "periodic plan digest differs")
        status = await self.gateway.describe_periodic(binding)
        if status.state != "running":
            raise FoundationError(
                ErrorCode.INVALID_ARGUMENT, "periodic execution is closed or its history is missing"
            )
        binding = binding.model_copy(update={"current_run_id": status.run_id})
        with self.ledger.tasks.uow.transaction() as tx:
            latest = tx.read("temporal_periodic_binding", deployment) or {}
            tx.write(
                "temporal_periodic_binding",
                deployment,
                {**latest, "plan": plan, "binding": binding.model_dump(mode="json")},
            )
        return binding
