"""One fenced stage per delivery, with atomic SQL continuation and leased capacity.

The legacy temporal_* evidence tables are retained intentionally: domain body
write reconciliation, operation lookup and historical jobs share that evidence.
Every new binding has an explicit backend and a distinct orchestration identity.
"""

import asyncio
import os
import secrets
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Any

from aether_agent_memory.runtime.contracts.models import EffectStatus, ErrorCode
from aether_agent_memory.runtime.foundation.common import FoundationError, fingerprint, later
from aether_agent_memory.runtime.foundation.tasks import TERMINAL
from aether_agent_memory.runtime.foundation.transactions import business_write_guard
from aether_agent_memory.runtime.temporal.activities import Activities, _context
from aether_agent_memory.runtime.temporal.locking import ExecutionLimits
from aether_agent_memory.runtime.temporal.models import (
    CloseRequest,
    ExecutionRef,
    StepRequest,
    StepResult,
    WorkflowInput,
)

from .config import CeleryConfiguration


class CeleryExecution:
    def __init__(self, ledger: Any, registry: Any, config: CeleryConfiguration) -> None:
        self.ledger, self.registry, self.config = ledger, registry, config
        self.tasks = ledger.tasks
        self.executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="p3-celery")
        self.activities = Activities(
            ledger, registry, ExecutionLimits(self.tasks.class_limits), self.executor
        )

    def close(self) -> None:
        self.executor.shutdown(wait=True, cancel_futures=True)

    def bind(self, tx: Any, task: Any) -> WorkflowInput:
        job = WorkflowInput(
            job_id=task.task_id,
            kind=task.kind,
            deployment_id=self.ledger.config.deployment_id,
            input_hash=task.input_hash,
        )
        stage = next(iter(self.registry.policies(task.kind)))
        execution_id = f"celery/{job.deployment_id}/{job.kind}/{job.job_id}"
        tx.write(
            "temporal_bindings",
            task.task_id,
            {
                "backend": "celery",
                "job": job.model_dump(mode="json"),
                "namespace": self.ledger.config.namespace,
                "workflow_id": execution_id,
                "binding": None,
                "epoch": 0,
                "query_deadline_at": later(task.deadline_at, 60),
            },
        )
        row = {
            "state": "pending",
            "generation": 0,
            "ordinal": 0,
            "stage": stage,
            "mode": "execute",
            "due_at": self.tasks.clock(),
            "lease_until": "",
            "execution": None,
        }
        self.persist(tx, job, row)
        return job

    def persist(self, tx: Any, job: WorkflowInput, row: dict[str, Any]) -> None:
        tx.write("celery_jobs", job.job_id, row)
        tx.write(
            "celery_dispatch_intents",
            job.job_id,
            {
                "state": "pending",
                "due_at": row["due_at"],
                "job_id": job.job_id,
                "generation": row["generation"],
                "input_hash": job.input_hash,
                "deployment_id": job.deployment_id,
            },
        )

    def capacity(
        self, tx: Any, task: Any, execution_class: str, lease_until: str
    ) -> list[str] | None:
        stamp = self.tasks.clock()
        scope = fingerprint(task.subject.scope.model_dump(mode="json"))
        domains = [
            ("class:" + execution_class, self.tasks.class_limits[execution_class]),
            ("tenant:" + task.subject.scope.tenant_id, self.tasks.per_tenant_running),
            ("scope:" + scope, self.tasks.per_scope_running),
        ]
        selected = []
        for domain, limit in domains:
            available = None
            for ordinal in range(limit):
                key = fingerprint([self.ledger.config.deployment_id, domain, ordinal])
                owner = tx.read("celery_capacity", key)
                if not owner or owner["until"] <= stamp or owner["job_id"] == task.task_id:
                    available = key
                    break
            if available is None:
                return None
            selected.append(available)
        for key in selected:
            tx.write("celery_capacity", key, {"job_id": task.task_id, "until": lease_until})
        return selected

    def claim(
        self, job_id: str, generation: int, input_hash: str | None, deployment_id: str | None
    ) -> tuple[StepRequest, str, ExecutionRef] | None:
        with self.tasks.uow.transaction() as tx:
            binding = tx.read("temporal_bindings", job_id)
            row = tx.read("celery_jobs", job_id)
            if not binding or binding.get("backend") != "celery" or row is None:
                return None
            job = WorkflowInput.model_validate(binding["job"])
            if (
                (input_hash is not None and input_hash != job.input_hash)
                or (deployment_id is not None and deployment_id != job.deployment_id)
                or job.deployment_id != self.ledger.config.deployment_id
                or row["generation"] != generation
            ):
                return None
            task_row, task = self.tasks.load(tx, job_id)
            stamp = self.tasks.clock()
            if task.state in TERMINAL or row["due_at"] > stamp:
                return None
            if row["state"] == "running" and row["lease_until"] > stamp:
                return None
            mode = row["mode"]
            if row["state"] == "running" and task.effect_status == EffectStatus.UNKNOWN:
                mode = "reconcile"
            step = StepRequest(job=job, stage=row["stage"], ordinal=row["ordinal"], mode=mode)
            deadline = task.deadline_at if mode == "execute" else binding["query_deadline_at"]
            self.activities.authorized_context(tx, task, deadline)
            if fingerprint(tx.get(task.input_ref)) != task.input_hash:
                tx.abort(ErrorCode.VERSION_CONFLICT, "task input changed")
            policy = self.registry.get(job.kind, step.stage).policy
            until = later(stamp, policy.timeout_seconds + self.config.lease_grace_seconds)
            slots = self.capacity(tx, task, task_row["class"], until)
            if slots is None:
                self.persist(
                    tx, job, {**row, "state": "pending", "mode": mode, "due_at": later(stamp, 1)}
                )
                return None
            execution = ExecutionRef(
                namespace=binding["namespace"],
                workflow_id=binding["workflow_id"],
                run_id=job.job_id,
                activity_id=f"{step.ordinal}:{generation}:{secrets.token_hex(8)}",
                delivery_attempt=1,
                epoch=binding["epoch"] + 1,
            )
            tx.write(
                "celery_jobs",
                job_id,
                {
                    **row,
                    "state": "running",
                    "mode": mode,
                    "lease_until": until,
                    "execution": execution.model_dump(mode="json"),
                    "slots": slots,
                },
            )
            worker_id = "celery_" + fingerprint([job.deployment_id, os.getpid()])[:24]
            self.tasks.progress.heartbeat(tx, worker_id, task_row["class"])
            tx.write(
                "workers",
                worker_id,
                {
                    "state": "polling",
                    "worker_id": worker_id,
                    "execution_class": task_row["class"],
                    "last_seen": stamp,
                    "backend": "celery",
                },
            )
            return step, deadline, execution

    def finish(
        self,
        step: StepRequest,
        context: Any,
        result: StepResult,
        *,
        generation: int | None = None,
        execution: ExecutionRef | None = None,
        expected_owner: dict[str, Any] | None = None,
    ) -> StepResult | None:
        with self.tasks.uow.transaction() as tx:
            owner = tx.read("celery_jobs", step.job.job_id)
            if expected_owner is not None and owner != expected_owner:
                return None
            if generation is not None and owner["generation"] != generation:
                return None
            if execution is not None and owner.get("execution") != execution.model_dump(
                mode="json"
            ):
                return None
            control = tx.read("celery_controls", step.job.job_id)
            if control and control["request"]["action"] == "cancel" and result.outcome == "retry":
                result = StepResult(
                    outcome="obsolete",
                    effect_status=result.effect_status,
                    reason_code="CANCELLED_AFTER_RECONCILIATION",
                )
            if control and control["request"]["action"] == "cancel" and result.next_stage:
                result = StepResult(
                    outcome="attention",
                    effect_status=result.effect_status,
                    reason_code="CANCELLED_PARTIAL_EFFECT",
                )
            if context is not None:
                result = self.activities.record_in(tx, step, context, result)
            else:
                result = self.activities.close_in(tx, CloseRequest(job=step.job, result=result))
            row = tx.read("celery_jobs", step.job.job_id)
            _, task = self.tasks.load(tx, step.job.job_id)
            for slot in row.get("slots", []):
                owner = tx.read("celery_capacity", slot)
                if owner and owner["job_id"] == task.task_id:
                    tx.write("celery_capacity", slot, {**owner, "until": self.tasks.clock()})
            if task.state in TERMINAL:
                tx.write(
                    "celery_jobs", task.task_id, {**row, "state": "terminal", "lease_until": ""}
                )
                intent = tx.read("celery_dispatch_intents", task.task_id)
                tx.write("celery_dispatch_intents", task.task_id, {**intent, "state": "terminal"})
                if control:
                    operation = tx.read("operations", control["operation_id"])
                    tx.write(
                        "operations",
                        control["operation_id"],
                        {
                            **operation,
                            "record": {
                                **operation["record"],
                                "state": "completed",
                                "reason": "BUSINESS_" + task.state.value.upper(),
                            },
                        },
                    )
            else:
                next_stage = result.next_stage or step.stage
                ordinal = step.ordinal + (1 if result.next_stage else 0)
                due = self.tasks.clock()
                if result.outcome in {"retry", "query"}:
                    due = later(
                        due,
                        min(
                            60,
                            self.tasks.retry_seconds
                            * 2 ** min(task.query_attempt + task.attempt, 5),
                        ),
                    )
                self.persist(
                    tx,
                    step.job,
                    {
                        **row,
                        "state": "pending",
                        "generation": row["generation"] + 1,
                        "ordinal": ordinal,
                        "stage": next_stage,
                        "mode": "reconcile" if result.outcome == "query" else "execute",
                        "due_at": due,
                        "lease_until": "",
                        "execution": None,
                        "slots": [],
                    },
                )
            return result

    def expire(self, intent: dict[str, Any]) -> bool:
        """Settle queue deadlines even if no broker or worker is reachable."""
        with self.tasks.uow.transaction() as tx:
            row = tx.read("celery_jobs", intent["job_id"])
            binding = tx.read("temporal_bindings", intent["job_id"])
            _, task = self.tasks.load(tx, intent["job_id"])
            stamp = self.tasks.clock()
            if row["state"] == "running" and row["lease_until"] > stamp:
                return False
            deadline = (
                binding["query_deadline_at"]
                if task.effect_status == EffectStatus.UNKNOWN
                else task.deadline_at
            )
            if deadline > stamp:
                return False
            step = StepRequest(
                job=WorkflowInput.model_validate(binding["job"]),
                stage=row["stage"],
                ordinal=row["ordinal"],
                mode=row["mode"],
            )
        result = self.finish(
            step,
            None,
            StepResult(
                outcome="failed",
                effect_status=EffectStatus.NO_EFFECT,
                reason_code="DEADLINE_EXCEEDED",
            ),
            generation=intent["generation"],
            expected_owner=row,
        )
        return result is not None

    async def run(
        self,
        job_id: str,
        generation: int,
        input_hash: str | None = None,
        deployment_id: str | None = None,
    ) -> StepResult | None:
        try:
            claimed = await self.activities.blocking(
                lambda: self.claim(job_id, generation, input_hash, deployment_id)
            )
        except FoundationError as error:
            reason = error.code.value
            # Failed authorization rolls back claims; closure cannot claim an external effect.
            with self.tasks.uow.transaction() as tx:
                binding = tx.read("temporal_bindings", job_id)
                row = tx.read("celery_jobs", job_id)
            if not binding or not row or row["generation"] != generation:
                return None
            step = StepRequest(
                job=WorkflowInput.model_validate(binding["job"]),
                ordinal=row["ordinal"],
                stage=row["stage"],
                mode=row["mode"],
            )
            return await self.activities.blocking(
                lambda: self.finish(
                    step,
                    None,
                    StepResult(
                        outcome="failed", effect_status=EffectStatus.NO_EFFECT, reason_code=reason
                    ),
                    generation=generation,
                )
            )
        if claimed is None:
            return None
        step, deadline, execution = claimed
        try:
            context = await self.activities.blocking(
                lambda: self.activities.begin_step(step, deadline, execution)
            )
        except FoundationError as error:
            reason = error.code.value
            return await self.activities.blocking(
                lambda: self.finish(
                    step,
                    None,
                    StepResult(
                        outcome="failed", effect_status=EffectStatus.NO_EFFECT, reason_code=reason
                    ),
                    generation=generation,
                    execution=execution,
                )
            )
        if isinstance(context, StepResult):
            return await self.activities.blocking(
                lambda: self.finish(step, None, context, generation=generation, execution=execution)
            )
        stage = self.registry.get(step.job.kind, step.stage)
        token = _context.set(context)
        fence = business_write_guard.set(context.commit_fence)
        try:
            seconds = (
                datetime.fromisoformat(context.context.deadline_at)
                - datetime.fromisoformat(self.tasks.clock())
            ).total_seconds()
            try:
                async with asyncio.timeout(max(0, seconds)):
                    handler = stage.execute if step.mode == "execute" else stage.reconcile
                    result = await handler(step)
            except Exception:
                result = StepResult(
                    outcome="retry" if stage.policy.effect_mode == "read" else "query",
                    effect_status=EffectStatus.NO_EFFECT
                    if stage.policy.effect_mode == "read"
                    else EffectStatus.UNKNOWN,
                    reason_code="PROVIDER_INTERRUPTED",
                )
        finally:
            business_write_guard.reset(fence)
            _context.reset(token)
        return await self.activities.blocking(
            lambda: self.finish(step, context, result, generation=generation, execution=execution)
        )
