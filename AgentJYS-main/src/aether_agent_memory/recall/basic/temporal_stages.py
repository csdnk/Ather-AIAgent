"""Recall candidates and plans are references, never plaintext Workflow payloads."""

import asyncio
from typing import Any

from pydantic import TypeAdapter

from aether_agent_memory.recall.contracts.foundation import (
    ContextAssemblyPlan,
    ContextCommitRequest,
)
from aether_agent_memory.recall.contracts.models import RecallRequest
from aether_agent_memory.remember.contracts.foundation import ContextGuardRequest
from aether_agent_memory.runtime.contracts.models import (
    EffectStatus,
    ErrorCode,
    RecordRef,
    TrustedContext,
)
from aether_agent_memory.runtime.foundation.common import FoundationError, fingerprint
from aether_agent_memory.runtime.temporal.activities import StageContext
from aether_agent_memory.runtime.temporal.models import StepRequest, StepResult
from aether_agent_memory.runtime.temporal.recall import recall_binding

from .generation import GenerationRecall
from .retrievers import SourceResult
from .service import Recall

source_results = TypeAdapter(list[SourceResult])


class RecallStages:
    def __init__(self, recall: Recall) -> None:
        self.recall = recall

    def input(self) -> tuple[TrustedContext, RecallRequest, str]:
        c = StageContext.current()
        with self.recall.uow.transaction() as tx:
            c.guard(tx)
            value = tx.get(c.task.input_ref)
        if value is None or value["binding"] != recall_binding(self.recall):
            raise FoundationError(
                ErrorCode.VERSION_CONFLICT, "Recall policy or model binding changed"
            )
        # Keep the business deadline unchanged. The enclosing Activity independently
        # enforces its delivery timeout, and its commit fence guards every transaction.
        ctx = c.context.model_copy(update={"deadline_at": c.task.deadline_at})
        return ctx, RecallRequest.model_validate(value["request"]), str(value["recall_id"])

    def ref(self, phase: str) -> RecordRef:
        return StageContext.current().task.subject.model_copy(
            update={"object_type": "recall_phase_" + phase}
        )

    def load(self, phase: str) -> dict[str, Any] | None:
        self.input()
        with self.recall.uow.transaction() as tx:
            value = tx.get(self.ref(phase))
        if value is None:
            return None
        if not isinstance(value["data"], dict) or value["hash"] != fingerprint(value["data"]):
            raise FoundationError(ErrorCode.VERSION_CONFLICT, "Recall phase changed")
        return dict(value["data"])

    def save(self, phase: str, data: dict[str, Any]) -> RecordRef:
        ref = self.ref(phase)
        value: dict[str, Any] = {"data": data, "hash": fingerprint(data)}
        with self.recall.uow.transaction() as tx:
            StageContext.current().guard(tx)
            old = tx.get(ref)
            if old is None:
                tx.put_if_revision(ref, value, None)
            elif old != value:
                tx.abort(ErrorCode.IDEMPOTENCY_CONFLICT, "Recall phase changed")
        return ref

    def done(self, phase: str, next_stage: str | None) -> StepResult:
        return StepResult(
            outcome="done",
            result_ref=self.ref(phase),
            next_stage=next_stage,
            effect_status=EffectStatus.CONFIRMED,
            reason_code="RECALL_PHASE_READY",
        )

    async def prepare(self, step: StepRequest) -> StepResult:
        (await asyncio.to_thread(self.input))
        (await asyncio.to_thread(self.save, "prepared", {"job_id": step.job.job_id}))
        return self.done("prepared", "candidates")

    async def candidates(self, step: StepRequest) -> StepResult:
        if (await asyncio.to_thread(self.load, "candidates")) is None:
            ctx, request, recall_id = await asyncio.to_thread(self.input)
            results = await self.recall.discover_candidates(ctx, request, recall_id)
            reasons = {
                reason
                for result in results
                for reason in (result.reason or "").split(";")
                if reason
            }
            if not any(result.candidates for result in results) and reasons == {"index_pending"}:
                raise FoundationError(ErrorCode.REQUEST_IN_PROGRESS, "Recall index not ready")
            (
                await asyncio.to_thread(
                    self.save,
                    "candidates",
                    {"results": source_results.dump_python(results, mode="json")},
                )
            )
        return self.done("candidates", "assemble")

    async def assemble(self, step: StepRequest) -> StepResult:
        if (await asyncio.to_thread(self.load, "assembled")) is None:
            ctx, request, recall_id = await asyncio.to_thread(self.input)
            candidates = await asyncio.to_thread(self.load, "candidates")
            if candidates is None:
                raise FoundationError(ErrorCode.CONTRACT_VIOLATION, "Recall candidates missing")
            prepared = await self.recall.assemble_candidates(
                ctx, request, recall_id, source_results.validate_python(candidates["results"])
            )
            (await asyncio.to_thread(self.save, "assembled", prepared))
        return self.done("assembled", "commit")

    async def commit(self, step: StepRequest) -> StepResult:
        c = StageContext.current()
        ctx, request, recall_id = await asyncio.to_thread(self.input)
        data = await asyncio.to_thread(self.load, "assembled")
        if data is None:
            raise FoundationError(ErrorCode.CONTRACT_VIOLATION, "Recall plan missing")
        ref = self.ref("result")

        def commit_result() -> None:
            with self.recall.uow.transaction() as tx:
                c.guard(tx)
                self.recall.commit_prepared(tx, ctx, request, recall_id, data)
                tx.put_if_revision(ref, {"recall_id": recall_id}, None)
                c.ledger.complete(tx, c.task.task_id, c.execution, ref)

        await asyncio.to_thread(commit_result)
        return self.done("result", None)


class GenerationStages(RecallStages):
    recall: GenerationRecall

    def __init__(self, recall: GenerationRecall) -> None:
        super().__init__(recall)

    async def candidates(self, step: StepRequest) -> StepResult:
        if (await asyncio.to_thread(self.load, "candidates")) is None:
            ctx, request, recall_id = await asyncio.to_thread(self.input)
            await asyncio.to_thread(self.recall.stage, ctx, recall_id, "discover")
            plan_request = await asyncio.to_thread(
                self.recall.plan_request, ctx, request, recall_id
            )
            results = await self.recall.assembly.discover(ctx, plan_request)
            if (
                not results["snapshots"]
                and results["reasons"]
                and all(reason.endswith("_index_pending") for reason in results["reasons"])
            ):
                # Do not freeze an empty pending-index observation as a completed
                # stage. The bounded retry must rediscover the now-published index.
                raise FoundationError(ErrorCode.REQUEST_IN_PROGRESS, "Recall index not ready")
            (await asyncio.to_thread(self.save, "candidates", results))
        return self.done("candidates", "assemble")

    async def assemble(self, step: StepRequest) -> StepResult:
        if (await asyncio.to_thread(self.load, "assembled")) is None:
            ctx, request, recall_id = await asyncio.to_thread(self.input)
            plan_request = await asyncio.to_thread(
                self.recall.plan_request, ctx, request, recall_id
            )

            def read_plan() -> ContextAssemblyPlan | None:
                with self.recall.uow.transaction() as tx:
                    saved = tx.read("recall_assembly", recall_id)
                    if saved is not None:
                        plan = ContextAssemblyPlan.model_validate(saved["plan"])
                        if plan.request != plan_request or saved["signature"] != fingerprint(
                            plan.model_dump(mode="json")
                        ):
                            tx.abort(ErrorCode.VERSION_CONFLICT, "persisted Recall plan changed")
                        self.recall.assembly.revalidate(
                            tx,
                            ctx,
                            ContextGuardRequest.model_validate(saved["expectations"]),
                            ctx.deadline_at,
                        )
                return plan if saved is not None else None

            plan = await asyncio.to_thread(read_plan)
            if plan is None:
                discovered = await asyncio.to_thread(self.load, "candidates")
                if discovered is None:
                    raise FoundationError(ErrorCode.CONTRACT_VIOLATION, "Recall candidates missing")
                await asyncio.to_thread(self.recall.stage, ctx, recall_id, "assemble")
                plan = await self.recall.assembly.assemble_discovered(ctx, plan_request, discovered)
            (
                await asyncio.to_thread(
                    self.save, "assembled", {"plan": plan.model_dump(mode="json")}
                )
            )
        return self.done("assembled", "commit")

    async def commit(self, step: StepRequest) -> StepResult:
        c = StageContext.current()
        ctx, _, recall_id = await asyncio.to_thread(self.input)
        data = await asyncio.to_thread(self.load, "assembled")
        if data is None:
            raise FoundationError(ErrorCode.CONTRACT_VIOLATION, "Recall plan missing")
        plan = ContextAssemblyPlan.model_validate(data["plan"])
        ref = self.ref("result")

        def commit_result() -> None:
            with self.recall.uow.transaction() as tx:
                c.guard(tx)
                row = tx.read("recall_requests", recall_id)
                self.recall.assembly.commit(
                    tx,
                    ctx,
                    ContextCommitRequest(
                        operation_id=ctx.operation_id,
                        expected_recall_revision=row["record"]["revision"],
                        plan=plan,
                        final_guards=tuple(
                            b.guard for u in plan.units for b in u.bodies if b.guard is not None
                        ),
                    ),
                )
                tx.put_if_revision(ref, {"recall_id": recall_id}, None)
                c.ledger.complete(tx, c.task.task_id, c.execution, ref)

        await asyncio.to_thread(commit_result)
        return self.done("result", None)
