"""Durable task admission, business evidence and fences; Temporal schedules execution."""

from __future__ import annotations

import secrets
from collections.abc import Callable
from typing import Any

from aether_agent_memory.runtime.contracts.models import (
    DiagnosticRecord,
    EffectStatus,
    ErrorCode,
    Flow,
    Lease,
    OperationRecord,
    Permission,
    RecordRef,
    RecoveryRequest,
    TaskRecord,
    TaskSpec,
    TaskState,
    TrustedContext,
)
from aether_agent_memory.runtime.contracts.ports import TaskHandler, Transaction
from aether_agent_memory.runtime.contracts.rules import TASK_TRANSITIONS
from aether_agent_memory.runtime.foundation.transactions import native
from aether_agent_memory.runtime.storage.ports import MetadataTransaction, MetadataUnitOfWork

from .common import FoundationError, fingerprint, later, now
from .identity import Identity
from .telemetry import Telemetry, observed

TERMINAL = {TaskState.SUCCEEDED, TaskState.FAILED, TaskState.CANCELLED, TaskState.ATTENTION}


@observed("runtime.tasks")
class Tasks:
    def __init__(
        self,
        uow: MetadataUnitOfWork,
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
        maintenance_principals: tuple[str, ...] = (),
    ) -> None:
        self.uow, self.identity, self.clock = uow, identity, clock
        self.maintenance_principals = maintenance_principals
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
        self.attempt_limits: dict[str, int] = {}
        self.on_recovery: (
            Callable[[MetadataTransaction, TrustedContext, RecoveryRequest], OperationRecord] | None
        ) = None
        self.on_admitted: Callable[[MetadataTransaction, TaskRecord], None] | None = None
        self.on_terminal: Callable[[MetadataTransaction, TaskRecord], None] | None = None
        from .task_progress import TaskProgress

        self.progress = TaskProgress(self, secrets.token_hex(8))

    def register(
        self,
        kind: str,
        execution_class: str,
        handler: TaskHandler,
        *,
        permission: Permission = Permission.WRITE,
        attempt_limit: int | None = None,
    ) -> None:
        limit = self.max_attempts if attempt_limit is None else attempt_limit
        if limit <= 0:
            raise ValueError("task attempt limit must be positive")
        if kind in self.handlers or execution_class not in self.class_limits:
            raise ValueError("duplicate task kind or unknown execution class")
        self.handlers[kind] = (execution_class, handler)
        self.permissions[kind] = permission
        # Only trusted handler registration can change a kind's admission budget.
        self.attempt_limits[kind] = limit

    @staticmethod
    def load(tx: MetadataTransaction, task_id: str) -> tuple[dict[str, Any], TaskRecord]:
        row = tx.read("tasks", task_id)
        if row is None:
            raise FoundationError(ErrorCode.NOT_FOUND, "task not found")
        return row, TaskRecord.model_validate(row["record"])

    def note(
        self,
        tx: MetadataTransaction,
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
        self, tx: MetadataTransaction, row: dict[str, Any], task: TaskRecord, **changes: Any
    ) -> TaskRecord:
        state = changes.get("state", task.state)
        if state != task.state and state not in TASK_TRANSITIONS[task.state]:
            tx.abort(ErrorCode.CONTRACT_VIOLATION, "illegal task state transition")
        updated = TaskRecord.model_validate(
            {**task.model_dump(), **changes, "revision": task.revision + 1}
        )
        tx.write("tasks", task.task_id, {**row, "record": updated.model_dump(mode="json")})
        self.project_terminal(tx, updated)
        return updated

    def project_terminal(self, tx: MetadataTransaction, task: TaskRecord) -> None:
        if task.state not in TERMINAL:
            return
        self.close_recovery_operations(
            tx,
            task,
            "completed"
            if task.state == TaskState.SUCCEEDED
            else "unknown"
            if task.effect_status == EffectStatus.UNKNOWN
            else "failed",
        )
        if self.on_terminal is not None:
            self.on_terminal(tx, task)

    def enqueue(self, tx: Transaction, ctx: TrustedContext, spec: TaskSpec) -> TaskRecord:
        sql = native(tx)
        backend = sql.read("meta", "execution_backend")
        if backend and backend.get("backend") == "temporal" and self.on_admitted is None:
            sql.abort(ErrorCode.CONTRACT_VIOLATION, "Temporal admission binding is required")
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
        if (
            spec.deadline_at > ctx.deadline_at
            or spec.deadline_at <= self.clock()
            or spec.max_attempts > self.attempt_limits[spec.kind]
        ):
            sql.abort(ErrorCode.INVALID_ARGUMENT, "invalid task deadline or attempt budget")
        content = sql.get(spec.input_ref)
        if content is None or fingerprint(content) != spec.input_hash:
            sql.abort(ErrorCode.CONTRACT_VIOLATION, "input must exist and match its fingerprint")
        # PostgreSQL's existing state projection excludes terminal history before
        # transferring/decoding rows. Reuse this one snapshot for both quotas;
        # keep exact Python scope equality and the scope-before-tenant error order.
        quota_rows = (
            sql.active_task_rows()
            if getattr(self.uow, "backend", "sqlite") == "postgresql"
            else None
        )
        active = [
            r
            for _, r in (quota_rows if quota_rows is not None else sql.rows("tasks"))
            if r["record"]["subject"]["scope"] == spec.subject.scope.model_dump(mode="json")
            and r["record"]["state"] not in TERMINAL
        ]
        if len(active) >= self.pending_limit:
            sql.abort(ErrorCode.CAPACITY_EXCEEDED, "scope task queue is full")
        tenant_active = sum(
            r["record"]["subject"]["scope"]["tenant_id"] == spec.subject.scope.tenant_id
            and r["record"]["state"] not in TERMINAL
            for _, r in (quota_rows if quota_rows is not None else sql.rows("tasks"))
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
        if self.on_admitted is not None:
            self.on_admitted(sql, task)
        self.note(sql, task, ctx, "task.enqueued")
        sql.before_commit.append(lambda: self.identity.revalidate(tx, ctx))
        return task

    def sweep(self) -> int:
        raise RuntimeError("RF scheduling is retired; use the configured Temporal service")

    def claim(self, worker_id: str, execution_class: str, now: str) -> TaskRecord | None:
        # The public parameter is advisory; lease decisions use the server clock.
        raise RuntimeError("RF scheduling is retired; use the configured Temporal service")

    def guard(self, tx: MetadataTransaction, task: TaskRecord) -> tuple[dict[str, Any], TaskRecord]:
        row, current = self.load(tx, task.task_id)
        if current.execution is not None or task.execution is not None:
            if (
                current.state not in {TaskState.RUNNING, TaskState.RECOVERY_WAIT}
                or task.execution is None
                or current.execution != task.execution
            ):
                tx.abort(ErrorCode.VERSION_CONFLICT, "Temporal execution is stale")
            ctx = TrustedContext.model_validate(row["context"])
            ctx = ctx.model_copy(
                update={"deadline_at": row.get("execution_deadline", task.deadline_at)}
            )
            self.identity.authorize(
                tx, ctx, self.permissions.get(current.kind, Permission.WRITE), current.subject
            )
            self.identity.authorize(tx, ctx, Permission.READ, current.input_ref)
            if fingerprint(tx.get(current.input_ref)) != current.input_hash:
                tx.abort(ErrorCode.VERSION_CONFLICT, "task input changed")
            tx.before_commit.append(lambda: self.identity.revalidate(tx, ctx))
            return row, current
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
        tx: MetadataTransaction,
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
        sql.before_commit.append(lambda: self.identity.revalidate(tx, ctx))

    def request_recovery(
        self, tx: Transaction, ctx: TrustedContext, request: RecoveryRequest
    ) -> OperationRecord:
        sql = native(tx)
        self.identity.revalidate(tx, ctx)
        if self.on_recovery is None:
            _, task = self.load(sql, request.task_id)
            self.identity.authorize(tx, ctx, Permission.RECOVER, task.subject)
            sql.abort(ErrorCode.DEPENDENCY_UNAVAILABLE, "Temporal control admission is required")
        # The configured Temporal admission authorizes the loaded task, including
        # narrow deployment maintenance authority, and rechecks at delivery.
        return self.on_recovery(sql, ctx, request)

    def close_recovery_operations(
        self, tx: MetadataTransaction, task: TaskRecord, state: str
    ) -> None:
        for key, row in tx.rows("operations"):
            operation = OperationRecord.model_validate(row["record"])
            if (
                task.task_id in operation.task_ids
                and operation.state
                in {
                    "accepted",
                    "running",
                    "unknown",
                }
                and operation.state != state
            ):
                task_row, _ = self.load(tx, task.task_id)
                reason = (
                    operation.reason
                    if state == "completed"
                    else (
                        task_row.get("terminal_reason")
                        or (task.error_code.value if task.error_code else task.state.value)
                    )
                )
                updated = OperationRecord.model_validate(
                    {**operation.model_dump(), "state": state, "reason": reason}
                )
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

    async def run_once(self, worker_id: str, execution_class: str) -> bool:
        raise RuntimeError("RF scheduling is retired; use the configured Temporal service")

    async def invoke(self, coroutine: Any, ctx: TrustedContext, task: TaskRecord) -> Any:
        raise RuntimeError("RF scheduling is retired; use the configured Temporal service")
