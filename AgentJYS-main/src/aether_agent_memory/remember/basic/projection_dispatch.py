"""Durable projection admission, independent of atomic long-term result commits.

Intents are not executable tasks. Existing Tasks admission remains the only place
that creates a task and its Temporal/Outbox delivery. Each admission transaction
either records both task and receipt or rolls back; capacity never rolls back an
already committed long-term result.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from aether_agent_memory.remember.contracts.foundation import MemoryRecord
from aether_agent_memory.remember.contracts.models import MemoryRef, MemorySnapshot
from aether_agent_memory.runtime.contracts.models import (
    ErrorCode,
    EventEnvelope,
    RecordRef,
    TrustedContext,
)
from aether_agent_memory.runtime.foundation.common import FoundationError, fingerprint, later
from aether_agent_memory.runtime.foundation.tasks import TERMINAL
from aether_agent_memory.runtime.storage.ports import MetadataTransaction

from .policy import RememberPolicy

if TYPE_CHECKING:
    from .pipeline import RememberPipeline

TABLE = "remember_projection_dispatch"
EVENTS = "remember_event_dispatch"


def stage_dispatch_anchor(
    tx: MetadataTransaction, ctx: TrustedContext, task_id: str, ref: MemoryRef
) -> None:
    """Reuse the existing public Temporal remember_pending route, not a new plan."""
    key = "dispatch:" + task_id
    if tx.read("remember_pending", key) is None:
        tx.write(
            "remember_pending",
            key,
            {
                "state": "dispatching",
                "dispatch_task_id": task_id,
                "ref": ref.model_dump(mode="json"),
                "context": ctx.model_dump(mode="json"),
            },
        )


def stage_event(
    tx: MetadataTransaction, ctx: TrustedContext, event: EventEnvelope, task_id: str
) -> None:
    if tx.read(EVENTS, event.event_id) is None:
        tx.write(
            EVENTS,
            event.event_id,
            {
                "event": event.model_dump(mode="json"),
                "context": ctx.model_dump(mode="json"),
                "source_task_id": task_id,
                "state": "pending",
                "reason": None,
            },
        )


def _room(
    owner: RememberPipeline, tx: MetadataTransaction, scope: dict[str, Any], needed: int = 1
) -> bool:
    active = [
        row["record"] for _, row in tx.active_task_rows() if row["record"]["state"] not in TERMINAL
    ]
    return bool(
        sum(row["subject"]["scope"] == scope for row in active) + needed
        <= owner.tasks.pending_limit
        and sum(row["subject"]["scope"]["tenant_id"] == scope["tenant_id"] for row in active)
        + needed
        <= owner.tasks.pending_limit_per_tenant
    )


def _event_keys(tx: MetadataTransaction, task_id: str | None) -> list[str]:
    return [
        key
        for key, row in sorted(
            tx.rows(EVENTS),
            key=lambda pair: (
                pair[1]["event"]["occurred_at"],
                pair[1]["event"]["payload"]["change_seq"],
                pair[0],
            ),
        )
        if row["state"] == "pending" and (task_id is None or row["source_task_id"] == task_id)
    ]


def _admit_event(owner: RememberPipeline, tx: MetadataTransaction, key: str) -> int:
    row = tx.read(EVENTS, key)
    if not row or row["state"] != "pending":
        return 0
    event = EventEnvelope.model_validate(row["event"])
    ctx = TrustedContext.model_validate(row["context"]).model_copy(
        update={
            "deadline_at": later(owner.identity.clock(), owner.processing_seconds),
        }
    )
    consumers = sum(kind == event.event_type for kind, _ in owner.events.consumers)
    if not _room(owner, tx, ctx.principal.home_scope.model_dump(mode="json"), consumers):
        return 0
    # Earlier events of this object keep their original sequence when different
    # result jobs commit before the queue has room to publish their signals.
    if any(
        other["state"] == "pending"
        and other["event"]["subject"]["object_id"] == event.subject.object_id
        and other["event"]["payload"]["change_seq"] < event.payload["change_seq"]
        for _, other in tx.rows(EVENTS)
    ):
        return 0
    owner.events.append(tx, ctx, event)
    tx.write(EVENTS, key, {**row, "state": "admitted", "reason": None})
    return 1


def dispatch_events(owner: RememberPipeline, task_id: str | None = None) -> int:
    with owner.uow.transaction() as tx:
        keys = _event_keys(tx, task_id)
    admitted = 0
    for key in keys[: min(owner.tasks.pending_limit, owner.tasks.pending_limit_per_tenant)]:
        try:
            with owner.uow.transaction() as tx:
                added = _admit_event(owner, tx, key)
            admitted += added
        except FoundationError as error:
            # Admission may abort: diagnose only after its transaction closed.
            with owner.uow.transaction() as tx:
                row = tx.read(EVENTS, key)
                if row and row["state"] == "pending":
                    tx.write(
                        EVENTS,
                        key,
                        {
                            **row,
                            "reason": error.code.value,
                            "state": "blocked"
                            if error.code
                            in {
                                ErrorCode.FORBIDDEN,
                                ErrorCode.CONTRACT_VIOLATION,
                                ErrorCode.IDEMPOTENCY_CONFLICT,
                            }
                            else "pending",
                        },
                    )
    return admitted


def dispatch_result_in(
    owner: RememberPipeline, tx: MetadataTransaction, key: str, anchor: dict[str, Any]
) -> int:
    """Public periodic callback: supplied transaction, no nested transactions.

    Quota preflight avoids expected capacity aborts. Unexpected aborts propagate
    and roll back the entire dispatch/cursor transaction, never semantic results.
    """
    if anchor["state"] != "dispatching":
        return 0
    task_id = anchor["dispatch_task_id"]
    limit = min(owner.tasks.pending_limit, owner.tasks.pending_limit_per_tenant)
    count = 0
    for event_key in _event_keys(tx, task_id)[:limit]:
        count += _admit_event(owner, tx, event_key)
    for intent_key, row in tx.rows(TABLE):
        if count >= limit:
            break
        if (
            row["source_task_id"] == task_id
            and row["state"] == "pending"
            and _room(owner, tx, row["memory"]["scope"])
        ):
            count += _admit(owner, tx, intent_key)
    if not any(
        row["source_task_id"] == task_id and row["state"] == "pending"
        for table in (EVENTS, TABLE)
        for _, row in tx.rows(table)
    ):
        tx.write("remember_pending", key, {**anchor, "state": "processed"})
    return count


def stage_projection(
    owner: RememberPipeline,
    tx: MetadataTransaction,
    ctx: TrustedContext,
    memory: MemorySnapshot,
    source_task_id: str,
) -> str:
    """Called only inside the transaction that commits the corresponding version."""
    key = fingerprint(["long_term_projection", memory.ref.model_dump(mode="json")])
    if tx.read(TABLE, key) is None:
        tx.write(
            TABLE,
            key,
            {
                "memory": memory.ref.model_dump(mode="json"),
                "source_task_id": source_task_id,
                "context": ctx.model_dump(mode="json"),
                "policy": owner.policy.model_dump(mode="json"),
                "binding": owner.checkpoint_binding("remember.project"),
                "state": "pending",
                "created_at": owner.identity.clock(),
                "task_id": None,
                "reason": None,
            },
        )
    return key


def dispatch_status(tx: MetadataTransaction, memory_ids: set[str]) -> dict[str, Any]:
    rows = [
        _effective_row(tx, row)
        for _, row in tx.rows(TABLE)
        if row["memory"]["memory_id"] in memory_ids
    ]
    events = [
        row for _, row in tx.rows(EVENTS) if row["event"]["subject"]["object_id"] in memory_ids
    ]
    return {
        "pending": sum(row["state"] == "pending" for row in (*rows, *events)),
        "blocked": sum(row["state"] == "blocked" for row in (*rows, *events)),
        "pending_events": sum(row["state"] == "pending" for row in events),
        "items": [
            {key: row.get(key) for key in ("memory", "state", "task_id", "reason")} for row in rows
        ],
    }


def _effective_row(tx: MetadataTransaction, row: dict[str, Any]) -> dict[str, Any]:
    """A repaired or obsolete historical intent cannot keep status failed forever."""
    if row["state"] not in {"pending", "blocked"}:
        return row
    ref = row["memory"]
    pointer = tx.read("remember_current", ref["memory_id"])
    raw = tx.get(RecordRef.model_validate(pointer)) if pointer else None
    if raw is None or raw["ref"] != ref or raw["status"] != "active":
        return {**row, "state": "obsolete", "reason": "MEMORY_GONE"}
    if raw["projection_state"] == "ready":
        return {**row, "state": "ready", "reason": None}
    latest = tx.read("remember_latest_task", fingerprint([ref["memory_id"], "remember.project"]))
    envelope = tx.read("tasks", latest) if latest else None
    frozen = tx.get(RecordRef.model_validate(envelope["record"]["input_ref"])) if envelope else None
    if (
        envelope
        and frozen
        and frozen["ref"] == ref
        and (
            envelope["record"]["state"] not in TERMINAL
            or envelope["record"]["effect_status"] == "unknown"
        )
    ):
        return {**row, "state": "admitted", "task_id": latest, "reason": None}
    return row


def dispatch_projections(owner: RememberPipeline, *, source_task_id: str | None = None) -> int:
    """Bound admission work by existing capacity; resume remaining intents next tick.

    No original Working body or eligibility is consulted after result commit.
    Long-term versions are independent objects, authorized using their own scope.
    """
    event_count = dispatch_events(owner, source_task_id)
    with owner.uow.transaction() as tx:
        pending = sorted(
            (
                (key, row)
                for key, row in tx.rows(TABLE)
                if row["state"] == "pending"
                and (source_task_id is None or row["source_task_id"] == source_task_id)
            ),
            key=lambda item: (item[1]["created_at"], item[0]),
        )
        active = [
            envelope["record"]
            for _, envelope in (
                tx.active_task_rows()
                if getattr(owner.uow, "backend", "") == "postgresql"
                else tx.rows("tasks")
            )
            if envelope["record"]["state"] not in TERMINAL
        ]
    scope_used: dict[str, int] = {}
    tenant_used: dict[str, int] = {}
    for task in active:
        scope = task["subject"]["scope"]
        scope_id, tenant_id = fingerprint(scope), scope["tenant_id"]
        scope_used[scope_id] = scope_used.get(scope_id, 0) + 1
        tenant_used[tenant_id] = tenant_used.get(tenant_id, 0) + 1
    # This bounds each sweep, not queue size or semantic output count. The source
    # of the bound is the configured shared capacity, never a second quota.
    limit = min(owner.tasks.pending_limit, owner.tasks.pending_limit_per_tenant)
    admitted = attempts = 0
    full_scopes: set[str] = set()
    for key, row in pending:
        scope_key = fingerprint(row["memory"]["scope"])
        tenant_key = row["memory"]["scope"]["tenant_id"]
        # Advisory preflight only: Tasks.enqueue rechecks exact quotas inside
        # its admission transaction. Skip saturated tenants so another tenant
        # is not starved by the beginning of the pending list.
        if (
            scope_key in full_scopes
            or scope_used.get(scope_key, 0) >= owner.tasks.pending_limit
            or tenant_used.get(tenant_key, 0) >= owner.tasks.pending_limit_per_tenant
        ):
            continue
        if attempts >= limit:
            break
        attempts += 1
        try:
            with owner.uow.transaction() as tx:
                added = _admit(owner, tx, key)
            admitted += added
            scope_used[scope_key] = scope_used.get(scope_key, 0) + added
            tenant_used[tenant_key] = tenant_used.get(tenant_key, 0) + added
        except FoundationError as error:
            # A Tasks abort poisons the transaction: handle it ONLY after the
            # context manager has rolled it back. Never write into that tx.
            if error.code == ErrorCode.CAPACITY_EXCEEDED:
                full_scopes.add(scope_key)
                _record_failure(owner, key, error.code, terminal=False)
            elif error.code in {
                ErrorCode.FORBIDDEN,
                ErrorCode.VERSION_CONFLICT,
                ErrorCode.CONTRACT_VIOLATION,
                ErrorCode.IDEMPOTENCY_CONFLICT,
            }:
                _record_failure(owner, key, error.code, terminal=True)
            else:
                # Transient admission/storage errors leave a durable pending
                # item. Do not mislabel the semantic result as failed.
                _record_failure(owner, key, error.code, terminal=False)
    return admitted + event_count


def _record_failure(owner: RememberPipeline, key: str, code: ErrorCode, *, terminal: bool) -> None:
    with owner.uow.transaction() as tx:
        row = tx.read(TABLE, key)
        if row and row["state"] == "pending":
            tx.write(
                TABLE,
                key,
                {**row, "state": "blocked" if terminal else "pending", "reason": code.value},
            )


def _admit(owner: RememberPipeline, tx: MetadataTransaction, key: str) -> int:
    from .pipeline import _task_policy

    row = tx.read(TABLE, key)
    if row is None or row["state"] not in {"pending", "blocked"}:
        return 0
    effective = _effective_row(tx, row)
    if effective != row:
        tx.write(TABLE, key, effective)
        return 0
    if row["state"] == "blocked":
        return 0
    if any(
        event["state"] in {"pending", "blocked"}
        and event["event"]["subject"]["object_id"] == row["memory"]["memory_id"]
        for _, event in tx.rows(EVENTS)
    ):
        return 0
    ref = MemoryRef.model_validate(row["memory"])
    pointer = tx.read("remember_current", ref.memory_id)
    raw = tx.get(RecordRef.model_validate(pointer)) if pointer else None
    if raw is None or raw["ref"] != row["memory"] or raw["status"] != "active":
        tx.write(TABLE, key, {**row, "state": "obsolete", "reason": "MEMORY_GONE"})
        return 0
    memory = (
        MemoryRecord.model_validate(raw)
        if "body_location" in raw
        else MemorySnapshot.model_validate(raw)
    )
    if memory.expires_at is not None and memory.expires_at <= owner.identity.clock():
        tx.write(TABLE, key, {**row, "state": "obsolete", "reason": "MEMORY_EXPIRED"})
        return 0
    ctx = TrustedContext.model_validate(row["context"]).model_copy(
        update={
            "deadline_at": later(owner.identity.clock(), owner.processing_seconds),
            "operation_id": key,
        }
    )
    owner.identity.revalidate(tx, ctx)
    token = _task_policy.set(RememberPolicy.model_validate(row["policy"]))
    try:
        if owner.checkpoint_binding("remember.project") != row["binding"]:
            tx.write(
                TABLE, key, {**row, "state": "blocked", "reason": ErrorCode.VERSION_CONFLICT.value}
            )
            return 0
        task_id = owner.enqueue(tx, ctx, memory, "remember.project")
    finally:
        _task_policy.reset(token)
    tx.write(TABLE, key, {**row, "state": "admitted", "task_id": task_id, "reason": None})
    return 1
