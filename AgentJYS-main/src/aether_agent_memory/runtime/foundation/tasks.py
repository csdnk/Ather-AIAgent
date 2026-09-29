"""Durable task execution and fenced, domain-directed recovery on one SQLite host."""

from __future__ import annotations

import asyncio
import secrets
from collections.abc import Callable
from datetime import datetime
from typing import Any

from aether_agent_memory.runtime.contracts.models import (
    DiagnosticRecord,
    EffectStatus,
    ErrorCode,
    Flow,
    Lease,
    MaintenanceRecord,
    OperationRecord,
    Permission,
    RecordRef,
    RecoveryAction,
    RecoveryDecision,
    RecoveryRequest,
    RunResult,
    TaskRecord,
    TaskSpec,
    TaskState,
    TrustedContext,
)
from aether_agent_memory.runtime.contracts.ports import TaskHandler, Transaction
from aether_agent_memory.runtime.contracts.rules import TASK_TRANSITIONS

from .common import FoundationError, fingerprint, later, now
from .identity import Identity
from .storage import SQLiteTransaction, SQLiteUnitOfWork, native
from .telemetry import Telemetry, observed

TERMINAL = {TaskState.SUCCEEDED, TaskState.FAILED, TaskState.CANCELLED, TaskState.ATTENTION}


@observed("runtime.tasks")
class Tasks:
    def __init__(
        self,
        uow: SQLiteUnitOfWork,
        identity: Identity,
        *,
        clock: Callable[[], str] = now,
        lease_seconds: float = 30,
        retry_seconds: float = 1,
        max_attempts: int = 3,
        query_max_attempts: int = 6,
        pending_limit: int = 100,
        class_limits: dict[str, int] | None = None,
        per_scope_running: int = 1,
        pending_limit_per_tenant: int = 300,
        per_tenant_running: int = 1,
    ) -> None:
        self.uow, self.identity, self.clock = uow, identity, clock
        if (
            min(
                lease_seconds,
                retry_seconds,
                max_attempts,
                query_max_attempts,
                pending_limit,
                per_scope_running,
                pending_limit_per_tenant,
                per_tenant_running,
            )
            <= 0
        ):
            raise ValueError("task limits must be positive")
        self.lease_seconds, self.retry_seconds = lease_seconds, retry_seconds
        self.max_attempts, self.query_max_attempts = max_attempts, query_max_attempts
        self.pending_limit, self.per_scope_running = pending_limit, per_scope_running
        self.pending_limit_per_tenant = pending_limit_per_tenant
        self.per_tenant_running = per_tenant_running
        self.class_limits = class_limits or {
            "remember": 2,
            "recall": 2,
            "operate": 1,
            "engineering": 1,
        }
        if any(limit <= 0 for limit in self.class_limits.values()):
            raise ValueError("class limits must be positive")
        self.handlers: dict[str, tuple[str, TaskHandler]] = {}
        self.permissions: dict[str, Permission] = {}
        from .task_progress import TaskProgress

        self.progress = TaskProgress(self, secrets.token_hex(8))

    def register(
        self,
        kind: str,
        execution_class: str,
        handler: TaskHandler,
        *,
        permission: Permission = Permission.WRITE,
    ) -> None:
        if kind in self.handlers or execution_class not in self.class_limits:
            raise ValueError("duplicate task kind or unknown execution class")
        self.handlers[kind] = (execution_class, handler)
        self.permissions[kind] = permission

    @staticmethod
    def load(tx: SQLiteTransaction, task_id: str) -> tuple[dict[str, Any], TaskRecord]:
        row = tx.read("tasks", task_id)
        if row is None:
            raise FoundationError(ErrorCode.NOT_FOUND, "task not found")
        return row, TaskRecord.model_validate(row["record"])

    def note(
        self,
        tx: SQLiteTransaction,
        task: TaskRecord,
        ctx: TrustedContext,
        stage: str,
        reason: str | None = None,
    ) -> None:
        record = DiagnosticRecord(
            record_id=secrets.token_hex(16),
            subject=task.subject,
            request_id=ctx.request_id,
            trace_id=ctx.trace_id,
            stage=stage,
            occurred_at=self.clock(),
            related=(
                RecordRef(
                    owner=Flow.RUNTIME,
                    object_type="task",
                    object_id=task.task_id,
                    scope=task.subject.scope,
                ),
            ),
            reason_code=reason,
            coverage="complete",
        )
        tx.write("diagnostics", record.record_id, record.model_dump(mode="json"))

    def change(
        self, tx: SQLiteTransaction, row: dict[str, Any], task: TaskRecord, **changes: Any
    ) -> TaskRecord:
        state = changes.get("state", task.state)
        if state != task.state and state not in TASK_TRANSITIONS[task.state]:
            tx.abort(ErrorCode.CONTRACT_VIOLATION, "illegal task state transition")
        updated = TaskRecord.model_validate(
            {**task.model_dump(), **changes, "revision": task.revision + 1}
        )
        tx.write("tasks", task.task_id, {**row, "record": updated.model_dump(mode="json")})
        return updated

    def enqueue(self, tx: Transaction, ctx: TrustedContext, spec: TaskSpec) -> TaskRecord:
        sql = native(tx)
        spec = TaskSpec.model_validate_json(spec.model_dump_json())
        self.identity.authorize(
            tx, ctx, self.permissions.get(spec.kind, Permission.WRITE), spec.subject
        )
        self.identity.authorize(tx, ctx, Permission.READ, spec.input_ref)
        if spec.kind not in self.handlers:
            sql.abort(ErrorCode.INVALID_ARGUMENT, "unregistered task kind")
        if (
            spec.initiator_id != ctx.principal.principal_id
            or spec.initiator_auth_epoch != ctx.principal.auth_epoch
            or spec.subject.scope != spec.input_ref.scope
            or spec.owner_flow != spec.subject.owner
        ):
            sql.abort(ErrorCode.CONTRACT_VIOLATION, "task identity/scope/owner mismatch")
        if (
            spec.deadline_at > ctx.deadline_at
            or spec.deadline_at <= self.clock()
            or spec.max_attempts > self.max_attempts
        ):
            sql.abort(ErrorCode.INVALID_ARGUMENT, "invalid task deadline or attempt budget")
        content = sql.get(spec.input_ref)
        if content is None or fingerprint(content) != spec.input_hash:
            sql.abort(ErrorCode.CONTRACT_VIOLATION, "input must exist and match its fingerprint")
        key = fingerprint(
            [
                spec.initiator_id,
                spec.subject.scope.model_dump(mode="json"),
                spec.kind,
                spec.idempotency_key,
            ]
        )
        signature = fingerprint(spec.model_dump(mode="json", exclude={"task_id", "deadline_at"}))
        old = sql.read("task_keys", key)
        if old:
            if old["signature"] != signature:
                sql.abort(ErrorCode.IDEMPOTENCY_CONFLICT, "same key with different task content")
            return self.load(sql, old["task_id"])[1]
        if sql.read("tasks", spec.task_id):
            sql.abort(ErrorCode.IDEMPOTENCY_CONFLICT, "task ID already used")
        active = [
            r
            for _, r in sql.rows("tasks")
            if r["record"]["subject"]["scope"] == spec.subject.scope.model_dump(mode="json")
            and r["record"]["state"] not in TERMINAL
        ]
        if len(active) >= self.pending_limit:
            sql.abort(ErrorCode.CAPACITY_EXCEEDED, "scope task queue is full")
        tenant_active = sum(
            r["record"]["subject"]["scope"]["tenant_id"] == spec.subject.scope.tenant_id
            and r["record"]["state"] not in TERMINAL
            for _, r in sql.rows("tasks")
        )
        if tenant_active >= self.pending_limit_per_tenant:
            sql.abort(ErrorCode.CAPACITY_EXCEEDED, "tenant task queue is full")
        task = TaskRecord(
            **spec.model_dump(),
            state=TaskState.PENDING,
            revision=1,
            attempt=0,
            effect_status=EffectStatus.NOT_STARTED,
        )
        sql.write(
            "tasks",
            task.task_id,
            {
                "record": task.model_dump(mode="json"),
                "context": Telemetry.producer_context(ctx).model_dump(mode="json"),
                "class": self.handlers[spec.kind][0],
                "created_at": self.clock(),
            },
        )
        sql.write("task_keys", key, {"task_id": task.task_id, "signature": signature})
        self.note(sql, task, ctx, "task.enqueued")
        sql.before_commit.append(lambda: self.identity.revalidate(tx, ctx))
        return task

    def sweep(self) -> int:
        count = 0
        with self.uow.transaction() as tx:
            for _, row in tx.active_task_rows():
                # Completed history is immutable for scheduling. Do not rebuild
                # every nested Pydantic model on each idle worker poll.
                record = row["record"]
                if (
                    record["state"] not in {TaskState.RUNNING, TaskState.RECOVERY_WAIT}
                    or not record.get("lease")
                    or record["lease"]["until"] > self.clock()
                ):
                    continue
                task = TaskRecord.model_validate(row["record"])
                if (
                    task.state in {TaskState.RUNNING, TaskState.RECOVERY_WAIT}
                    and task.lease
                    and task.lease.until <= self.clock()
                ):
                    updated = self.change(
                        tx,
                        row,
                        task,
                        state=TaskState.RECOVERY_WAIT,
                        lease=None,
                        effect_status=EffectStatus.UNKNOWN,
                        next_run_at=self.clock(),
                        error_code=ErrorCode.EXECUTION_INTERRUPTED,
                    )
                    self.note(
                        tx,
                        updated,
                        TrustedContext.model_validate(row["context"]),
                        "recovery.lease_expired"
                        if task.state == TaskState.RECOVERY_WAIT
                        else "task.lease_expired",
                    )
                    self.finish_attempt(
                        tx, task, EffectStatus.UNKNOWN, ErrorCode.EXECUTION_INTERRUPTED
                    )
                    count += 1
        return count

    def _claim(self, worker_id: str, execution_class: str, recovery: bool) -> TaskRecord | None:
        if execution_class not in self.class_limits:
            raise ValueError("unknown execution class")
        self.sweep()
        with self.uow.transaction() as tx:
            rows = sorted(
                (r for _, r in tx.active_task_rows()),
                key=lambda r: (r["created_at"], r["record"]["task_id"]),
            )
            leased = [
                r
                for r in rows
                if r["record"].get("lease")
                and r["record"]["lease"]["until"] > self.clock()
                and r["record"]["state"] in {TaskState.RUNNING, TaskState.RECOVERY_WAIT}
            ]
            if (
                sum(r["class"] == execution_class for r in leased)
                >= self.class_limits[execution_class]
            ):
                return None
            for row in rows:
                wanted = (
                    {TaskState.RECOVERY_WAIT}
                    if recovery
                    else {TaskState.PENDING, TaskState.RETRY_WAIT}
                )
                record = row["record"]
                if (
                    record["state"] not in wanted
                    or row["class"] != execution_class
                    or record["kind"] not in self.handlers
                ):
                    continue
                task = TaskRecord.model_validate(row["record"])
                if (
                    sum(
                        r["class"] == execution_class
                        and r["record"]["subject"]["scope"]["tenant_id"]
                        == task.subject.scope.tenant_id
                        for r in leased
                    )
                    >= self.per_tenant_running
                ):
                    continue
                if self.handlers[task.kind][0] != row["class"]:
                    continue
                if (task.lease and task.lease.until > self.clock()) or (
                    task.next_run_at and task.next_run_at > self.clock()
                ):
                    continue
                if (
                    sum(
                        r["class"] == execution_class
                        and r["record"]["subject"]["scope"]
                        == task.subject.scope.model_dump(mode="json")
                        for r in leased
                    )
                    >= self.per_scope_running
                ):
                    continue
                if not recovery and (
                    task.deadline_at <= self.clock() or task.attempt >= task.max_attempts
                ):
                    updated = self.change(
                        tx,
                        row,
                        task,
                        state=TaskState.FAILED,
                        lease=None,
                        error_code=ErrorCode.DEADLINE_EXCEEDED
                        if task.deadline_at <= self.clock()
                        else ErrorCode.CAPACITY_EXCEEDED,
                    )
                    self.note(
                        tx, updated, TrustedContext.model_validate(row["context"]), "task.exhausted"
                    )
                    self.close_recovery_operations(tx, updated, "failed")
                    continue
                if recovery and task.query_attempt >= self.query_max_attempts:
                    updated = self.change(tx, row, task, state=TaskState.ATTENTION, lease=None)
                    self.note(
                        tx,
                        updated,
                        TrustedContext.model_validate(row["context"]),
                        "recovery.exhausted",
                    )
                    self.close_recovery_operations(tx, updated, "unknown")
                    continue
                lease = Lease(
                    owner_id=worker_id,
                    token=secrets.token_hex(16),
                    until=later(self.clock(), self.lease_seconds),
                )
                if not recovery:
                    lease = lease.model_copy(update={"until": min(lease.until, task.deadline_at)})
                updated = self.change(
                    tx,
                    row,
                    task,
                    state=TaskState.RECOVERY_WAIT if recovery else TaskState.RUNNING,
                    lease=lease,
                    attempt=task.attempt + (not recovery),
                    query_attempt=task.query_attempt + recovery,
                    next_run_at=None,
                )
                self.progress.heartbeat(tx, worker_id, execution_class, task=updated)
                ctx = TrustedContext.model_validate(row["context"])
                self.note(tx, updated, ctx, "recovery.claimed" if recovery else "task.claimed")
                tx.write(
                    "attempts",
                    lease.token,
                    {
                        "task_id": task.task_id,
                        "attempt": updated.attempt,
                        "recovery": recovery,
                        "lease_token": lease.token,
                        "request_id": ctx.request_id,
                        "trace_id": ctx.trace_id,
                        "started_at": self.clock(),
                        "finished_at": None,
                        "effect_status": updated.effect_status,
                    },
                )
                return updated
        return None

    def claim(self, worker_id: str, execution_class: str, now: str) -> TaskRecord | None:
        # The public parameter is advisory; lease decisions use the server clock.
        return self._claim(worker_id, execution_class, False)

    def guard(self, tx: SQLiteTransaction, task: TaskRecord) -> tuple[dict[str, Any], TaskRecord]:
        row, current = self.load(tx, task.task_id)
        if (
            current.state not in {TaskState.RUNNING, TaskState.RECOVERY_WAIT}
            or not current.lease
            or not task.lease
            or current.lease.token != task.lease.token
            or current.lease.owner_id != task.lease.owner_id
            or current.lease.until <= self.clock()
        ):
            tx.abort(ErrorCode.VERSION_CONFLICT, "task lease is stale")
        until = current.lease.until

        def final_fence() -> None:
            if self.clock() >= until:
                tx.abort(ErrorCode.VERSION_CONFLICT, "lease expired before transaction commit")

        tx.before_commit.append(final_fence)
        return row, current

    def renew(self, tx: Transaction, task_id: str, lease: Lease, revision: int) -> TaskRecord:
        sql = native(tx)
        row, task = self.load(sql, task_id)
        if (
            task.revision != revision
            or task.lease is None
            or task.lease.token != lease.token
            or task.lease.owner_id != lease.owner_id
        ):
            sql.abort(ErrorCode.VERSION_CONFLICT, "lease/revision changed")
        self.guard(sql, task)
        until = later(self.clock(), self.lease_seconds)
        if task.state == TaskState.RUNNING:
            until = min(until, task.deadline_at)
        return self.change(
            sql, row, task, lease=Lease(owner_id=lease.owner_id, token=lease.token, until=until)
        )

    def finish_attempt(
        self,
        tx: SQLiteTransaction,
        task: TaskRecord,
        effect: EffectStatus,
        error: ErrorCode | None = None,
    ) -> None:
        if task.lease:
            row = tx.read("attempts", task.lease.token)
            if row:
                tx.write(
                    "attempts",
                    task.lease.token,
                    {
                        **row,
                        "finished_at": self.clock(),
                        "effect_status": effect,
                        "error_code": error,
                    },
                )

    def complete(
        self, tx: Transaction, ctx: TrustedContext, task: TaskRecord, result: RecordRef
    ) -> None:
        sql = native(tx)
        row, current = self.guard(sql, task)
        if (
            ctx.principal.principal_id != current.initiator_id
            or ctx.principal.auth_epoch != current.initiator_auth_epoch
        ):
            sql.abort(ErrorCode.FORBIDDEN, "completion principal mismatch")
        self.identity.authorize(
            tx, ctx, self.permissions.get(current.kind, Permission.WRITE), current.subject
        )
        if (
            result.scope != current.subject.scope
            or sql.get(result) is None
            or any(sql.get(ref) is None for ref in current.required_outputs)
        ):
            sql.abort(
                ErrorCode.CONTRACT_VIOLATION, "required durable outputs are missing or out of scope"
            )
        from aether_agent_memory.runtime.contracts.foundation import CheckpointRecord

        configuration = sql.read("runtime_configuration", "active")
        self.progress.checkpoint(
            sql,
            ctx,
            task,
            CheckpointRecord(
                task_id=current.task_id,
                expected_task_revision=current.revision,
                stage="completed",
                input_hash=current.input_hash,
                output_refs=tuple(dict.fromkeys((result, *current.required_outputs))),
                committed_at=self.clock(),
                config_version=configuration["version"] if configuration else "foundation_2",
            ),
        )
        updated = self.change(
            sql,
            {**row, "completion_token": current.lease.token if current.lease else None},
            current,
            state=TaskState.SUCCEEDED,
            result_ref=result,
            effect_status=EffectStatus.CONFIRMED,
            lease=None,
            error_code=None,
        )
        self.finish_attempt(sql, current, EffectStatus.CONFIRMED)
        self.note(sql, updated, ctx, "task.committed")
        self.close_recovery_operations(sql, updated, "completed")
        sql.before_commit.append(lambda: self.identity.revalidate(tx, ctx))

    def request_recovery(
        self, tx: Transaction, ctx: TrustedContext, request: RecoveryRequest
    ) -> OperationRecord:
        sql = native(tx)
        row, task = self.load(sql, request.task_id)
        self.identity.authorize(tx, ctx, Permission.RECOVER, task.subject)
        signature = fingerprint([ctx.principal.principal_id, request.model_dump(mode="json")])
        existing = sql.read("operations", request.operation_id)
        if existing:
            if existing["signature"] != signature:
                sql.abort(ErrorCode.IDEMPOTENCY_CONFLICT, "recovery operation ID already used")
            return OperationRecord.model_validate(existing["record"])
        if task.revision != request.expected_revision:
            sql.abort(ErrorCode.VERSION_CONFLICT, "task changed")
        if task.lease and task.lease.until > self.clock():
            sql.abort(ErrorCode.TASK_STILL_RUNNING, "cannot take over a live lease")
        if task.state not in {TaskState.RUNNING, TaskState.RECOVERY_WAIT}:
            sql.abort(
                ErrorCode.INVALID_ARGUMENT, "only interrupted or unresolved tasks can be recovered"
            )
        self.finish_attempt(sql, task, EffectStatus.UNKNOWN, ErrorCode.EXECUTION_INTERRUPTED)
        updated = self.change(
            sql,
            row,
            task,
            state=TaskState.RECOVERY_WAIT,
            lease=None,
            effect_status=EffectStatus.UNKNOWN,
            next_run_at=self.clock(),
        )
        operation = OperationRecord(
            operation_id=request.operation_id,
            subject=task.subject,
            phase="recovery",
            state="accepted",
            task_ids=(task.task_id,),
            reason=request.reason,
        )
        sql.write(
            "operations",
            operation.operation_id,
            {"record": operation.model_dump(mode="json"), "signature": signature},
        )
        audit = MaintenanceRecord(
            record_id=secrets.token_hex(16),
            actor_id=ctx.principal.principal_id,
            operation_id=operation.operation_id,
            subject=task.subject,
            reason=request.reason,
            occurred_at=self.clock(),
            phase="accepted",
            config_version="foundation_1",
        )
        sql.write("maintenance", audit.record_id, audit.model_dump(mode="json"))
        self.note(sql, updated, ctx, "recovery.requested")
        return operation

    def close_recovery_operations(
        self, tx: SQLiteTransaction, task: TaskRecord, state: str
    ) -> None:
        for key, row in tx.rows("operations"):
            operation = OperationRecord.model_validate(row["record"])
            if task.task_id in operation.task_ids and operation.state in {
                "accepted",
                "running",
                "unknown",
            }:
                updated = OperationRecord.model_validate({**operation.model_dump(), "state": state})
                tx.write("operations", key, {**row, "record": updated.model_dump(mode="json")})
                accepted = next(
                    (
                        v
                        for _, v in tx.rows("maintenance")
                        if v["operation_id"] == key and v["phase"] == "accepted"
                    ),
                    None,
                )
                if accepted:
                    audit = {
                        **accepted,
                        "record_id": secrets.token_hex(16),
                        "occurred_at": self.clock(),
                        "phase": state,
                    }
                    tx.write("maintenance", audit["record_id"], audit)

    def context_for(self, task: TaskRecord, recovery: bool) -> TrustedContext:
        with self.uow.transaction() as tx:
            row, _ = self.load(tx, task.task_id)
            ctx = TrustedContext.model_validate(row["context"])
            if recovery:
                ctx = ctx.model_copy(
                    update={"deadline_at": later(self.clock(), self.lease_seconds)}
                )
            self.identity.authorize(
                tx, ctx, self.permissions.get(task.kind, Permission.WRITE), task.subject
            )
            return ctx

    async def run_once(self, worker_id: str, execution_class: str) -> bool:
        """No automatic external retries. Handlers return typed effect evidence.

        Cooperative async handlers are renewed; complete belongs in the result transaction.
        Unexpected exceptions are uncertain, never retryable-no-effect.
        """
        task = self._claim(worker_id, execution_class, True)
        recovery = task is not None
        if task is None:
            task = self.claim(worker_id, execution_class, self.clock())
        if task is None:
            return False
        try:
            ctx = self.context_for(task, recovery)
            handler = self.handlers[task.kind][1]
            if recovery:
                decision = RecoveryDecision.model_validate_json(
                    (await self.invoke(handler.recover(ctx, task), ctx, task)).model_dump_json()
                )
                self.apply_recovery(task, ctx, decision)
            else:
                result = RunResult.model_validate_json(
                    (await self.invoke(handler.run(ctx, task), ctx, task)).model_dump_json()
                )
                self.apply_result(task, ctx, result)
        except Exception as exc:
            # Store a bounded code, not exception text that may contain secrets/body.
            self.record_failure(task, exc)
        finally:
            with self.uow.transaction() as tx:
                self.progress.heartbeat(tx, worker_id, execution_class)
        return True

    async def invoke(self, coroutine: Any, ctx: TrustedContext, task: TaskRecord) -> Any:
        async def heartbeat() -> None:
            while True:
                await asyncio.sleep(self.lease_seconds / 3)
                with self.uow.transaction() as tx:
                    _, current = self.load(tx, task.task_id)
                    if current.state == TaskState.SUCCEEDED:
                        return
                    if (
                        not current.lease
                        or not task.lease
                        or current.lease.token != task.lease.token
                    ):
                        tx.abort(ErrorCode.VERSION_CONFLICT, "lease lost while executing")
                    self.identity.revalidate(tx, ctx)
                    renewed = self.renew(tx, task.task_id, current.lease, current.revision)
                    self.progress.heartbeat(
                        tx, current.lease.owner_id, self.handlers[current.kind][0], task=renewed
                    )
                    tx.write(
                        "workers",
                        current.lease.owner_id,
                        {
                            **(tx.read("workers", current.lease.owner_id) or {}),
                            "worker_id": current.lease.owner_id,
                            "last_seen": self.clock(),
                            "state": "executing",
                        },
                    )

        work = asyncio.create_task(coroutine)
        beat = asyncio.create_task(heartbeat())
        timeout = max(
            0.001,
            (
                datetime.fromisoformat(ctx.deadline_at.replace("Z", "+00:00"))
                - datetime.fromisoformat(self.clock().replace("Z", "+00:00"))
            ).total_seconds(),
        )
        try:
            done, _ = await asyncio.wait(
                {work, beat}, timeout=timeout, return_when=asyncio.FIRST_COMPLETED
            )
            if work in done:
                return await work
            if beat in done:
                await beat
                return await asyncio.wait_for(work, timeout=timeout)
            raise FoundationError(ErrorCode.DEADLINE_EXCEEDED, "handler exceeded deadline")
        finally:
            for pending in (work, beat):
                pending.cancel()
            await asyncio.gather(work, beat, return_exceptions=True)

    def record_failure(self, task: TaskRecord, error: Exception) -> None:
        with self.uow.transaction() as tx:
            row, current = self.load(tx, task.task_id)
            if (
                current.state in TERMINAL
                or not current.lease
                or not task.lease
                or current.lease.token != task.lease.token
                or current.lease.until <= self.clock()
            ):
                return
            code = (
                error.code
                if isinstance(error, FoundationError)
                else ErrorCode.EXECUTION_INTERRUPTED
            )
            state = TaskState.ATTENTION if code == ErrorCode.FORBIDDEN else TaskState.RECOVERY_WAIT
            updated = self.change(
                tx,
                row,
                current,
                state=state,
                lease=None,
                effect_status=EffectStatus.UNKNOWN,
                error_code=code,
                next_run_at=later(self.clock(), self.retry_seconds),
            )
            self.finish_attempt(tx, current, EffectStatus.UNKNOWN, code)
            self.note(
                tx, updated, TrustedContext.model_validate(row["context"]), "task.unresolved", code
            )
            if state == TaskState.ATTENTION:
                self.close_recovery_operations(tx, updated, "unknown")

    def apply_result(self, task: TaskRecord, ctx: TrustedContext, result: RunResult) -> None:
        with self.uow.transaction() as tx:
            row, current = self.load(tx, task.task_id)
            if result.outcome == "committed":
                if (
                    current.state != TaskState.SUCCEEDED
                    or current.result_ref != result.result_ref
                    or not task.lease
                    or row.get("completion_token") != task.lease.token
                ):
                    tx.abort(
                        ErrorCode.CONTRACT_VIOLATION,
                        "handler reported success without atomic complete",
                    )
                return
            row, current = self.guard(tx, task)
            mapping = {
                "retryable_no_effect": TaskState.RETRY_WAIT,
                "uncertain": TaskState.RECOVERY_WAIT,
                "obsolete": TaskState.CANCELLED,
                "failed": TaskState.FAILED,
            }
            state = mapping[result.outcome]
            effect = EffectStatus.UNKNOWN if result.outcome == "uncertain" else result.effect_status
            if result.operation_id:
                original = row.get("original_operation_id")
                if original and original != result.operation_id:
                    tx.abort(ErrorCode.CONTRACT_VIOLATION, "external operation ID cannot change")
                row = {**row, "original_operation_id": result.operation_id}
            if state == TaskState.RETRY_WAIT and current.attempt >= current.max_attempts:
                state = TaskState.FAILED
            wait = tx.read("task_waits", task.task_id)
            wait_lease = tx.read("task_wait_leases", task.task_id)
            if not wait_lease or not task.lease or wait_lease["token"] != task.lease.token:
                wait = None
            next_run = later(self.clock(), self.retry_seconds)
            if wait and state in {TaskState.RETRY_WAIT, TaskState.RECOVERY_WAIT}:
                if wait["effect_status"] != effect.value:
                    tx.abort(ErrorCode.CONTRACT_VIOLATION, "wait and handler effect disagree")
                next_run = min(wait["next_check_at"], current.deadline_at)
                if wait["resume_mode"] == "attention":
                    state = TaskState.ATTENTION
                elif wait["resume_mode"] == "query_only":
                    state = TaskState.RECOVERY_WAIT
            updated = self.change(
                tx,
                row,
                current,
                state=state,
                effect_status=effect,
                lease=None,
                next_run_at=next_run,
            )
            self.finish_attempt(tx, current, effect)
            self.note(tx, updated, ctx, "task." + result.outcome)
            if state in TERMINAL:
                self.close_recovery_operations(tx, updated, "failed")

    def apply_recovery(
        self, task: TaskRecord, ctx: TrustedContext, decision: RecoveryDecision
    ) -> None:
        with self.uow.transaction() as tx:
            row, current = self.load(tx, task.task_id)
            if current.state == TaskState.SUCCEEDED:
                if not task.lease or row.get("completion_token") != task.lease.token:
                    tx.abort(ErrorCode.VERSION_CONFLICT, "another attempt completed this task")
                tx.write(
                    "recovery",
                    secrets.token_hex(16),
                    {
                        "task_id": task.task_id,
                        "occurred_at": self.clock(),
                        "decision": decision.model_dump(mode="json"),
                    },
                )
                self.note(tx, current, ctx, "recovery.confirmed")
                return  # Handler already committed a verified original result.
            row, current = self.guard(tx, task)
            if any(ref.scope != task.subject.scope for ref in decision.evidence):
                tx.abort(ErrorCode.CONTRACT_VIOLATION, "recovery evidence outside task scope")
            if (
                decision.action == RecoveryAction.QUERY_ONLY
                and row.get("original_operation_id")
                and decision.original_operation_id != row["original_operation_id"]
            ):
                tx.abort(
                    ErrorCode.CONTRACT_VIOLATION, "recovery must query the original operation ID"
                )
            state = {
                RecoveryAction.RESUME: TaskState.RETRY_WAIT,
                RecoveryAction.QUERY_ONLY: TaskState.RECOVERY_WAIT,
                RecoveryAction.CANCEL: TaskState.CANCELLED,
                RecoveryAction.FAIL: TaskState.FAILED,
                RecoveryAction.ATTENTION: TaskState.ATTENTION,
            }[decision.action]
            if decision.action == RecoveryAction.RESUME and decision.effect_status not in {
                EffectStatus.NO_EFFECT,
                EffectStatus.NOT_STARTED,
            }:
                tx.abort(ErrorCode.CONTRACT_VIOLATION, "resume requires evidence of no effect")
            if state == TaskState.RETRY_WAIT and (
                current.attempt >= current.max_attempts or current.deadline_at <= self.clock()
            ):
                state = TaskState.FAILED
            updated = self.change(
                tx,
                row,
                current,
                state=state,
                effect_status=decision.effect_status,
                lease=None,
                next_run_at=later(self.clock(), self.retry_seconds),
            )
            tx.write(
                "recovery",
                secrets.token_hex(16),
                {
                    "task_id": task.task_id,
                    "occurred_at": self.clock(),
                    "decision": decision.model_dump(mode="json"),
                },
            )
            self.finish_attempt(tx, current, decision.effect_status)
            self.note(tx, updated, ctx, "recovery." + decision.action)
            if state in TERMINAL:
                self.close_recovery_operations(
                    tx, updated, "unknown" if state == TaskState.ATTENTION else "failed"
                )
