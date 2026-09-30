"""Explicit offline handoff, never database repair or automatic live conversion.

Inspection uses a read-only SQLite snapshot. Application owns the P3 directory
and a database write transaction, revalidates the entire report, and commits the
backend marker, original task bindings and transport intents together. An old
unpatched binary cannot obey this marker: operators must stop it and its restart
policy before invoking apply. A directory copy proves no such thing.
"""

import json
import sqlite3
from collections import Counter
from contextlib import closing
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict

from aether_agent_memory.operate.contracts.models import ActionIntent, ActionRecord
from aether_agent_memory.recall.basic.service import Recall
from aether_agent_memory.remember.basic.service import Remember
from aether_agent_memory.runtime.contracts.models import (
    EffectStatus,
    EventEnvelope,
    Flow,
    RecordRef,
    TaskRecord,
    TaskSpec,
    TaskState,
    TrustedContext,
)
from aether_agent_memory.runtime.foundation.common import encode, fingerprint, later, now
from aether_agent_memory.runtime.foundation.tasks import TERMINAL

from .config import TemporalConfiguration, deployment_configuration
from .locking import DirectoryLock
from .models import StartIntent, WorkflowInput

KINDS = {
    **dict.fromkeys(("remember.save", "remember.document", "remember.correct"), "remember"),
    **dict.fromkeys(
        (
            "remember." + s
            for s in (
                "extract",
                "project",
                "cleanup",
                "compress",
                "distill",
                "revalidate",
                "summarize",
            )
        ),
        "remember",
    ),
    "recall.execute": "recall",
    "operate.evaluate": "operate",
    "operate_repair_cache": "maintenance",
    "runtime.event_delivery": "engineering",
}
SUBSCRIBERS = {
    ("memory.changed", "operate"),
    ("recall.access", "operate"),
    ("recall.access", "remember_retention"),
}


class MigrationReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    database: str
    config_path: str
    database_fingerprint: str
    config_fingerprint: str
    temporal: TemporalConfiguration
    state_counts: dict[str, dict[str, int]]
    original_operations: dict[str, str]
    blockers: tuple[str, ...]


class MigrationResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    batch_id: str
    bindings: dict[str, WorkflowInput]
    retained_terminal_ids: tuple[str, ...]


def read_rows(db: sqlite3.Connection) -> list[tuple[str, str, str, str]]:
    return db.execute(
        "SELECT namespace,tenant,key,value FROM capability_records ORDER BY namespace,tenant,key"
    ).fetchall()


def table(rows: list[tuple[str, str, str, str]], name: str) -> dict[str, Any]:
    return {
        key: json.loads(value)
        for ns, tenant, key, value in rows
        if ns == "p3_rf_" + name and tenant == "system"
    }


def put(db: sqlite3.Connection, name: str, key: str, value: Any) -> None:
    db.execute(
        "INSERT INTO capability_records VALUES(?,?,?,?) "
        "ON CONFLICT(namespace,tenant,key) DO UPDATE SET value=excluded.value",
        ("p3_rf_" + name, "system", key, encode(value)),
    )


def inspect_rows(
    database: Path, config_path: Path, rows: list[tuple[str, str, str, str]]
) -> MigrationReport:
    config_bytes = config_path.read_bytes()
    config = yaml.safe_load(config_bytes)
    temporal = TemporalConfiguration.model_validate(config["temporal"])
    tasks, bindings = table(rows, "tasks"), table(rows, "temporal_bindings")
    records, blockers, operations = table(rows, "records"), [], {}
    marker = table(rows, "meta").get("execution_backend")
    if marker and (
        marker.get("deployment_id") != temporal.deployment_id
        or marker.get("namespace") != temporal.namespace
    ):
        blockers.append("BACKEND_BINDING_CHANGED")
    counts = {}
    for name in ("tasks", "deliveries", "recall_requests", "operate_actions"):
        counts[name] = dict(
            Counter(
                str(r.get("record", r).get("state", "unknown")) for r in table(rows, name).values()
            )
        )
    for key, row in tasks.items():
        task = TaskRecord.model_validate(row["record"])
        original = row.get("original_operation_id")
        action = table(rows, "operate_task_actions").get(key)
        if original or action:
            operations[key] = str(original or (action and action["action_id"]))
        if task.state in TERMINAL:
            continue
        if key in bindings:
            job = WorkflowInput.model_validate(bindings[key]["job"])
            if job.deployment_id != temporal.deployment_id or job.input_hash != task.input_hash:
                blockers.append(f"{key}:BINDING_CHANGED")
            continue
        if task.kind not in KINDS or row["class"] != KINDS.get(task.kind):
            blockers.append(f"{key}:UNREGISTERED_KIND:{task.kind}")
        value = records.get(fingerprint(task.input_ref.model_dump(mode="json")), {}).get("value")
        if value is None or fingerprint(value) != task.input_hash:
            blockers.append(f"{key}:INPUT_EVIDENCE_MISSING")
        if task.lease and task.lease.until > now():
            blockers.append(f"{key}:ACTIVE_LEASE")
        if (
            task.kind.startswith("remember.")
            and task.kind.split(".")[-1]
            in {"extract", "project", "cleanup", "compress", "distill", "revalidate", "summarize"}
            and key not in table(rows, "remember_task_binding")
        ):
            blockers.append(f"{key}:ADMISSION_MODEL_POLICY_BINDING_MISSING")
        if task.kind == "operate.evaluate" and task.effect_status == EffectStatus.UNKNOWN:
            if not action or action["action_id"] not in table(rows, "operate_actions"):
                blockers.append(f"{key}:ORIGINAL_ACTION_MISSING")
            elif original and original != action["action_id"]:
                blockers.append(f"{key}:ORIGINAL_ACTION_CHANGED")
            else:
                try:
                    intent = ActionIntent.model_validate(action)
                    record = ActionRecord.model_validate(
                        table(rows, "operate_actions")[intent.action_id]
                    )
                    if record.intent != intent:
                        blockers.append(f"{key}:ORIGINAL_ACTION_CHANGED")
                except ValueError:
                    blockers.append(f"{key}:ORIGINAL_ACTION_INVALID")
        if task.kind == "recall.execute" and (not value or not value.get("binding")):
            blockers.append(f"{key}:RECALL_MODEL_POLICY_BINDING_MISSING")
        if task.effect_status == EffectStatus.UNKNOWN and task.kind not in {
            "operate.evaluate",
            "recall.execute",
            "runtime.event_delivery",
        }:
            blockers.append(f"{key}:LEGACY_UNKNOWN_PHASE_UNMAPPED")
    for key, row in table(rows, "recall_requests").items():
        if row["record"]["state"] in {"accepted", "running"} and key not in tasks:
            blockers.append(f"{key}:RECALL_ADMISSION_BINDING_MISSING")
    outbox = table(rows, "outbox")
    for key, row in table(rows, "deliveries").items():
        if row["state"] in {"acknowledged", "attention_required"} or key in tasks:
            continue
        original = outbox.get(row["event_id"])
        if not original or original["signature"] != fingerprint(original["event"]):
            blockers.append(f"{key}:EVENT_EVIDENCE_MISSING")
            continue
        event = EventEnvelope.model_validate(original["event"])
        try:
            validator = {
                "memory.changed": Remember.validate_event,
                "recall.access": Recall.validate_event,
            }[event.event_type]
            validator(event)
            context = TrustedContext.model_validate(original["context"])
            if (
                event.payload_hash != fingerprint(event.payload)
                or context.principal.principal_id != event.initiator_id
                or context.principal.auth_epoch != event.initiator_auth_epoch
                or context.request_id != event.request_id
                or context.trace_id != event.trace_id
            ):
                raise ValueError("event context or payload binding changed")
        except (ValueError, KeyError):
            blockers.append(f"{key}:EVENT_DOMAIN_INVALID")
        if (event.event_type, row["consumer_id"]) not in SUBSCRIBERS:
            blockers.append(f"{key}:EVENT_SUBSCRIBER_UNREGISTERED")
        if key != fingerprint([row["consumer_id"], event.event_id]):
            blockers.append(f"{key}:EVENT_ID_CHANGED")
        if row["attempt"] >= 6 and key not in table(rows, "inbox"):
            blockers.append(f"{key}:LEGACY_EVENT_BUDGET_EXHAUSTED")
        if row.get("lease") and row["lease"]["until"] > now():
            blockers.append(f"{key}:ACTIVE_LEASE")
        operations[key] = event.event_id
    for key, row in table(rows, "workers").items():
        if row.get("state") != "stopped" and later(row["last_seen"], 60) > now():
            blockers.append(f"{key}:RECENT_WORKER_HEARTBEAT")
    return MigrationReport(
        database=str(database.resolve()),
        config_path=str(config_path.resolve()),
        database_fingerprint=fingerprint(rows),
        config_fingerprint=fingerprint(config_bytes.hex()),
        temporal=temporal,
        state_counts=counts,
        original_operations=operations,
        blockers=tuple(sorted(blockers)),
    )


def inspect_migration(database: Path, *, config_path: Path | None = None) -> MigrationReport:
    database = database.resolve(strict=True)
    with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as db:
        db.execute("BEGIN")
        return inspect_rows(
            database, config_path or database.parent / "service.yaml", read_rows(db)
        )


def check_service_backend(database: Path, config: TemporalConfiguration) -> None:
    """Called under directory ownership, before constructing any mutable service."""
    if not database.exists():
        return
    with closing(sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True)) as db:
        rows = read_rows(db)
    marker = table(rows, "meta").get("execution_backend")
    expected = deployment_configuration(config)
    if marker and marker != {
        "backend": "temporal",
        "deployment_id": config.deployment_id,
        "namespace": config.namespace,
        "task_queue_prefix": expected.task_queue_prefix,
    }:
        raise ValueError("Temporal backend binding changed; explicit migration required")
    bindings = table(rows, "temporal_bindings")
    for key, row in table(rows, "tasks").items():
        if row["record"]["state"] not in TERMINAL and key not in bindings:
            raise ValueError("historical tasks require explicit offline migration")
    for key, row in table(rows, "deliveries").items():
        if row["state"] not in {"acknowledged", "attention_required"} and key not in bindings:
            raise ValueError("historical deliveries require explicit offline migration")
    for key, row in table(rows, "recall_requests").items():
        if row["record"]["state"] in {"accepted", "running"} and key not in bindings:
            raise ValueError("historical Recall requires explicit offline migration")


def bind(
    db: sqlite3.Connection, task: TaskRecord, row: dict[str, Any], config: TemporalConfiguration
) -> WorkflowInput:
    reconcile = task.state in {TaskState.RUNNING, TaskState.RECOVERY_WAIT} or (
        task.effect_status == EffectStatus.UNKNOWN
    )
    job = WorkflowInput(
        job_id=task.task_id,
        kind=task.kind,
        deployment_id=config.deployment_id,
        input_hash=task.input_hash,
        entry="reconcile" if reconcile else "execute",
    )
    workflow_id = f"p3/{job.deployment_id}/{job.kind}/{job.job_id}"
    intent = StartIntent(
        intent_id=fingerprint(workflow_id),
        job=job,
        task_queue=f"{config.task_queue_prefix}.{row['class']}",
    )
    put(
        db,
        "temporal_bindings",
        task.task_id,
        {
            "job": job.model_dump(mode="json"),
            "namespace": config.namespace,
            "workflow_id": workflow_id,
            "binding": None,
            "epoch": 0,
        },
    )
    put(
        db,
        "temporal_start_intents",
        intent.intent_id,
        {"state": "pending", "intent": intent.model_dump(mode="json")},
    )
    # Keep attempts, IDs, deadlines, model bindings and business evidence intact.
    updated = TaskRecord.model_validate(
        {
            **task.model_dump(),
            "lease": None,
            "state": TaskState.RECOVERY_WAIT if task.state == TaskState.RUNNING else task.state,
            "revision": task.revision + 1,
        }
    )
    put(
        db,
        "tasks",
        task.task_id,
        {**row, "record": updated.model_dump(mode="json"), "migrated_from_rf": True},
    )
    if task.attempt:
        put(db, "temporal_stage_budgets", f"{task.task_id}:0", {"executions": task.attempt})
    return job


def migrate_events(db: sqlite3.Connection, rows: list[tuple[str, str, str, str]]) -> None:
    tasks, outbox = table(rows, "tasks"), table(rows, "outbox")
    for key, delivery in table(rows, "deliveries").items():
        if delivery["state"] in {"acknowledged", "attention_required"} or key in tasks:
            continue
        original = outbox[delivery["event_id"]]
        event = EventEnvelope.model_validate(original["event"])
        deadline = later(event.occurred_at, 86400)
        ctx = TrustedContext.model_validate(original["context"]).model_copy(
            update={"deadline_at": deadline, "operation_id": event.event_id}
        )
        subject = RecordRef(
            owner=Flow.RUNTIME,
            object_type="event_delivery",
            object_id=key,
            scope=ctx.principal.home_scope,
        )
        ref = subject.model_copy(update={"object_type": "event_delivery_input"})
        value = {
            "event_id": event.event_id,
            "consumer_id": delivery["consumer_id"],
            "signature": original["signature"],
        }
        put(
            db,
            "records",
            fingerprint(ref.model_dump(mode="json")),
            {"ref": ref.model_dump(mode="json"), "revision": 1, "value": value},
        )
        spec = TaskSpec(
            task_id=key,
            kind="runtime.event_delivery",
            owner_flow=Flow.RUNTIME,
            subject=subject,
            input_ref=ref,
            input_hash=fingerprint(value),
            initiator_id=event.initiator_id,
            initiator_auth_epoch=event.initiator_auth_epoch,
            idempotency_key=key,
            deadline_at=deadline,
            max_attempts=6,
        )
        task = TaskRecord(
            **spec.model_dump(),
            state=TaskState.RECOVERY_WAIT,
            revision=1,
            attempt=delivery["attempt"],
            effect_status=EffectStatus.UNKNOWN,
        )
        put(
            db,
            "tasks",
            key,
            {
                "record": task.model_dump(mode="json"),
                "context": ctx.model_dump(mode="json"),
                "class": "engineering",
                "created_at": event.occurred_at,
            },
        )
        put(db, "deliveries", key, {**delivery, "lease": None})


def apply_migration(
    database: Path,
    report: MigrationReport,
    deployment_id: str,
    *,
    legacy_service_stopped: bool = False,
) -> MigrationResult:
    if not legacy_service_stopped:
        raise ValueError("stop the original service and disable its automatic restart first")
    database = database.resolve(strict=True)
    if str(database) != report.database or deployment_id != report.temporal.deployment_id:
        raise ValueError("report belongs to another database or deployment")
    lock = DirectoryLock()
    lock.acquire(database.parent)
    try:
        with (
            closing(sqlite3.connect(database.as_uri() + "?mode=rw", uri=True, timeout=0)) as db,
            db,
        ):
            db.execute("PRAGMA synchronous=FULL")
            db.execute("BEGIN IMMEDIATE")
            rows = read_rows(db)
            batch_id = fingerprint(report.model_dump(mode="json"))
            prior = table(rows, "temporal_migration_batches").get(batch_id)
            if prior:
                if (
                    fingerprint(Path(report.config_path).read_bytes().hex())
                    != report.config_fingerprint
                ):
                    raise ValueError("stale migration configuration")
                return MigrationResult.model_validate(prior)
            current = inspect_rows(database, Path(report.config_path), rows)
            if current != report:
                raise ValueError("stale migration report; inspect again")
            if report.blockers:
                raise ValueError("migration blocked: " + ", ".join(report.blockers))
            migrate_events(db, rows)
            rows = read_rows(db)
            jobs, retained = {}, []
            config = deployment_configuration(report.temporal)
            bindings = table(rows, "temporal_bindings")
            for key, row in table(rows, "tasks").items():
                task = TaskRecord.model_validate(row["record"])
                if task.state in TERMINAL:
                    retained.append(key)
                elif key in bindings:
                    jobs[key] = WorkflowInput.model_validate(bindings[key]["job"])
                else:
                    jobs[key] = bind(db, task, row, config)
            result = MigrationResult(
                batch_id=batch_id, bindings=jobs, retained_terminal_ids=tuple(sorted(retained))
            )
            put(
                db,
                "meta",
                "execution_backend",
                {
                    "backend": "temporal",
                    "deployment_id": deployment_id,
                    "namespace": config.namespace,
                    "task_queue_prefix": config.task_queue_prefix,
                },
            )
            put(db, "temporal_migration_batches", batch_id, result.model_dump(mode="json"))
            return result
    finally:
        lock.release()
