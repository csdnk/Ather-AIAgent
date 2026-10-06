"""Authorized state/trace queries. Business capabilities remain unknown until wired."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Any

from aether_agent_memory.runtime.contracts.models import (
    CapabilityHealth,
    DiagnosticRecord,
    ErrorCode,
    HealthReport,
    MaintenanceRecord,
    OperationRecord,
    PageRequest,
    Permission,
    RecordRef,
    RelatedRecords,
    TaskRecord,
    TaskState,
    TrustedContext,
)
from aether_agent_memory.runtime.contracts.ports import Transaction
from aether_agent_memory.runtime.foundation.transactions import native
from aether_agent_memory.runtime.storage.ports import MetadataUnitOfWork

from .common import FoundationError, now
from .identity import Identity
from .monitoring import Monitoring
from .tasks import Tasks


class Diagnostics:
    def __init__(
        self,
        uow: MetadataUnitOfWork,
        identity: Identity,
        clock: Callable[[], str] = now,
        *,
        maintenance_principals: tuple[str, ...] = (),
    ) -> None:
        self.uow, self.identity, self.clock = uow, identity, clock
        self.maintenance_principals = maintenance_principals
        self.monitoring: Monitoring | None = None

    def authorize(self, ctx: TrustedContext) -> None:
        with self.uow.transaction() as tx:
            self.identity.revalidate(tx, ctx)
            if Permission.DIAGNOSE not in ctx.principal.permissions:
                tx.abort(ErrorCode.FORBIDDEN, "diagnose permission required")

    def trace(
        self, ctx: TrustedContext, trace_id: str, *, after: int = 0, limit: int = 100
    ) -> dict[str, Any]:
        self.authorize(ctx)
        if self.uow.telemetry is None:
            raise FoundationError(ErrorCode.DEPENDENCY_UNAVAILABLE, "log store not connected")
        result = self.uow.telemetry.page(ctx, trace_id, after=after, limit=limit)
        with self.uow.transaction() as tx:
            self.identity.revalidate(tx, ctx)
            tasks = []
            for _, row in tx.rows("tasks"):
                if row["context"]["trace_id"] != trace_id:
                    continue
                if row["context"]["principal"]["principal_id"] != ctx.principal.principal_id:
                    continue
                task = TaskRecord.model_validate(row["record"])
                if self.identity.permits(tx, ctx, Permission.DIAGNOSE, task.subject):
                    tasks.append(
                        {
                            "task_id": task.task_id,
                            "kind": task.kind,
                            "state": task.state,
                            "effect_status": task.effect_status,
                            "error_code": task.error_code,
                            "attempt": task.attempt,
                            "query_attempt": task.query_attempt,
                            "subject": task.subject.model_dump(mode="json"),
                        }
                    )
            event_ids = {
                key
                for key, row in tx.rows("outbox")
                if row["event"]["trace_id"] == trace_id
                and row["event"]["initiator_id"] == ctx.principal.principal_id
                and self.identity.permits(
                    tx, ctx, Permission.DIAGNOSE, RecordRef.model_validate(row["event"]["subject"])
                )
            }
            deliveries = [
                {k: row[k] for k in ("event_id", "consumer_id", "state", "attempt", "error_code")}
                for _, row in tx.rows("deliveries")
                if row["event_id"] in event_ids
            ]
            result["durable_facts"] = {
                "tasks": tasks[:100],
                "deliveries": deliveries[:100],
                "truncated": len(tasks) > 100 or len(deliveries) > 100,
                "meaning": "current committed state; independent of log retention",
            }
            stages = []
            for _, stage in tx.rows("recall_stages"):
                if stage["trace_id"] != trace_id:
                    continue
                request = tx.read("recall_requests", stage["recall_id"])
                if (
                    request
                    and request["context"]["principal"]["principal_id"]
                    == ctx.principal.principal_id
                    and self.identity.permits(
                        tx, ctx, Permission.DIAGNOSE, RecordRef.model_validate(stage["subject"])
                    )
                ):
                    stages.append(stage)
            result["durable_facts"]["recall_stages"] = sorted(
                stages, key=lambda row: row["recorded_at"]
            )[:100]
            result["durable_facts"]["truncated"] |= len(stages) > 100
        # Query is deliberately initiator/scope restricted, even when an object
        # grant exists: an object grant must not disclose an entire user's trace.
        self.authorize(ctx)
        return result

    def record(self, tx: Transaction, record: DiagnosticRecord) -> None:
        sql = native(tx)
        existing = sql.read("diagnostics", record.record_id)
        data = DiagnosticRecord.model_validate_json(record.model_dump_json()).model_dump(
            mode="json"
        )
        if existing and existing != data:
            sql.abort(ErrorCode.IDEMPOTENCY_CONFLICT, "diagnostic ID already used")
        sql.write("diagnostics", record.record_id, data)

    def record_maintenance(self, tx: Transaction, record: MaintenanceRecord) -> None:
        sql = native(tx)
        data = MaintenanceRecord.model_validate_json(record.model_dump_json()).model_dump(
            mode="json"
        )
        existing = sql.read("maintenance", record.record_id)
        if existing and existing != data:
            sql.abort(ErrorCode.IDEMPOTENCY_CONFLICT, "maintenance ID already used")
        sql.write("maintenance", record.record_id, data)

    def tasks_page(
        self, ctx: TrustedContext, page: PageRequest, state: TaskState | None = None
    ) -> dict[str, Any]:
        self.authorize(ctx)
        with self.uow.transaction() as tx:
            self.identity.revalidate(tx, ctx)
            items = []
            for _, row in tx.rows("tasks"):
                task = TaskRecord.model_validate(row["record"])
                if state is not None and task.state != state:
                    continue
                if not self.identity.permits_task_maintenance(
                    tx, ctx, Permission.DIAGNOSE, task, self.maintenance_principals
                ):
                    continue
                created = row["created_at"]
                # Stable reverse chronological key for signed, identity-bound cursors.
                order = 10**20 - int(datetime.fromisoformat(created).timestamp() * 1_000_000)
                value = {
                    key: row["record"][key]
                    for key in (
                        "task_id",
                        "revision",
                        "kind",
                        "owner_flow",
                        "state",
                        "attempt",
                        "max_attempts",
                        "effect_status",
                        "error_code",
                        "subject",
                        "next_run_at",
                        "deadline_at",
                    )
                }
                value["created_at"] = created
                # Object grants do not grant access to another initiator's full trace.
                value["trace_id"] = (
                    row["context"]["trace_id"]
                    if row["context"]["principal"]["principal_id"] == ctx.principal.principal_id
                    and row["context"]["principal"]["home_scope"]
                    == ctx.principal.home_scope.model_dump(mode="json")
                    else None
                )
                items.append((f"{order:021d}:{task.task_id}", value))
            rows, cursor = tx.page(
                items,
                ["diagnostic_tasks", ctx.principal.model_dump(mode="json"), state],
                page,
            )
        self.authorize(ctx)
        return {"items": rows, "next_cursor": cursor}

    def task(self, ctx: TrustedContext, task_id: str) -> TaskRecord:
        with self.uow.transaction() as tx:
            _, task = Tasks.load(tx, task_id)
            if not self.identity.permits_task_maintenance(
                tx, ctx, Permission.DIAGNOSE, task, self.maintenance_principals
            ):
                tx.abort(ErrorCode.FORBIDDEN, "task metadata access denied")
            return task

    def operation(self, ctx: TrustedContext, operation_id: str) -> OperationRecord:
        with self.uow.transaction() as tx:
            row = tx.read("operations", operation_id)
            if row is None:
                raise FoundationError(ErrorCode.NOT_FOUND, "operation not found")
            operation = OperationRecord.model_validate(row["record"])
            self.identity.authorize(tx, ctx, Permission.DIAGNOSE, operation.subject)
            return operation

    def related(self, ctx: TrustedContext, subject: RecordRef, page: PageRequest) -> RelatedRecords:
        with self.uow.transaction() as tx:
            self.identity.authorize(tx, ctx, Permission.DIAGNOSE, subject)
            items = []
            for key, value in tx.rows("diagnostics"):
                record = DiagnosticRecord.model_validate(value)
                if record.subject == subject or subject in record.related:
                    # Related-ID knowledge cannot bypass authorization on the actual fact.
                    self.identity.authorize(tx, ctx, Permission.DIAGNOSE, record.subject)
                    items.append((key, value))
            rows, cursor = tx.page(
                items,
                [subject.model_dump(mode="json"), ctx.principal.model_dump(mode="json")],
                page,
            )
            return RelatedRecords(
                subject=subject,
                records=tuple(DiagnosticRecord.model_validate(r) for r in rows),
                next_cursor=cursor,
            )

    def health(self, ctx: TrustedContext) -> HealthReport:
        monitoring = self.monitoring
        if monitoring is not None:
            return monitoring.legacy_health(ctx)
        with self.uow.transaction() as tx:
            self.identity.revalidate(tx, ctx)
            if Permission.DIAGNOSE not in ctx.principal.permissions:
                tx.abort(ErrorCode.FORBIDDEN, "diagnose permission required")
        return HealthReport(
            capabilities=tuple(
                CapabilityHealth(
                    capability=capability,
                    state="unknown",
                    checked_at=self.clock(),
                    reason="business adapter not connected",
                )
                for capability in ("save", "working_read", "long_term", "context", "scheduling")
            ),
            config_version="foundation_1",
        )

    def task_evidence(self, ctx: TrustedContext, task_id: str) -> dict[str, Any]:
        """Bounded-to-one-task local maintenance query, without input/result bodies."""
        with self.uow.transaction() as tx:
            _, task = Tasks.load(tx, task_id)
            self.identity.authorize(tx, ctx, Permission.DIAGNOSE, task.subject)
            outbox = {
                k: r
                for k, r in tx.rows("outbox")
                if r["event"]["subject"] == task.subject.model_dump(mode="json")
            }
            return {
                "task": task.model_dump(mode="json"),
                "attempts": [r for _, r in tx.rows("attempts") if r["task_id"] == task_id],
                "recovery": [r for _, r in tx.rows("recovery") if r["task_id"] == task_id],
                "stages": [
                    r
                    for _, r in tx.rows("diagnostics")
                    if any(
                        v["object_id"] == task_id and v["object_type"] == "task"
                        for v in r["related"]
                    )
                ],
                "deliveries": [r for _, r in tx.rows("deliveries") if r["event_id"] in outbox],
                "maintenance": [
                    r
                    for _, r in tx.rows("maintenance")
                    if r["subject"] == task.subject.model_dump(mode="json")
                ],
            }
