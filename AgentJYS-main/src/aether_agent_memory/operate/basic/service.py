"""Level-based internal controller: events schedule inspections of current state."""

from __future__ import annotations

import asyncio
from typing import Any

from pydantic import JsonValue

from aether_agent_memory.operate.contracts.models import (
    ActionChanged,
    ActionIntent,
    ActionRecord,
    ActionState,
    ExecutionFeedback,
    PlacementDecision,
    PlacementObservation,
    SchedulingInput,
    Tier,
)
from aether_agent_memory.recall.contracts.models import AccessObserved
from aether_agent_memory.remember.contracts.models import MemoryRef, StorageChanged
from aether_agent_memory.remember.contracts.ports import MemoryReadPort, RememberPort
from aether_agent_memory.runtime.contracts.models import (
    EffectStatus,
    ErrorCode,
    EventEnvelope,
    Flow,
    Permission,
    RecordRef,
    RecoveryAction,
    RecoveryDecision,
    RunResult,
    TaskRecord,
    TaskSpec,
    TrustedContext,
)
from aether_agent_memory.runtime.contracts.ports import Transaction
from aether_agent_memory.runtime.foundation.common import FoundationError, fingerprint
from aether_agent_memory.runtime.foundation.events import Events
from aether_agent_memory.runtime.foundation.identity import Identity
from aether_agent_memory.runtime.foundation.requests import event_context
from aether_agent_memory.runtime.foundation.tasks import Tasks
from aether_agent_memory.runtime.foundation.telemetry import observed
from aether_agent_memory.runtime.foundation.transactions import native
from aether_agent_memory.runtime.storage.ports import MetadataTransaction, MetadataUnitOfWork

from .cache_port import CacheCapacityError, CacheExecutor
from .record_sync import MemoryRecordSync


@observed("operate")
class Operate:
    def __init__(
        self,
        uow: MetadataUnitOfWork,
        identity: Identity,
        tasks: Tasks,
        events: Events,
        remember: RememberPort,
        memories: MemoryReadPort,
        executor: CacheExecutor,
    ) -> None:
        self.uow, self.identity, self.tasks, self.events = uow, identity, tasks, events
        self.remember, self.memories, self.executor = remember, memories, executor
        self.record_sync = MemoryRecordSync(executor)
        self.paused = False
        tasks.register(
            "operate.evaluate",
            "operate",
            self,
            permission=Permission.READ,
            attempt_limit=self.evaluation_attempt_budget(cleanup=False),
        )
        events.register_type(
            "operate.action_changed", self.validate_event, permission=Permission.READ
        )
        events.subscribe("memory.changed", "operate", self.consume)
        events.subscribe("recall.access", "operate", self.consume)

    @staticmethod
    def validate_event(event: EventEnvelope) -> None:
        payload = ActionChanged.model_validate(event.payload)
        if event.producer != Flow.OPERATE or event.subject != RecordRef(
            owner=Flow.OPERATE,
            object_type="action",
            object_id=payload.action_id,
            scope=payload.memory.scope,
        ):
            raise ValueError("action event binding mismatch")

    def should_evaluate(self, *, cleanup: bool) -> bool:
        """Skip impossible placement work; lifecycle cleanup keeps its own authority."""
        return cleanup or getattr(self.executor, "supported_moves", None) != ()

    def consume(self, tx: Transaction, event: EventEnvelope) -> None:
        sql = native(tx)
        ctx = event_context(sql, event, self.identity.clock())
        if event.event_type == "recall.access":
            access = AccessObserved.model_validate(event.payload)
            if access.stage != "read" or access.outcome != "succeeded":
                return
            memory = access.memory
            if (
                self.memories.final_guard(tx, ctx, (memory,), "actuate").items[0].decision
                != "allowed"
            ):
                return
            change = None
        else:
            change = StorageChanged.model_validate(event.payload)
            memory = change.memory
            if (
                change.status == "active"
                and self.memories.final_guard(tx, ctx, (memory,), "actuate").items[0].decision
                != "allowed"
            ):
                return
            if (
                self.memories.final_guard(tx, ctx, (memory,), "cleanup").items[0].decision
                != "allowed"
            ):
                return
        key = fingerprint([memory.scope.model_dump(mode="json"), memory.memory_id])
        view = sql.read("operate_views", key) or {
            "memory": memory.model_dump(mode="json"),
            "storage_watermark": 0,
            "access_watermark": 0,
            "successful_reads": 0,
        }
        if view["memory"]["version"] > memory.version:
            # Old lifecycle events may still need cleanup, but cannot revert current state.
            if change and change.status != "active":
                self.enqueue(
                    sql,
                    ctx,
                    memory,
                    event.event_id,
                    cleanup=True,
                    permanent=change.status == "deleted",
                    trigger_kind=event.event_type,
                )
            return
        if view["memory"]["version"] < memory.version:
            view = {
                "memory": memory.model_dump(mode="json"),
                "storage_watermark": 0,
                "access_watermark": 0,
                "successful_reads": 0,
            }
        if change:
            if change.object_revision < view["storage_watermark"]:
                return
            view["storage_watermark"] = change.object_revision
            view["cleanup"] = change.status != "active"
            view["permanent"] = change.status == "deleted"
            view["cleanup_completed"] = False
            view["scheduler_event"] = event.model_dump(mode="json")
        else:
            seen_key = fingerprint([key, access.access_key])
            if sql.read("operate_access_keys", seen_key):
                return
            sql.write("operate_access_keys", seen_key, True)
            view["access_watermark"] += 1
            view["successful_reads"] += 1
            if self.identity.permits(
                sql,
                ctx,
                Permission.READ,
                RecordRef(
                    owner=Flow.OPERATE,
                    object_type="evaluation",
                    object_id=memory.memory_id,
                    scope=memory.scope,
                ),
            ):
                # An authorized owner read refreshes the scheduling epoch. A grant
                # limited to Remember resources cannot confer Operate authority.
                view["scheduler_event"] = event.model_dump(mode="json")
        view["event"] = event.model_dump(mode="json")
        sql.write("operate_views", key, view)
        scheduling_event = view.get("scheduler_event")
        if scheduling_event is None and not change:
            # A shared reader does not acquire authority over the owner's scheduler.
            # Preserve the input; the durable storage event establishes its work context.
            return
        if scheduling_event is not None:
            ctx = event_context(
                sql, EventEnvelope.model_validate(scheduling_event), self.identity.clock()
            )
        self.schedule_evaluation(
            sql,
            ctx,
            memory,
            event.event_id,
            cleanup=view.get("cleanup", False),
            permanent=view.get("permanent", False),
            trigger_kind=event.event_type,
        )

    def schedule_evaluation(
        self,
        tx: MetadataTransaction,
        ctx: TrustedContext,
        memory: MemoryRef,
        trigger: str,
        *,
        cleanup: bool,
        permanent: bool,
        trigger_kind: str,
    ) -> None:
        if self.should_evaluate(cleanup=cleanup):
            self.enqueue(
                tx,
                ctx,
                memory,
                trigger,
                cleanup=cleanup,
                permanent=permanent,
                trigger_kind=trigger_kind,
            )

    def evaluation_attempt_budget(self, *, cleanup: bool) -> int:
        return self.tasks.max_attempts

    def enqueue(
        self,
        tx: MetadataTransaction,
        ctx: TrustedContext,
        memory: MemoryRef,
        trigger: str,
        *,
        cleanup: bool,
        permanent: bool,
        trigger_kind: str = "unrecorded",
    ) -> str:
        task_id = fingerprint(["operate", trigger, memory.model_dump(mode="json")])
        ref = RecordRef(
            owner=Flow.OPERATE, object_type="evaluation", object_id=task_id, scope=memory.scope
        )
        content: dict[str, JsonValue] = {
            "memory": memory.model_dump(mode="json"),
            "cleanup": cleanup,
            "permanent": permanent,
        }
        if tx.get(ref) is None:
            tx.put_if_revision(ref, content, None)
        previous = tx.read("tasks", task_id)
        # Preserve the signature and execution budget when replaying pre-upgrade inputs.
        attempt_budget = (
            previous["record"]["max_attempts"]
            if previous
            else self.evaluation_attempt_budget(cleanup=cleanup)
        )
        self.tasks.enqueue(
            tx,
            ctx,
            TaskSpec(
                task_id=task_id,
                owner_flow=Flow.OPERATE,
                kind="operate.evaluate",
                subject=ref,
                input_ref=ref,
                idempotency_key=task_id,
                input_hash=fingerprint(content),
                initiator_id=ctx.principal.principal_id,
                initiator_auth_epoch=ctx.principal.auth_epoch,
                deadline_at=ctx.deadline_at,
                max_attempts=attempt_budget,
            ),
        )
        if tx.read("operate_evaluation_triggers", task_id) is None:
            tx.write("operate_evaluation_triggers", task_id, {"kind": trigger_kind})
        return task_id

    def decide(self, ctx: TrustedContext, inputs: SchedulingInput) -> PlacementDecision:
        target, outcome = inputs.current_tier, "keep"
        reason = "current placement satisfies basic cache policy"
        if self.paused or inputs.coverage != "complete":
            outcome, reason = "defer", "new actions paused or observation incomplete"
        elif inputs.current_tier == Tier.WARM:
            outcome, reason = "defer", "legacy warm placement requires verified observation"
        elif inputs.current_tier == Tier.COLD and inputs.successful_reads >= 1:
            if inputs.available_bytes < inputs.content_bytes:
                outcome, reason = "defer", "copy capacity unavailable"
            else:
                target, outcome, reason = Tier.HOT, "promote", "successful read creates hot replica"
        elif inputs.current_tier == Tier.HOT and inputs.successful_reads == 0:
            target, outcome, reason = Tier.COLD, "demote", "no reads; remove hot replica"
        return PlacementDecision(
            decision_id=fingerprint(inputs.model_dump(mode="json")),
            memory=inputs.memory,
            outcome=outcome,
            current_tier=inputs.current_tier,
            target_tier=target,
            reason=reason,
            policy_version="ceph_redis_basic_v2",
            storage_watermark=inputs.storage_watermark,
            access_watermark=inputs.access_watermark,
        )

    def save_action(
        self, tx: MetadataTransaction, ctx: TrustedContext, action: ActionRecord
    ) -> None:
        tx.write("operate_actions", action.intent.action_id, action.model_dump(mode="json"))
        payload = ActionChanged(
            action_id=action.intent.action_id,
            memory=action.intent.decision.memory,
            state=action.state,
            provider_mode=action.intent.provider_mode,
            observed_epoch=None
            if action.feedback is None or action.feedback.observation is None
            else action.feedback.observation.epoch,
            reason=action.reason or action.state.value,
        )
        self.events.append(
            tx,
            ctx,
            EventEnvelope(
                event_id=fingerprint([action.intent.action_id, action.revision]),
                event_type="operate.action_changed",
                producer=Flow.OPERATE,
                subject=RecordRef(
                    owner=Flow.OPERATE,
                    object_type="action",
                    object_id=action.intent.action_id,
                    scope=action.intent.decision.memory.scope,
                ),
                subject_revision=action.revision,
                occurred_at=self.identity.clock(),
                request_id=ctx.request_id,
                trace_id=ctx.trace_id,
                initiator_id=ctx.principal.principal_id,
                initiator_auth_epoch=ctx.principal.auth_epoch,
                payload=payload.model_dump(mode="json"),
                payload_hash=fingerprint(payload.model_dump(mode="json")),
            ),
        )

    async def execute(self, ctx: TrustedContext, intent: ActionIntent) -> ActionRecord:
        prepared = await asyncio.to_thread(self.prepare_submission, ctx, intent)
        if isinstance(prepared, ActionRecord):
            return prepared
        submit = prepared
        if not submit:
            return await self.reconcile(ctx, intent.action_id)
        try:
            feedback = await self.executor.submit(ctx, intent)
        except Exception:
            feedback = ExecutionFeedback(
                action_id=intent.action_id,
                provider_instance_id=intent.provider_instance_id,
                state="unknown",
                observed_at=self.identity.clock(),
                reason="execution response unavailable",
            )
        return await self.accept_feedback(ctx, intent, feedback)

    def prepare_submission(self, ctx: TrustedContext, intent: ActionIntent) -> ActionRecord | bool:
        with self.uow.transaction() as tx:
            self.identity.authorize(
                tx,
                ctx,
                Permission.READ,
                RecordRef(
                    owner=Flow.OPERATE,
                    object_type="action",
                    object_id=intent.action_id,
                    scope=intent.decision.memory.scope,
                ),
            )
            previous = tx.read("operate_actions", intent.action_id)
            if previous:
                old = ActionRecord.model_validate(previous)
                if old.intent != intent:
                    tx.abort(ErrorCode.IDEMPOTENCY_CONFLICT, "action ID cannot change intent")
                if old.state in {ActionState.SUCCEEDED, ActionState.FAILED, ActionState.CANCELLED}:
                    return old
            submit = previous is None or old.state == ActionState.GENERATED
            if submit:
                eligible = self.memories.final_guard(tx, ctx, (intent.decision.memory,), "actuate")
                if eligible.items[0].decision != "allowed":
                    # Only an unsubmitted intent can be cancelled from eligibility
                    # alone. Submitted/unknown actions still query their original ID.
                    cancelled = ActionRecord(
                        intent=intent,
                        state=ActionState.CANCELLED,
                        revision=old.revision + 1 if previous else 1,
                        cleanup_state="not_required",
                        reason="memory invalidated before provider submission",
                    )
                    self.save_action(tx, ctx, cancelled)
                    return cancelled
                self.record_sync.reserve(tx, intent)
                action = ActionRecord(
                    intent=intent,
                    state=ActionState.SUBMITTED,
                    revision=old.revision + 1 if previous else 1,
                    cleanup_state="pending",
                    reason="intent durable before submission",
                )
                self.save_action(tx, ctx, action)
        return submit

    async def accept_feedback(
        self, ctx: TrustedContext, intent: ActionIntent, feedback: ExecutionFeedback
    ) -> ActionRecord:
        prepared = await asyncio.to_thread(self.prepare_feedback, ctx, intent)
        if isinstance(prepared, ActionRecord):
            return prepared
        evidence = prepared
        valid_binding = (
            feedback.action_id == intent.action_id
            and feedback.provider_instance_id == intent.provider_instance_id
        )
        state = ActionState.UNKNOWN
        if valid_binding and feedback.state == "succeeded":
            try:
                proof = await self.executor.verify_read(ctx, intent)
                observation = await self.executor.observe(
                    ctx, intent.decision.memory, intent.representation_id
                )
                feedback = feedback.model_copy(
                    update={"read_proof": proof, "observation": observation}
                )
                candidate = ActionRecord(
                    intent=intent,
                    state=ActionState.SUCCEEDED,
                    revision=1,
                    feedback=feedback,
                    cleanup_state="completed",
                )
                state = candidate.state
            except Exception:
                state = ActionState.UNKNOWN
        elif valid_binding and feedback.state == "failed":
            state = ActionState.FAILED
        try:
            return await asyncio.to_thread(
                self.commit_feedback, ctx, intent, feedback, state, evidence
            )
        except Exception:
            if state != ActionState.SUCCEEDED:
                raise
            # Redis may already be changed. Roll back the entire metadata/action
            # conclusion, persist UNKNOWN and recover this exact original ID.
            return await asyncio.to_thread(
                self.commit_feedback,
                ctx,
                intent,
                feedback.model_copy(update={"reason": "memory_record_sync_pending"}),
                ActionState.UNKNOWN,
                None,
            )

    def prepare_feedback(
        self, ctx: TrustedContext, intent: ActionIntent
    ) -> ActionRecord | dict[str, Any] | None:
        with self.uow.transaction() as tx:
            self.authorize_action(tx, ctx, intent)
            previous = ActionRecord.model_validate(tx.read("operate_actions", intent.action_id))
            if previous.intent != intent:
                tx.abort(ErrorCode.IDEMPOTENCY_CONFLICT, "feedback intent changed")
            # A delayed callback must not re-open a terminal action or overwrite
            # the metadata published by a later cooling/promotion.
            if previous.state in {ActionState.SUCCEEDED, ActionState.FAILED, ActionState.CANCELLED}:
                return previous
            self.record_sync.reserve(tx, intent)
            return self.record_sync.evidence(tx, intent.decision.memory)

    def capture_evidence(self, memory: MemoryRef) -> dict[str, Any] | None:
        with self.uow.transaction() as tx:
            return self.record_sync.evidence(tx, memory)

    def authorize_action(
        self, tx: MetadataTransaction, ctx: TrustedContext, intent: ActionIntent
    ) -> None:
        self.identity.authorize(
            tx,
            ctx,
            Permission.READ,
            RecordRef(
                owner=Flow.OPERATE,
                object_type="action",
                object_id=intent.action_id,
                scope=intent.decision.memory.scope,
            ),
        )

    def commit_feedback(
        self,
        ctx: TrustedContext,
        intent: ActionIntent,
        feedback: ExecutionFeedback,
        state: ActionState,
        evidence: dict[str, Any] | None,
    ) -> ActionRecord:
        with self.uow.transaction() as tx:
            self.authorize_action(tx, ctx, intent)
            previous = ActionRecord.model_validate(tx.read("operate_actions", intent.action_id))
            if previous.intent != intent:
                tx.abort(ErrorCode.IDEMPOTENCY_CONFLICT, "feedback intent changed")
            if previous.state in {ActionState.SUCCEEDED, ActionState.FAILED, ActionState.CANCELLED}:
                return previous
            if (
                self.memories.final_guard(tx, ctx, (intent.decision.memory,), "actuate")
                .items[0]
                .decision
                != "allowed"
                and feedback.action_id == intent.action_id
                and feedback.provider_instance_id == intent.provider_instance_id
                and feedback.state in {"succeeded", "failed"}
            ):
                state = ActionState.CANCELLED
                feedback = feedback.model_copy(update={"reason": "memory_no_longer_schedulable"})
            if state == ActionState.SUCCEEDED:
                assert feedback.observation is not None
                synced = self.record_sync.publish(tx, feedback.observation, evidence, action=intent)
                if synced == "superseded":
                    state = ActionState.CANCELLED
                    feedback = feedback.model_copy(update={"reason": "memory_record_superseded"})
            action = ActionRecord(
                intent=intent,
                state=state,
                revision=previous.revision + 1,
                feedback=feedback,
                cleanup_state="completed"
                if state == ActionState.SUCCEEDED
                else "unknown"
                if state == ActionState.UNKNOWN
                else "pending",
                reason=feedback.reason or state.value,
            )
            self.record_sync.conclude(tx, intent, state)
            self.save_action(tx, ctx, action)
            return action

    def sync_observation(
        self,
        ctx: TrustedContext,
        observation: PlacementObservation,
        evidence: dict[str, Any] | None,
    ) -> str:
        with self.uow.transaction() as tx:
            if (
                self.memories.final_guard(tx, ctx, (observation.memory,), "actuate")
                .items[0]
                .decision
                != "allowed"
            ):
                return "ineligible"
            return self.record_sync.publish(tx, observation, evidence)

    def load_action(self, ctx: TrustedContext, action_id: str) -> ActionRecord:
        with self.uow.transaction() as tx:
            raw = tx.read("operate_actions", action_id)
            if raw is None:
                raise FoundationError(ErrorCode.NOT_FOUND, "action not found")
            action = ActionRecord.model_validate(raw)
            self.identity.authorize(
                tx,
                ctx,
                Permission.READ,
                RecordRef(
                    owner=Flow.OPERATE,
                    object_type="action",
                    object_id=action_id,
                    scope=action.intent.decision.memory.scope,
                ),
            )
        return action

    async def reconcile(self, ctx: TrustedContext, action_id: str) -> ActionRecord:
        action = await asyncio.to_thread(self.load_action, ctx, action_id)
        if action.state in {ActionState.SUCCEEDED, ActionState.FAILED, ActionState.CANCELLED}:
            return action
        try:
            feedback = await self.executor.query(ctx, action_id)
        except Exception:
            feedback = ExecutionFeedback(
                action_id=action_id,
                provider_instance_id=action.intent.provider_instance_id,
                state="unknown",
                observed_at=self.identity.clock(),
                reason="query unavailable",
            )
        return await self.accept_feedback(ctx, action.intent, feedback)

    def finish(
        self, tx: MetadataTransaction, ctx: TrustedContext, task: TaskRecord, value: dict[str, Any]
    ) -> RunResult:
        result = RecordRef(
            owner=Flow.OPERATE,
            object_type="evaluation_result",
            object_id=task.task_id,
            scope=task.subject.scope,
        )
        tx.put_if_revision(result, value, None)
        self.tasks.complete(tx, ctx, task, result)
        return RunResult(
            outcome="committed",
            effect_status=EffectStatus.CONFIRMED,
            result_ref=result,
            reason="controller decision/result persisted",
        )

    async def run(self, ctx: TrustedContext, task: TaskRecord) -> RunResult:
        prepared = await self.prepare_evaluation(ctx, task)
        if isinstance(prepared, RunResult):
            return prepared
        result = await self.submit_evaluation(ctx, task, prepared)
        if isinstance(result, RunResult):
            return result
        await self.before_completion(ctx, task, result)
        with self.uow.transaction() as tx:
            return self.finish(tx, ctx, task, result)

    async def before_completion(
        self, ctx: TrustedContext, task: TaskRecord, value: dict[str, Any]
    ) -> None:
        """Optional local controller bookkeeping, before atomic task completion."""

    async def submit_evaluation(
        self, ctx: TrustedContext, task: TaskRecord, prepared: dict[str, Any]
    ) -> dict[str, Any] | RunResult:
        if "value" in prepared:
            return dict(prepared["value"])
        if prepared.get("cleanup"):
            memory = MemoryRef.model_validate(prepared["memory"])
            with self.uow.transaction() as tx:
                self.tasks.guard(tx, task)
                valid = (
                    prepared["valid"]
                    and self.memories.final_guard(tx, ctx, (memory,), "actuate").items[0].decision
                    != "allowed"
                )
            if valid:
                await asyncio.to_thread(
                    self.executor.purge, memory, permanent=prepared["permanent"], ctx=ctx
                )
            if valid:
                await asyncio.to_thread(self.complete_cleanup_record, ctx, task, memory)
            return {"cache_cleanup": "completed" if valid else "ineligible"}
        intent = ActionIntent.model_validate(prepared["intent"])
        return self.evaluation_feedback(ctx, task, await self.execute(ctx, intent))

    def complete_cleanup_record(
        self, ctx: TrustedContext, task: TaskRecord, memory: MemoryRef
    ) -> None:
        with self.uow.transaction() as tx:
            self.tasks.guard(tx, task)
            if (
                self.memories.final_guard(tx, ctx, (memory,), "cleanup").items[0].decision
                != "allowed"
            ):
                tx.abort(ErrorCode.COMMIT_UNCONFIRMED, "cleanup metadata authorization changed")
            # Active status alone does not imply eligibility: expiry, replacement
            # or invalidated source can also require removal of the old pointer.
            if (
                self.memories.final_guard(tx, ctx, (memory,), "actuate").items[0].decision
                != "allowed"
            ):
                self.record_sync.clear_inactive(tx, memory)

    def evaluation_feedback(
        self, ctx: TrustedContext, task: TaskRecord, action: ActionRecord
    ) -> dict[str, Any] | RunResult:
        if action.state == ActionState.UNKNOWN:
            with self.uow.transaction() as tx:
                self.tasks.progress.defer(
                    tx,
                    ctx,
                    task,
                    "cache_executor",
                    "original_action_unknown",
                    action.intent.action_id,
                    EffectStatus.UNKNOWN,
                )
            return RunResult(
                outcome="uncertain",
                effect_status=EffectStatus.UNKNOWN,
                operation_id=action.intent.action_id,
                reason="query original action on recovery",
            )
        return {
            "action_id": action.intent.action_id,
            "action_state": action.state.value,
            "provider_mode": "real",
        }

    async def prepare_evaluation(
        self, ctx: TrustedContext, task: TaskRecord
    ) -> dict[str, Any] | RunResult:
        prepared = await asyncio.to_thread(self.prepare_evaluation_inputs, ctx, task)
        if isinstance(prepared, RunResult) or "item" not in prepared:
            return prepared
        item, memory, key, existing = (
            prepared["item"],
            prepared["memory"],
            prepared["key"],
            prepared["existing"],
        )
        if existing and existing["state"] in {"generated", "submitted", "unknown"}:
            intent = ActionRecord.model_validate(existing).intent
        else:
            try:
                if not getattr(self.executor, "policy_managed", False):
                    await asyncio.to_thread(self.executor.ensure, item, ctx)
            except CacheCapacityError:
                return {"value": {"deferred": "cache_capacity"}}
            evidence = await asyncio.to_thread(self.capture_evidence, memory)
            observation = await self.executor.observe(ctx, memory, "original")
            if observation.readable and observation.tier in {Tier.COLD, Tier.HOT}:
                await asyncio.to_thread(self.sync_observation, ctx, observation, evidence)
            resources = await self.executor.resources(ctx)
            decision = await asyncio.to_thread(
                self.evaluation_decision, ctx, item, memory, key, observation, resources
            )
            if decision.outcome in {"keep", "defer"}:
                return {"value": {"decision": decision.model_dump(mode="json")}}
            if decision.outcome not in resources.supported_moves:
                deferred = decision.model_copy(
                    update={
                        "decision_id": fingerprint(
                            [decision.decision_id, resources.model_dump(mode="json"), "unsupported"]
                        ),
                        "outcome": "defer",
                        "target_tier": decision.current_tier,
                        "reason": "unsupported_tier_transition",
                    }
                )
                return {"value": {"decision": deferred.model_dump(mode="json")}}
            intent = ActionIntent(
                action_id=fingerprint(
                    [
                        memory.model_dump(mode="json"),
                        decision.current_tier,
                        decision.target_tier,
                        resources.epoch,
                        task.task_id,
                    ]
                ),
                decision=decision,
                representation_id="original",
                content_hash=item.content_hash,
                provider_id=self.executor.provider_id,
                provider_instance_id=self.executor.instance_id,
                expected_epoch=resources.epoch,
                provider_mode="real",
                created_at=self.identity.clock(),
            )
        return await asyncio.to_thread(self.reserve_evaluation, ctx, task, key, intent)

    def prepare_evaluation_inputs(
        self, ctx: TrustedContext, task: TaskRecord
    ) -> dict[str, Any] | RunResult:
        with self.uow.transaction() as tx:
            self.tasks.guard(tx, task)
            inputs = tx.get(task.input_ref)
            if (
                inputs is None
                or not isinstance(inputs.get("cleanup"), bool)
                or not isinstance(inputs.get("permanent"), bool)
            ):
                tx.abort(ErrorCode.CONTRACT_VIOLATION, "invalid evaluation input")
            memory = MemoryRef.model_validate(inputs["memory"])
            valid = (
                self.memories.final_guard(
                    tx, ctx, (memory,), "cleanup" if inputs["cleanup"] else "actuate"
                )
                .items[0]
                .decision
                == "allowed"
            )
        if inputs["cleanup"]:
            with self.uow.transaction() as tx:
                # An old archive event cannot purge a currently active same version.
                active_now = (
                    self.memories.final_guard(tx, ctx, (memory,), "actuate").items[0].decision
                    == "allowed"
                )
            valid = valid and not active_now
            return {
                "cleanup": True,
                "valid": valid,
                "memory": memory.model_dump(mode="json"),
                "permanent": inputs["permanent"],
            }
        if not valid:
            return RunResult(
                outcome="obsolete",
                effect_status=EffectStatus.NO_EFFECT,
                reason="memory not schedulable",
            )
        with self.uow.transaction() as tx:
            prior_intent = tx.read("operate_task_actions", task.task_id)
            if prior_intent:
                return {"intent": prior_intent}
        item = self.remember.get(ctx, memory.memory_id)
        if item.ref != memory:
            return RunResult(
                outcome="obsolete",
                effect_status=EffectStatus.NO_EFFECT,
                reason="memory version changed",
            )
        key = fingerprint([memory.scope.model_dump(mode="json"), memory.memory_id])
        with self.uow.transaction() as tx:
            pending = tx.read("operate_pending", key)
            existing = None if not pending else tx.read("operate_actions", pending)
        return {"item": item, "memory": memory, "key": key, "existing": existing}

    def evaluation_decision(
        self,
        ctx: TrustedContext,
        item: Any,
        memory: MemoryRef,
        key: str,
        observation: Any,
        resources: Any,
    ) -> PlacementDecision:
        with self.uow.transaction() as tx:
            view = tx.read("operate_views", key)
        return self.decide(
            ctx,
            SchedulingInput(
                memory=memory,
                object_revision=item.object_revision,
                storage_watermark=view["storage_watermark"],
                access_watermark=view["access_watermark"],
                successful_reads=view["successful_reads"],
                current_tier=observation.tier,
                importance=item.importance,
                available_bytes=resources.available_bytes,
                content_bytes=len(item.content.encode()),
                coverage="complete" if observation.readable else "unknown",
                observed_at=self.identity.clock(),
                policy_version="ceph_redis_basic_v2",
            ),
        )

    def reserve_evaluation(
        self, ctx: TrustedContext, task: TaskRecord, key: str, intent: ActionIntent
    ) -> dict[str, Any]:
        with self.uow.transaction() as tx:
            self.tasks.guard(tx, task)
            pending = tx.read("operate_pending", key)
            prior = tx.read("operate_actions", pending) if pending else None
            if prior and prior["state"] in {"generated", "submitted", "unknown"}:
                intent = ActionRecord.model_validate(prior).intent
            elif tx.read("operate_actions", intent.action_id) is None:
                # Reserve the intent in the same transaction as the task binding.
                # Other prepare Activities must see it before any submit can run.
                self.save_action(
                    tx,
                    ctx,
                    ActionRecord(
                        intent=intent,
                        state=ActionState.GENERATED,
                        revision=1,
                        cleanup_state="not_required",
                        reason="prepared; provider submission has not started",
                    ),
                )
                tx.write(
                    "operate_action_triggers",
                    intent.action_id,
                    tx.read("operate_evaluation_triggers", task.task_id) or {"kind": "unrecorded"},
                )
            tx.write("operate_task_actions", task.task_id, intent.model_dump(mode="json"))
            tx.write("operate_pending", key, intent.action_id)
        return {"intent": intent.model_dump(mode="json")}

    async def recover(self, ctx: TrustedContext, task: TaskRecord) -> RecoveryDecision:
        with self.uow.transaction() as tx:
            self.tasks.guard(tx, task)
            raw = tx.read("operate_task_actions", task.task_id)
        if raw is None:
            return RecoveryDecision(
                action=RecoveryAction.RESUME,
                effect_status=EffectStatus.NO_EFFECT,
                reason="no action intent submitted; preparation and cleanup are idempotent",
                evidence=(task.input_ref,),
            )
        intent = ActionIntent.model_validate(raw)
        with self.uow.transaction() as tx:
            persisted = tx.read("operate_actions", intent.action_id)
        if persisted is None:
            # Submission cannot occur before the action record exists.
            return RecoveryDecision(
                action=RecoveryAction.RESUME,
                effect_status=EffectStatus.NO_EFFECT,
                reason="prepared intent never submitted",
                evidence=(task.input_ref,),
            )
        action = await self.reconcile(ctx, intent.action_id)
        if action.state == ActionState.UNKNOWN:
            return RecoveryDecision(
                action=RecoveryAction.QUERY_ONLY,
                effect_status=EffectStatus.UNKNOWN,
                original_operation_id=intent.action_id,
                reason="no definitive original-action evidence",
                evidence=(task.input_ref,),
            )
        with self.uow.transaction() as tx:
            self.finish(
                tx,
                ctx,
                task,
                {
                    "action_id": intent.action_id,
                    "action_state": action.state.value,
                    "provider_mode": "real",
                },
            )
        return RecoveryDecision(
            action=RecoveryAction.QUERY_ONLY,
            effect_status=EffectStatus.CONFIRMED,
            original_operation_id=intent.action_id,
            reason="original action outcome reconciled",
            evidence=(task.input_ref,),
        )

    def periodic(self, tick_id: str) -> int:
        count = 0
        # Each object has its own transaction; one revoked identity cannot block others.
        with self.uow.transaction() as tx:
            keys = [key for key, _ in tx.rows("operate_views")]
        for key in keys:
            try:
                with self.uow.transaction() as tx:
                    view = tx.read("operate_views", key)
                    if not self.should_evaluate(cleanup=view.get("cleanup", False)):
                        continue
                    event = EventEnvelope.model_validate(view.get("scheduler_event", view["event"]))
                    ctx = event_context(tx, event, self.identity.clock())
                    self.enqueue(
                        tx,
                        ctx,
                        MemoryRef.model_validate(view["memory"]),
                        fingerprint([tick_id, key]),
                        cleanup=view.get("cleanup", False),
                        permanent=view.get("permanent", False),
                        trigger_kind="periodic",
                    )
                    count += 1
            except FoundationError:
                continue
        return count
