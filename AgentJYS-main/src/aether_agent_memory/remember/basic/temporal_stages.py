"""Remember command stages, reusing the same domain preparation and commit methods."""

import asyncio
import json
from collections.abc import Awaitable, Callable
from typing import Any

from aether_agent_memory.remember.contracts.models import CorrectionRequest, RememberRequest
from aether_agent_memory.remember.documents import Documents
from aether_agent_memory.runtime.contracts.models import EffectStatus, ErrorCode, RecordRef, Scope
from aether_agent_memory.runtime.foundation.common import FoundationError, fingerprint
from aether_agent_memory.runtime.storage.ports import MetadataUnitOfWork
from aether_agent_memory.runtime.temporal.activities import StageContext
from aether_agent_memory.runtime.temporal.ingress import InputStore
from aether_agent_memory.runtime.temporal.models import StepRequest, StepResult


def confirm_body_replay(uow: MetadataUnitOfWork, table: str, key: str) -> None:
    """Resolve a reserved retry only after exact remote bytes have been observed."""
    with uow.transaction() as tx:
        StageContext.current().guard(tx)
        prior = tx.read(table, key)
        if prior and prior.get("replay_attempts") and prior.get("replay_state") != "confirmed":
            tx.write(table, key, {**prior, "replay_state": "confirmed"})


async def replay_absent_body(
    uow: MetadataUnitOfWork,
    table: str,
    key: str,
    publish: Callable[[], Awaitable[Any]],
    *,
    initial_intent: dict[str, Any] | None = None,
) -> StepResult | None:
    """Reserve a bounded retry of a capability-checked immutable absent object.

    The caller must have observed an absent object, not a failed read. Retain the
    original intent: a previous request may still be in flight despite that read.
    Recovery may enter before the initial publish activity; its frozen intent and
    first attempt must then be registered together before any external write.
    """
    context = StageContext.current()

    def reserve_replay() -> StepResult | None:
        with uow.transaction() as tx:
            context.guard(tx)
            prior = tx.read(table, key)
            if not prior:
                if initial_intent is None:
                    tx.abort(
                        ErrorCode.CONTRACT_VIOLATION, "immutable replay requires a write intent"
                    )
                prior = {**initial_intent, "intent_created_by_reconciliation": True}
            attempts = prior.get("replay_attempts", 0)
            if attempts >= context.task.max_attempts:
                tx.write(table, key, {**prior, "replay_state": "exhausted"})
                return StepResult(
                    outcome="attention",
                    effect_status=EffectStatus.UNKNOWN,
                    original_operation_id=context.context.operation_id,
                    reason_code="IMMUTABLE_BODY_REPLAY_EXHAUSTED",
                )
            tx.write(
                table,
                key,
                {
                    **prior,
                    "replay_attempts": attempts + 1,
                    "replay_limit": context.task.max_attempts,
                    "replay_state": "reserved",
                    "replay_reason": "confirmed_absent_immutable_write",
                },
            )
        return None

    reservation = await asyncio.to_thread(reserve_replay)
    if reservation is not None:
        return reservation
    await publish()
    (await asyncio.to_thread(confirm_body_replay, uow, table, key))
    return None


class SaveStages:
    def __init__(self, inputs: InputStore, remember: Any) -> None:
        self.inputs, self.remember = inputs, remember

    async def request(self) -> RememberRequest:
        context = StageContext.current()
        return RememberRequest.model_validate_json(
            await asyncio.to_thread(self.inputs.read, context.context, context.task.input_ref)
        )

    async def payload(self) -> dict[str, Any]:
        context = StageContext.current()
        value = json.loads(
            await asyncio.to_thread(self.inputs.read, context.context, context.task.input_ref)
        )
        if not isinstance(value, dict):
            raise FoundationError(ErrorCode.INVALID_ARGUMENT, "command requires an object payload")
        return value

    def ref(self, name: str) -> RecordRef:
        return StageContext.current().task.subject.model_copy(
            update={"object_type": "command_" + name}
        )

    def save(self, name: str, data: Any) -> RecordRef:
        context = StageContext.current()
        ref = self.ref(name)
        value = json.loads(json.dumps(data, ensure_ascii=False))
        with self.inputs.uow.transaction() as tx:
            context.guard(tx)
            prior = tx.get(ref)
            if prior is None:
                tx.put_if_revision(ref, value, None)
            elif prior != value:
                tx.abort(ErrorCode.IDEMPOTENCY_CONFLICT, "command stage result changed")
        return ref

    def load(self, name: str) -> dict[str, Any]:
        with self.inputs.uow.transaction() as tx:
            StageContext.current().guard(tx)
            value: dict[str, Any] | None = tx.get(self.ref(name))
            if value is None:
                tx.abort(ErrorCode.CONTRACT_VIOLATION, "command stage missing")
            return value

    async def prepare(self, step: StepRequest) -> StepResult:
        prepared = await self.remember.prepare_save(
            StageContext.current().context, await self.request()
        )
        ref = await asyncio.to_thread(self.save, "prepared", prepared)
        return StepResult(
            outcome="done",
            next_stage="persist",
            result_ref=ref,
            effect_status=EffectStatus.NO_EFFECT,
            reason_code="PREPARED",
        )

    async def persist(self, step: StepRequest) -> StepResult:
        context = StageContext.current()
        prepared = await asyncio.to_thread(self.load, "prepared")
        if "previous" not in prepared:
            scope = Scope.model_validate(prepared["scope"])
            for text in dict.fromkeys((prepared["text"], prepared["working_text"])):
                location = self.remember.bodies.location(scope, text)
                key = fingerprint([context.task.task_id, location.object_key])

                def read_body_intent(key: str) -> Any:
                    with self.inputs.uow.transaction() as tx:
                        context.guard(tx)
                        prior = tx.read("temporal_body_writes", key)
                    return prior

                prior = await asyncio.to_thread(read_body_intent, key)
                if prior:
                    checked = await self.check_body(scope, text, key)
                    if checked is not None:
                        return checked
                    continue

                def reserve_body(key: str, location: Any) -> None:
                    with self.inputs.uow.transaction() as tx:
                        context.guard(tx)
                        tx.write(
                            "temporal_body_writes",
                            key,
                            {"object_key": location.object_key, "started": True},
                        )

                await asyncio.to_thread(reserve_body, key, location)
                await self.remember.bodies.persist(context.context, scope, text)
        return StepResult(
            outcome="done",
            next_stage="commit",
            result_ref=self.ref("prepared"),
            effect_status=EffectStatus.CONFIRMED,
            reason_code="BODY_CONFIRMED",
        )

    async def reconcile_persist(self, step: StepRequest) -> StepResult:
        prepared = await asyncio.to_thread(self.load, "prepared")
        if "previous" not in prepared:
            scope = Scope.model_validate(prepared["scope"])
            for text in dict.fromkeys((prepared["text"], prepared["working_text"])):
                location = self.remember.bodies.location(scope, text)
                key = fingerprint([step.job.job_id, location.object_key])
                checked = await self.check_body(scope, text, key)
                if checked is not None:
                    return checked
        return StepResult(
            outcome="done",
            next_stage="commit",
            result_ref=self.ref("prepared"),
            effect_status=EffectStatus.CONFIRMED,
            reason_code="BODY_RECONCILED",
        )

    async def check_body(self, scope: Scope, text: str, key: str) -> StepResult | None:
        context = StageContext.current()
        bodies = self.remember.bodies
        location = bodies.location(scope, text)

        def read_body_intent() -> Any:
            with self.inputs.uow.transaction() as tx:
                context.guard(tx)
                prior = tx.read("temporal_body_writes", key)
            return prior

        prior = await asyncio.to_thread(read_body_intent)
        if bodies.p2:
            raw = await bodies.p2_call("get_object", location.object_key)
            if raw is None:
                if getattr(bodies.p2, "immutable_write_replay_safe", False) is True:
                    return await replay_absent_body(
                        self.inputs.uow,
                        "temporal_body_writes",
                        key,
                        lambda: bodies.persist(context.context, scope, text),
                        initial_intent={"object_key": location.object_key, "started": True},
                    )
                return StepResult(
                    outcome="query" if prior else "retry",
                    effect_status=EffectStatus.UNKNOWN if prior else EffectStatus.NO_EFFECT,
                    original_operation_id=context.context.operation_id,
                    reason_code="BODY_UNCONFIRMED",
                )
            if raw != text.encode("utf-8"):
                raise FoundationError(ErrorCode.CONTRACT_VIOLATION, "P2 exact body hash differs")
            (
                await asyncio.to_thread(
                    confirm_body_replay, self.inputs.uow, "temporal_body_writes", key
                )
            )
            bodies.prepared[location.object_key] = location
            await context.blocking(lambda: bodies._spool(location, text))
        else:
            # Local immutable publication can be retried without an external operation.
            await context.blocking(lambda: bodies._spool(location, text))
        return None

    async def commit(self, step: StepRequest) -> StepResult:
        # Restore verified body handles after a process restart, before entering a transaction.
        checked = await self.reconcile_persist(step)
        if checked.outcome != "done":
            return checked
        result = await asyncio.to_thread(
            self.remember.commit_save,
            StageContext.current().context,
            await self.request(),
            (await asyncio.to_thread(self.load, "prepared")),
        )
        ref = await asyncio.to_thread(self.save, "receipt", result.model_dump(mode="json"))
        return StepResult(
            outcome="done",
            next_stage="admit_cache",
            result_ref=ref,
            effect_status=EffectStatus.CONFIRMED,
            reason_code="SAVED",
        )

    async def admit_cache(self, step: StepRequest) -> StepResult:
        from aether_agent_memory.remember.contracts.models import RememberReceipt

        receipt = RememberReceipt.model_validate(await asyncio.to_thread(self.load, "receipt"))
        await self.remember.admit_save_cache(
            StageContext.current().context,
            (await asyncio.to_thread(self.load, "prepared")),
            receipt,
        )
        return StepResult(
            outcome="done",
            result_ref=self.ref("receipt"),
            effect_status=EffectStatus.CONFIRMED,
            reason_code="CACHE_CHECKED",
        )


class CorrectionStages(SaveStages):
    async def prepare(self, step: StepRequest) -> StepResult:
        payload = await self.payload()
        prepared = await asyncio.to_thread(
            self.remember.prepare_correction,
            StageContext.current().context,
            payload["memory_id"],
            CorrectionRequest.model_validate(payload["request"]),
        )
        return StepResult(
            outcome="done",
            next_stage="persist",
            result_ref=(await asyncio.to_thread(self.save, "prepared", prepared)),
            effect_status=EffectStatus.NO_EFFECT,
            reason_code="CORRECTION_PREPARED",
        )

    async def commit(self, step: StepRequest) -> StepResult:
        checked = await self.reconcile_persist(step)
        if checked.outcome != "done":
            return checked
        payload = await self.payload()
        result = await asyncio.to_thread(
            self.remember.correct,
            StageContext.current().context,
            payload["memory_id"],
            CorrectionRequest.model_validate(payload["request"]),
        )
        return StepResult(
            outcome="done",
            next_stage="admit_cache",
            result_ref=(
                await asyncio.to_thread(self.save, "receipt", result.model_dump(mode="json"))
            ),
            effect_status=EffectStatus.CONFIRMED,
            reason_code="CORRECTED",
        )

    async def admit_cache(self, step: StepRequest) -> StepResult:
        from aether_agent_memory.remember.contracts.models import RememberReceipt

        receipt = RememberReceipt.model_validate(await asyncio.to_thread(self.load, "receipt"))
        await self.remember.admit_correction_cache(StageContext.current().context, receipt)
        return StepResult(
            outcome="done",
            result_ref=self.ref("receipt"),
            effect_status=EffectStatus.CONFIRMED,
            reason_code="CORRECTION_COMMITTED",
        )


class DocumentStages(SaveStages):
    def __init__(self, inputs: InputStore, remember: Any) -> None:
        super().__init__(inputs, remember)
        self.documents = Documents(remember)

    async def body(self) -> bytes:
        payload = await self.payload()
        return await asyncio.to_thread(
            self.inputs.read,
            StageContext.current().context,
            RecordRef.model_validate(payload["blob_ref"]),
        )

    async def prepare(self, step: StepRequest) -> StepResult:
        payload = await self.payload()
        prepared = await asyncio.to_thread(
            self.documents.prepare_upload,
            StageContext.current().context,
            payload["document_id"],
            payload["version"],
            await self.body(),
            payload["media_type"],
        )
        return StepResult(
            outcome="done",
            next_stage="persist",
            result_ref=(await asyncio.to_thread(self.save, "prepared", prepared)),
            effect_status=EffectStatus.NO_EFFECT,
            reason_code="DOCUMENT_PREPARED",
        )

    async def persist(self, step: StepRequest) -> StepResult:
        prepared = await asyncio.to_thread(self.load, "prepared")
        context = StageContext.current()

        def read_document_intent() -> Any:
            with self.inputs.uow.transaction() as tx:
                started = tx.read("temporal_document_writes", step.job.job_id)
            return started

        started = await asyncio.to_thread(read_document_intent)
        if started:
            return await self.reconcile_persist(step)

        def reserve_document() -> None:
            with self.inputs.uow.transaction() as tx:
                context.guard(tx)
                tx.write(
                    "temporal_document_writes",
                    step.job.job_id,
                    {"key": prepared["value"]["object_key"]},
                )

        await asyncio.to_thread(reserve_document)
        await self.documents.persist_upload(prepared, await self.body())
        return StepResult(
            outcome="done",
            next_stage="commit",
            result_ref=self.ref("prepared"),
            effect_status=EffectStatus.CONFIRMED,
            reason_code="DOCUMENT_CONFIRMED",
        )

    async def publish_document(self, prepared: dict[str, Any]) -> None:
        await self.documents.persist_upload(prepared, await self.body())

    async def reconcile_persist(self, step: StepRequest) -> StepResult:
        prepared = await asyncio.to_thread(self.load, "prepared")
        key = prepared["value"]["object_key"]
        raw = await self.remember.bodies.p2_call("get_object", key)
        if raw is None:

            def read_document_intent() -> Any:
                with self.inputs.uow.transaction() as tx:
                    started = tx.read("temporal_document_writes", step.job.job_id)
                return started

            started = await asyncio.to_thread(read_document_intent)
            if getattr(self.remember.bodies.p2, "immutable_write_replay_safe", False) is True:
                checked = await replay_absent_body(
                    self.inputs.uow,
                    "temporal_document_writes",
                    step.job.job_id,
                    lambda: self.publish_document(prepared),
                    initial_intent={"key": key},
                )
                if checked is not None:
                    return checked
                return StepResult(
                    outcome="done",
                    next_stage="commit",
                    result_ref=self.ref("prepared"),
                    effect_status=EffectStatus.CONFIRMED,
                    reason_code="DOCUMENT_REPLAY_CONFIRMED",
                )
            return StepResult(
                outcome="query" if started else "retry",
                effect_status=EffectStatus.UNKNOWN if started else EffectStatus.NO_EFFECT,
                original_operation_id=key,
                reason_code="DOCUMENT_UNCONFIRMED",
            )
        if raw != await self.body():
            raise FoundationError(ErrorCode.CONTRACT_VIOLATION, "document bytes differ")
        (
            await asyncio.to_thread(
                confirm_body_replay, self.inputs.uow, "temporal_document_writes", step.job.job_id
            )
        )
        return StepResult(
            outcome="done",
            next_stage="commit",
            result_ref=self.ref("prepared"),
            effect_status=EffectStatus.CONFIRMED,
            reason_code="DOCUMENT_RECONCILED",
        )

    async def commit(self, step: StepRequest) -> StepResult:
        checked = await self.reconcile_persist(step)
        if checked.outcome != "done":
            return checked
        result = await asyncio.to_thread(
            self.documents.commit_upload,
            StageContext.current().context,
            (await asyncio.to_thread(self.load, "prepared")),
        )
        return StepResult(
            outcome="done",
            next_stage="admit_cache",
            result_ref=(
                await asyncio.to_thread(self.save, "receipt", result.model_dump(mode="json"))
            ),
            effect_status=EffectStatus.CONFIRMED,
            reason_code="DOCUMENT_COMMITTED",
        )

    async def admit_cache(self, step: StepRequest) -> StepResult:
        return StepResult(
            outcome="done",
            result_ref=self.ref("receipt"),
            effect_status=EffectStatus.CONFIRMED,
            reason_code="DOCUMENT_READY",
        )
