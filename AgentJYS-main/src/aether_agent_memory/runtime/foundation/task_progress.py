"""Lease-fenced attachments to the existing RF task engine; no second queue."""

from __future__ import annotations

from typing import TYPE_CHECKING

from aether_agent_memory.runtime.contracts.foundation import (
    CheckpointRecord,
    TaskWaitRecord,
    WorkerHeartbeat,
)
from aether_agent_memory.runtime.contracts.models import (
    ErrorCode,
    Permission,
    TaskRecord,
    TrustedContext,
)

from .common import later
from .storage import SQLiteTransaction

if TYPE_CHECKING:
    from .tasks import Tasks


class TaskProgress:
    def __init__(self, tasks: Tasks, instance_id: str) -> None:
        self.tasks, self.instance_id = tasks, instance_id

    def checkpoint(
        self, tx: SQLiteTransaction, ctx: TrustedContext, task: TaskRecord, record: CheckpointRecord
    ) -> None:
        _, current = self.tasks.guard(tx, task)
        self.tasks.identity.authorize(
            tx, ctx, self.tasks.permissions.get(task.kind, Permission.WRITE), task.subject
        )
        if (
            record.task_id != task.task_id
            or record.expected_task_revision != current.revision
            or record.input_hash != task.input_hash
            or record.committed_at > self.tasks.clock()
        ):
            tx.abort(ErrorCode.VERSION_CONFLICT, "checkpoint does not bind current task/input/time")
        for ref in record.output_refs:
            self.tasks.identity.authorize(tx, ctx, Permission.READ, ref)
            if ref.scope != task.subject.scope or tx.get(ref) is None:
                tx.abort(ErrorCode.CONTRACT_VIOLATION, "checkpoint output missing or out of scope")
        key = task.task_id + ":" + record.stage
        prior = tx.read("task_checkpoints", key)
        if prior:
            stable = {"task_id", "stage", "input_hash", "output_refs", "config_version"}
            current_dump = record.model_dump(mode="json")
            if any(prior[k] != current_dump[k] for k in stable):
                tx.abort(ErrorCode.IDEMPOTENCY_CONFLICT, "checkpoint stage already committed")
            return
        tx.write("task_checkpoints", key, record.model_dump(mode="json"))
        tx.before_commit.append(lambda: self.tasks.identity.revalidate(tx, ctx))

    def wait(
        self, tx: SQLiteTransaction, ctx: TrustedContext, task: TaskRecord, record: TaskWaitRecord
    ) -> None:
        row, current = self.tasks.guard(tx, task)
        self.tasks.identity.authorize(
            tx, ctx, self.tasks.permissions.get(task.kind, Permission.WRITE), task.subject
        )
        if (
            record.task_id != task.task_id
            or record.expected_task_revision != current.revision
            or record.deadline_at > task.deadline_at
            or record.next_check_at <= self.tasks.clock()
            or record.wait_started_at > self.tasks.clock()
        ):
            tx.abort(ErrorCode.VERSION_CONFLICT, "wait does not bind current task or deadline")
        original = row.get("original_operation_id")
        if original and original != record.original_operation_id:
            tx.abort(ErrorCode.IDEMPOTENCY_CONFLICT, "wait cannot change original operation")
        tx.write(
            "tasks", task.task_id, {**row, "original_operation_id": record.original_operation_id}
        )
        tx.write("task_waits", task.task_id, record.model_dump(mode="json"))
        tx.write(
            "task_wait_leases",
            task.task_id,
            {"token": current.lease.token if current.lease else None},
        )
        tx.before_commit.append(lambda: self.tasks.identity.revalidate(tx, ctx))

    def read(self, ctx: TrustedContext, task_id: str) -> dict[str, object]:
        with self.tasks.uow.transaction() as tx:
            _, task = self.tasks.load(tx, task_id)
            self.tasks.identity.authorize(tx, ctx, Permission.DIAGNOSE, task.subject)
            return {
                "wait": tx.read("task_waits", task_id),
                "checkpoints": [
                    v for _, v in tx.rows("task_checkpoints") if v["task_id"] == task_id
                ],
            }

    def heartbeat(
        self,
        tx: SQLiteTransaction,
        worker_id: str,
        execution_class: str,
        *,
        task: TaskRecord | None = None,
        stopped: bool = False,
    ) -> WorkerHeartbeat:
        # Existing routing names stay compatible; this field describes resources.
        category = {
            "remember": "model",
            "recall": "model",
            "operate": "io",
            "engineering": "maintenance",
        }.get(execution_class, execution_class)
        stamp = self.tasks.clock()
        record = WorkerHeartbeat(
            worker_id=worker_id,
            instance_id=self.instance_id,
            execution_class=category,
            state="stopped" if stopped else "running" if task else "polling",
            last_seen=stamp,
            fresh_until=later(stamp, self.tasks.lease_seconds * 2),
            task_id=task.task_id if task else None,
            lease_token=task.lease.token if task and task.lease else None,
        )
        tx.write("worker_heartbeats", worker_id, record.model_dump(mode="json"))
        tx.write(
            "workers",
            worker_id,
            {
                "worker_id": worker_id,
                "execution_class": execution_class,
                "state": "stopped" if stopped else "executing" if task else "polling",
                "last_seen": stamp,
            },
        )
        return record
