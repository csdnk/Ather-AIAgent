"""Lease-fenced attachments to the existing RF task engine; no second queue."""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import JsonValue

from aether_agent_memory.runtime.contracts.foundation import (
    CheckpointRecord,
    TaskWaitRecord,
    WorkerHeartbeat,
)
from aether_agent_memory.runtime.contracts.models import (
    EffectStatus,
    ErrorCode,
    Permission,
    RecordRef,
    TaskRecord,
    TrustedContext,
)
from aether_agent_memory.runtime.storage.ports import MetadataTransaction

from .common import fingerprint, later

if TYPE_CHECKING:
    from .tasks import Tasks


class TaskProgress:
    def __init__(self, tasks: Tasks, instance_id: str) -> None:
        self.tasks, self.instance_id = tasks, instance_id

    def part(
        self,
        tx: MetadataTransaction,
        ctx: TrustedContext,
        task: TaskRecord,
        stage: str,
        table: str,
        key: str,
        *,
        config_version: str,
    ) -> None:
        """Index an existing durable business result, without copying its body.

        Called in the same transaction as the result write, and also when an
        older result is reused after restart. Each part contributes only once.
        """
        _, current = self.tasks.guard(tx, task)
        value = tx.read(table, key)
        if value is None:
            tx.abort(ErrorCode.CONTRACT_VIOLATION, "stage result is not durable")
        part_id = fingerprint([task.task_id, stage, table, key])
        ref = RecordRef(
            owner=task.subject.owner,
            object_type="task_part",
            object_id=part_id,
            scope=task.subject.scope,
        )
        self.tasks.identity.authorize(tx, ctx, Permission.READ, ref)
        payload: dict[str, JsonValue] = {
            "table": table,
            "key": key,
            "result_hash": fingerprint(value),
        }
        prior = tx.get(ref)
        if prior is None:
            tx.put_if_revision(ref, payload, None)
        elif prior != payload:
            tx.abort(ErrorCode.IDEMPOTENCY_CONFLICT, "committed stage result changed")
        self.checkpoint(
            tx,
            ctx,
            task,
            CheckpointRecord(
                task_id=task.task_id,
                expected_task_revision=current.revision,
                stage=stage + "_" + part_id[:32],
                input_hash=task.input_hash,
                output_refs=(ref,),
                committed_at=self.tasks.clock(),
                config_version=config_version,
            ),
        )
        count_key = task.task_id + ":" + stage
        summary = tx.read("task_stage_counts", count_key) or {
            "task_id": task.task_id,
            "stage": stage,
            "completed_parts": 0,
        }
        if prior is None:
            tx.write(
                "task_stage_counts",
                count_key,
                {
                    **summary,
                    "completed_parts": summary["completed_parts"] + 1,
                    "updated_at": self.tasks.clock(),
                },
            )

    def defer(
        self,
        tx: MetadataTransaction,
        ctx: TrustedContext,
        task: TaskRecord,
        dependency: str,
        reason: str,
        operation_id: str,
        effect: EffectStatus,
    ) -> None:
        """Record a real handler wait; scheduling remains owned by Tasks."""
        _, current = self.tasks.guard(tx, task)
        stamp = self.tasks.clock()
        next_check = min(later(stamp, self.tasks.retry_seconds), task.deadline_at)
        if next_check <= stamp:
            return
        self.wait(
            tx,
            ctx,
            task,
            TaskWaitRecord(
                task_id=task.task_id,
                expected_task_revision=current.revision,
                dependency_id=dependency,
                reason_code=reason,
                wait_started_at=stamp,
                next_check_at=next_check,
                deadline_at=task.deadline_at,
                original_operation_id=operation_id,
                effect_status=effect,
                resume_mode="query_only" if effect == EffectStatus.UNKNOWN else "resume",
            ),
        )

    def checkpoint(
        self,
        tx: MetadataTransaction,
        ctx: TrustedContext,
        task: TaskRecord,
        record: CheckpointRecord,
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
        self, tx: MetadataTransaction, ctx: TrustedContext, task: TaskRecord, record: TaskWaitRecord
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
            {
                "token": current.lease.token if current.lease else None,
                "execution": current.execution.model_dump(mode="json")
                if current.execution
                else None,
            },
        )
        tx.before_commit.append(lambda: self.tasks.identity.revalidate(tx, ctx))

    def read(self, ctx: TrustedContext, task_id: str) -> dict[str, object]:
        with self.tasks.uow.transaction() as tx:
            _, task = self.tasks.load(tx, task_id)
            self.tasks.identity.authorize(tx, ctx, Permission.DIAGNOSE, task.subject)
            wait = tx.read("task_waits", task_id)
            lease = tx.read("task_wait_leases", task_id)
            owned = lease and (
                (task.lease and lease["token"] == task.lease.token)
                or (
                    task.execution
                    and lease.get("execution") == task.execution.model_dump(mode="json")
                )
            )
            if task.state.value not in {"retry_wait", "recovery_wait"} and not owned:
                wait = None
            return {
                "wait": wait,
                "stages": [v for _, v in tx.rows("task_stage_counts") if v["task_id"] == task_id],
                "checkpoints": [
                    v for _, v in tx.rows("task_checkpoints") if v["task_id"] == task_id
                ],
            }

    def heartbeat(
        self,
        tx: MetadataTransaction,
        worker_id: str,
        execution_class: str,
        *,
        task: TaskRecord | None = None,
        stopped: bool = False,
    ) -> WorkerHeartbeat:
        # Existing routing names stay compatible; this field describes resources.
        category = {
            "remember": "model",
            "remember_ingress": "io",
            "remember_index": "io",
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
