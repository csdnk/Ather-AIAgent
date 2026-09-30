"""Fenced action phases and independently verified cache repair."""

import asyncio
from functools import partial
from typing import Any

from aether_agent_memory.operate.contracts.models import ActionIntent, ActionRecord, ActionState
from aether_agent_memory.remember.contracts.models import MemoryRef, MemorySnapshot
from aether_agent_memory.runtime.contracts.foundation import IncidentRecord
from aether_agent_memory.runtime.contracts.models import (
    EffectStatus,
    ErrorCode,
    RecordRef,
    RunResult,
)
from aether_agent_memory.runtime.foundation.common import FoundationError, fingerprint
from aether_agent_memory.runtime.temporal.activities import StageContext
from aether_agent_memory.runtime.temporal.models import StepRequest, StepResult

from .maintenance import CacheMaintenance
from .service import Operate


class StoredStages:
    def __init__(self, uow: Any) -> None:
        self.uow = uow

    def ref(self, phase: str) -> RecordRef:
        c = StageContext.current()
        return c.task.subject.model_copy(
            update={"object_type": "operate_phase_" + phase, "object_id": c.task.task_id}
        )

    def load(self, phase: str) -> dict[str, Any] | None:
        with self.uow.transaction() as tx:
            StageContext.current().guard(tx)
            row = tx.get(self.ref(phase))
        if row is None:
            return None
        if not isinstance(row["data"], dict) or row["hash"] != fingerprint(row["data"]):
            raise FoundationError(ErrorCode.VERSION_CONFLICT, "Operate stage changed")
        return dict(row["data"])

    def required(self, phase: str) -> dict[str, Any]:
        value = self.load(phase)
        if value is None:
            raise FoundationError(ErrorCode.CONTRACT_VIOLATION, "Operate predecessor missing")
        return value

    def save(self, phase: str, value: dict[str, Any]) -> RecordRef:
        ref = self.ref(phase)
        data = {"data": value, "hash": fingerprint(value)}
        with self.uow.transaction() as tx:
            StageContext.current().guard(tx)
            old = tx.get(ref)
            if old is None:
                tx.put_if_revision(ref, data, None)
            elif old != data:
                tx.abort(ErrorCode.IDEMPOTENCY_CONFLICT, "Operate phase changed")
        return ref

    def done(self, phase: str, next_stage: str | None) -> StepResult:
        return StepResult(
            outcome="done",
            result_ref=self.ref(phase),
            next_stage=next_stage,
            effect_status=EffectStatus.CONFIRMED,
            reason_code="OPERATE_PHASE_READY",
        )

    @staticmethod
    def outcome(result: RunResult) -> StepResult:
        outcomes = {
            "committed": "done",
            "retryable_no_effect": "retry",
            "uncertain": "query",
            "obsolete": "obsolete",
            "failed": "failed",
        }
        return StepResult(
            outcome=outcomes[result.outcome],
            result_ref=result.result_ref,
            effect_status=result.effect_status,
            original_operation_id=result.operation_id,
            reason_code="OPERATE_" + result.outcome.upper(),
        )

    @staticmethod
    def unknown(operation_id: str) -> StepResult:
        return StepResult(
            outcome="query",
            effect_status=EffectStatus.UNKNOWN,
            original_operation_id=operation_id,
            reason_code="ORIGINAL_EFFECT_UNCONFIRMED",
        )

    @staticmethod
    def not_started() -> StepResult:
        return StepResult(
            outcome="retry",
            effect_status=EffectStatus.NO_EFFECT,
            reason_code="NO_SUBMISSION_RECORDED",
        )


class OperateStages(StoredStages):
    def __init__(self, owner: Operate) -> None:
        super().__init__(owner.uow)
        self.owner = owner

    async def prepare(self, step: StepRequest) -> StepResult:
        if self.load("prepared") is None:
            c = StageContext.current()
            result = await self.owner.prepare_evaluation(c.context, c.task)
            if isinstance(result, RunResult):
                return self.outcome(result)
            self.save("prepared", result)
        return self.done("prepared", "submit")

    def intent(self, data: dict[str, Any]) -> ActionIntent:
        intent = ActionIntent.model_validate(data["intent"])
        if (
            intent.provider_id != self.owner.executor.provider_id
            or intent.provider_instance_id != self.owner.executor.instance_id
        ):
            raise FoundationError(ErrorCode.VERSION_CONFLICT, "original action provider changed")
        with self.uow.transaction() as tx:
            persisted = tx.read("operate_actions", intent.action_id)
        if persisted is not None and ActionRecord.model_validate(persisted).intent != intent:
            raise FoundationError(ErrorCode.VERSION_CONFLICT, "original action binding changed")
        return intent

    async def submit(self, step: StepRequest) -> StepResult:
        c = StageContext.current()
        data = self.required("prepared")
        if "intent" in data:
            self.intent(data)
        if data.get("cleanup") and data["valid"]:
            with self.uow.transaction() as tx:
                started = tx.read("temporal_cache_cleanup", c.task.task_id)
            if started:
                return await self.reconcile(step)
            with self.uow.transaction() as tx:
                c.guard(tx)
                tx.write("temporal_cache_cleanup", c.task.task_id, data)
        result = await self.owner.submit_evaluation(c.context, c.task, data)
        if isinstance(result, RunResult):
            return self.outcome(result)
        self.save("evaluated", result)
        return self.done("evaluated", "reconcile")

    async def reconcile(self, step: StepRequest) -> StepResult:
        c, data = StageContext.current(), self.required("prepared")
        if "intent" in data:
            self.intent(data)
        if self.load("evaluated") is not None:
            return self.done("evaluated", "commit")
        if "intent" in data:
            intent = self.intent(data)
            with self.uow.transaction() as tx:
                raw = tx.read("operate_actions", intent.action_id)
            if raw is None:
                return self.not_started()
            action = ActionRecord.model_validate(raw)
            if action.state == ActionState.GENERATED:
                return self.not_started()
            if action.state not in {
                ActionState.SUCCEEDED,
                ActionState.FAILED,
                ActionState.CANCELLED,
            }:
                action = await self.owner.reconcile(c.context, intent.action_id)
            result = self.owner.evaluation_feedback(c.context, c.task, action)
            if isinstance(result, RunResult):
                return self.outcome(result)
        elif data.get("cleanup") and data["valid"]:
            memory = MemoryRef.model_validate(data["memory"])
            with self.uow.transaction() as tx:
                started = tx.read("temporal_cache_cleanup", c.task.task_id)
            if not started:
                return self.not_started()
            with self.owner.executor.db() as db:
                rows = db.execute("SELECT memory FROM copies").fetchall()
                tombstone = db.execute(
                    "SELECT version FROM tombstones WHERE id=?", (memory.memory_id,)
                ).fetchone()
            if any(
                (ref := MemoryRef.model_validate_json(row[0])).scope == memory.scope
                and ref.memory_id == memory.memory_id
                and ref.version <= memory.version
                for row in rows
            ) or (data["permanent"] and (not tombstone or tombstone[0] < memory.version)):
                return self.unknown(c.task.task_id)
            result = {"cache_cleanup": "completed"}
        else:
            result = data.get("value", {"cache_cleanup": "ineligible"})
        self.save("evaluated", result)
        return self.done("evaluated", "commit")

    async def commit(self, step: StepRequest) -> StepResult:
        c, value = StageContext.current(), self.required("evaluated")
        prepared = self.required("prepared")
        if "intent" in prepared:
            self.intent(prepared)
        await self.owner.before_completion(c.context, c.task, value)
        with self.uow.transaction() as tx:
            c.guard(tx)
            result = self.owner.finish(tx, c.context, c.task, value)
        return self.outcome(result)


class RepairStages(StoredStages):
    def __init__(self, owner: CacheMaintenance) -> None:
        super().__init__(owner.rf.uow)
        self.owner = owner

    def item(self) -> MemorySnapshot:
        return MemorySnapshot.model_validate(self.required("diagnosed")["item"])

    async def diagnose(self, step: StepRequest) -> StepResult:
        if self.load("diagnosed") is None:
            c = StageContext.current()
            item = await self.owner.prepare_repair(c.context, c.task)
            if isinstance(item, RunResult):
                return self.outcome(item)
            self.save("diagnosed", {"item": item.model_dump(mode="json")})
        return self.done("diagnosed", "repair")

    async def repair(self, step: StepRequest) -> StepResult:
        c = StageContext.current()
        with self.uow.transaction() as tx:
            started = tx.read("temporal_cache_repairs", c.task.task_id)
        if started:
            return await self.reconcile(step)
        with self.uow.transaction() as tx:
            c.guard(tx)
            tx.write("temporal_cache_repairs", c.task.task_id, {"started": True})
        await c.blocking(partial(self.owner.repair_prepared, c.context, c.task, self.item()))
        return self.done("diagnosed", "verify")

    async def reconcile(self, step: StepRequest) -> StepResult:
        c, item = StageContext.current(), self.item()
        with self.uow.transaction() as tx:
            c.guard(tx)
            started = tx.read("temporal_cache_repairs", c.task.task_id)
            eligible = self.owner.eligible(tx, c.context, item.ref)
        if not started:
            return self.not_started()
        with self.owner.executor.db() as db:
            original = db.execute(
                "SELECT memory,content_hash FROM repairs WHERE id=?", (c.task.task_id,)
            ).fetchone()
        if (
            original == (item.ref.model_dump_json(), item.content_hash)
            and eligible
            and self.owner.inspect(item.ref, item.content_hash)
        ):
            return self.done("diagnosed", "verify")
        return self.unknown(c.task.task_id)

    async def evidence(self) -> tuple[IncidentRecord, tuple[RecordRef, ...]]:
        c = StageContext.current()
        with self.uow.transaction() as tx:
            c.guard(tx)
            incident = IncidentRecord.model_validate(tx.get(c.task.input_ref))
            row = tx.read("incidents", incident.incident_id)
        verifier = self.owner.rf.dispositions.verifiers[row["rule"]["verification_operation"]]
        async with asyncio.timeout(2):
            refs = await verifier(c.context, incident, c.task)
        return incident, refs

    async def verify(self, step: StepRequest) -> StepResult:
        _, refs = await self.evidence()
        if not refs:
            return self.unknown(step.job.job_id)
        self.save("verified", {"refs": [r.model_dump(mode="json") for r in refs]})
        return self.done("verified", "close")

    async def close(self, step: StepRequest) -> StepResult:
        c, item = StageContext.current(), self.item()
        incident, refs = await self.evidence()
        if not refs:
            return self.unknown(c.task.task_id)
        with self.uow.transaction() as tx:
            c.guard(tx)
            self.owner.rf.dispositions.resolve_verified(
                tx, c.context, incident.incident_id, c.task.task_id, refs
            )
            result = self.owner.finish(tx, c.context, c.task, item.content_hash)
        return self.outcome(result)
