"""Remember's domain phases. Large intermediate values remain in fenced P3 storage."""

import asyncio
from functools import partial
from typing import Any, cast

from aether_agent_memory.remember.contracts.models import MemorySnapshot
from aether_agent_memory.runtime.contracts.models import (
    EffectStatus,
    ErrorCode,
    RecordRef,
    RunResult,
)
from aether_agent_memory.runtime.foundation.common import FoundationError, fingerprint
from aether_agent_memory.runtime.temporal.activities import StageContext
from aether_agent_memory.runtime.temporal.models import StepRequest, StepResult
from aether_agent_memory.runtime.temporal.registry import StageHandler

from .pipeline import RememberPipeline, _task_kind, _task_policy
from .policy import RememberPolicy
from .temporal_stages import confirm_body_replay, replay_absent_body


class BackgroundStages:
    def __init__(self, owner: RememberPipeline) -> None:
        self.owner = owner

    def handler(self, phase: str, *, reconcile: bool = False) -> StageHandler:
        async def call(step: StepRequest) -> StepResult:
            context = StageContext.current()
            if context.task.kind == "remember.summarize":
                # Historical provider bindings may no longer exist. Retirement
                # uses the live task fence, not an obsolete model configuration.
                return await self.retire_summary()

            def read_policy() -> tuple[Any, Any]:
                with self.owner.uow.transaction() as tx:
                    policy = tx.read("remember_task_policy", context.task.task_id)
                    binding = tx.read("remember_task_binding", context.task.task_id)
                return policy, binding

            policy, binding = await asyncio.to_thread(read_policy)
            if policy is None:
                raise FoundationError(
                    ErrorCode.CONTRACT_VIOLATION, "frozen Remember policy missing"
                )
            token = _task_policy.set(RememberPolicy.model_validate(policy))
            kind_token = _task_kind.set(context.task.kind)
            try:
                if binding != self.owner.checkpoint_binding():
                    raise FoundationError(
                        ErrorCode.VERSION_CONFLICT, "Remember model binding changed"
                    )
                return cast(StepResult, await getattr(self, phase)(step, reconcile=reconcile))
            finally:
                _task_kind.reset(kind_token)
                _task_policy.reset(token)

        return call

    async def retire_summary(self) -> StepResult:
        context = StageContext.current()
        result = await asyncio.to_thread(self.owner.summaries.retire, context.context, context.task)
        return self.outcome(result)

    def ref(self, name: str) -> RecordRef:
        return StageContext.current().task.subject.model_copy(
            update={
                "object_type": "remember_stage_" + name,
                "object_id": StageContext.current().task.task_id,
            }
        )

    def load(self, name: str) -> dict[str, Any] | None:
        with self.owner.uow.transaction() as tx:
            StageContext.current().guard(tx)
            row = tx.get(self.ref(name))
        if row is None:
            return None
        if (
            row["hash"] != fingerprint(row["data"])
            or row["binding"] != self.owner.checkpoint_binding()
        ):
            raise FoundationError(
                ErrorCode.VERSION_CONFLICT, "Remember stage input or configuration changed"
            )
        if not isinstance(row["data"], dict):
            raise FoundationError(
                ErrorCode.CONTRACT_VIOLATION, "Remember snapshot must be an object"
            )
        return dict(row["data"])

    def required(self, name: str) -> dict[str, Any]:
        value = self.load(name)
        if value is None:
            raise FoundationError(ErrorCode.CONTRACT_VIOLATION, "Remember predecessor missing")
        return value

    def save(self, name: str, value: dict[str, Any]) -> RecordRef:
        row: dict[str, Any] = {
            "data": value,
            "hash": fingerprint(value),
            "binding": self.owner.checkpoint_binding(),
        }
        ref = self.ref(name)
        with self.owner.uow.transaction() as tx:
            StageContext.current().guard(tx)
            previous = tx.get(ref)
            if previous is None:
                tx.put_if_revision(ref, row, None)
            elif previous != row:
                tx.abort(ErrorCode.IDEMPOTENCY_CONFLICT, "Remember stage snapshot changed")
        return ref

    def generation(self) -> str:
        with self.owner.uow.transaction() as tx:
            retry = (
                tx.read("remember_comparison_retries", StageContext.current().task.task_id) or {}
            )
        return "generation_" + str(retry.get("attempts", 0))

    def done(self, name: str, next_stage: str | None) -> StepResult:
        return StepResult(
            outcome="done",
            next_stage=next_stage,
            result_ref=self.ref(name),
            effect_status=EffectStatus.CONFIRMED,
            reason_code="REMEMBER_PHASE_READY",
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
            reason_code="REMEMBER_" + result.outcome.upper(),
        )

    def items(self) -> tuple[MemorySnapshot, ...]:
        return tuple(MemorySnapshot.model_validate(i) for i in self.required("prepared")["items"])

    async def prepare(self, step: StepRequest, *, reconcile: bool) -> StepResult:
        if StageContext.current().task.kind == "remember.summarize":
            return await self.retire_summary()
        if (await asyncio.to_thread(self.load, "prepared")) is None:
            context = StageContext.current()
            items = await self.owner.prepare_background(context.context, context.task)
            if isinstance(items, RunResult):
                return self.outcome(items)
            (
                await asyncio.to_thread(
                    self.save, "prepared", {"items": [i.model_dump(mode="json") for i in items]}
                )
            )
        return self.done("prepared", "generate")

    async def generate(self, step: StepRequest, *, reconcile: bool) -> StepResult:
        if StageContext.current().task.kind == "remember.summarize":
            return await self.retire_summary()
        name = await asyncio.to_thread(self.generation)
        if (await asyncio.to_thread(self.load, name)) is None:
            c = StageContext.current()
            items, kind = (await asyncio.to_thread(self.items)), c.task.kind
            value: dict[str, Any] | RunResult
            if kind in {"remember.extract", "remember.distill"}:
                value = await self.owner.generate_extraction(c.context, c.task, items)
            elif kind == "remember.compress":
                value = await self.owner.generate_compression(c.context, c.task, items[0])
            elif kind == "remember.project":
                value = await self.owner.generate_projection(c.context, c.task, items[0])
            elif kind == "remember.cleanup":
                value = await asyncio.to_thread(
                    self.owner.prepare_cleanup, c.context, c.task, items[0]
                )
            else:
                # Revalidation checks current source certificates in its commit transaction.
                value = {}
            if isinstance(value, RunResult):
                return self.outcome(value)
            (await asyncio.to_thread(self.save, name, value))
        return self.done(name, "publish")

    def texts(self, data: dict[str, Any]) -> list[str]:
        kind = StageContext.current().task.kind
        if kind == "remember.compress":
            return [data["text"]] if data["text"] is not None else []
        if kind in {"remember.extract", "remember.distill"}:
            # A preceding amendment can turn an equivalent candidate into a
            # conflict during commit. Confirm every accepted body beforehand;
            # an unused immutable object may remain when no fact references it.
            return list(
                dict.fromkeys(
                    c["text"] for c, d, _ in data["proposals"] if d["outcome"] != "reject"
                )
            )
        return []

    async def publish_bodies(self, data: dict[str, Any], *, reconcile: bool) -> StepResult | None:
        if StageContext.current().task.kind == "remember.summarize":
            return await self.retire_summary()
        c, bodies = StageContext.current(), self.owner.bodies
        scope = (await asyncio.to_thread(self.items))[0].ref.scope
        for text in self.texts(data):
            location = bodies.location(scope, text)
            key = fingerprint([c.task.task_id, location.object_key])

            def read_body_intent(key: str) -> Any:
                with self.owner.uow.transaction() as tx:
                    c.guard(tx)
                    started = tx.read("temporal_body_writes", key)
                return started

            started = await asyncio.to_thread(read_body_intent, key)
            if started or reconcile:
                if bodies.p2:
                    raw = await bodies.p2_call("get_object", location.object_key)
                    if raw is None:
                        if getattr(bodies.p2, "immutable_write_replay_safe", False) is True:
                            checked = await replay_absent_body(
                                self.owner.uow,
                                "temporal_body_writes",
                                key,
                                partial(bodies.persist, c.context, scope, text),
                                initial_intent={"object_key": location.object_key},
                            )
                            if checked is not None:
                                return checked
                            continue
                        return StepResult(
                            outcome="query" if started else "retry",
                            effect_status=EffectStatus.UNKNOWN
                            if started
                            else EffectStatus.NO_EFFECT,
                            original_operation_id=c.task.task_id,
                            reason_code="BODY_UNCONFIRMED",
                        )
                    if raw != text.encode("utf-8"):
                        raise FoundationError(ErrorCode.CONTRACT_VIOLATION, "Remember body differs")
                    (
                        await asyncio.to_thread(
                            confirm_body_replay, self.owner.uow, "temporal_body_writes", key
                        )
                    )
                    bodies.prepared[location.object_key] = location
                await c.blocking(partial(bodies._spool, location, text))
            else:

                def reserve_body(key: str, location: Any) -> None:
                    with self.owner.uow.transaction() as tx:
                        c.guard(tx)
                        tx.write("temporal_body_writes", key, {"object_key": location.object_key})

                await asyncio.to_thread(reserve_body, key, location)
                await bodies.persist(c.context, scope, text)
        return None

    async def publish(self, step: StepRequest, *, reconcile: bool) -> StepResult:
        if StageContext.current().task.kind == "remember.summarize":
            return await self.retire_summary()
        c = StageContext.current()
        name, items = (
            (await asyncio.to_thread(self.generation)),
            (await asyncio.to_thread(self.items)),
        )
        data = await asyncio.to_thread(self.required, name)
        result = None
        if c.task.kind == "remember.project":
            result = await self.owner.publish_projection(
                c.context, c.task, items[0], data, reconcile=reconcile
            )
        elif c.task.kind == "remember.cleanup":
            result = await self.owner.publish_cleanup(c.context, c.task, data, reconcile=reconcile)
        else:
            checked = await self.publish_bodies(data, reconcile=reconcile)
            if checked is not None:
                return checked
        return self.outcome(result) if result is not None else self.done(name, "commit")

    async def commit(self, step: StepRequest, *, reconcile: bool) -> StepResult:
        if StageContext.current().task.kind == "remember.summarize":
            return await self.retire_summary()
        c, items = StageContext.current(), (await asyncio.to_thread(self.items))
        data = await asyncio.to_thread(self.required, (await asyncio.to_thread(self.generation)))
        checked = await self.publish(step, reconcile=True)
        if checked.outcome != "done":
            return checked
        kind = c.task.kind
        if kind in {"remember.extract", "remember.distill"}:
            result = await asyncio.to_thread(
                self.owner.commit_extraction, c.context, c.task, items, data
            )
            if result is None:
                return self.done("prepared", "generate")
        elif kind == "remember.compress":
            result = await asyncio.to_thread(
                self.owner.commit_compression, c.context, c.task, items[0], data
            )
        elif kind == "remember.project":
            result = await self.owner.commit_projection(c.context, c.task, items[0], data)
        elif kind == "remember.cleanup":
            result = await asyncio.to_thread(self.owner.commit_cleanup, c.context, c.task)
        else:
            result = await self.owner.revalidate_sources(c.context, c.task, items[0])
        return self.outcome(result)
