"""Level-based internal controller: events schedule inspections of current state."""

from __future__ import annotations

from typing import Any

from pydantic import JsonValue

from aether_agent_memory.operate.contracts.models import (
    ActionChanged,
    ActionIntent,
    ActionRecord,
    ActionState,
    ExecutionFeedback,
    PlacementDecision,
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
from aether_agent_memory.runtime.foundation.storage import (
    SQLiteTransaction,
    SQLiteUnitOfWork,
    native,
)
from aether_agent_memory.runtime.foundation.tasks import Tasks
from aether_agent_memory.runtime.foundation.telemetry import observed

from .executor import CacheCapacityError, LocalCacheExecutor


@observed("operate")
class Operate:
    def __init__(
        self,
        uow: SQLiteUnitOfWork,
        identity: Identity,
        tasks: Tasks,
        events: Events,
        remember: RememberPort,
        memories: MemoryReadPort,
        executor: LocalCacheExecutor,
    ) -> None:
        self.uow, self.identity, self.tasks, self.events = uow, identity, tasks, events
        self.remember, self.memories, self.executor = remember, memories, executor
        self.paused = False
        tasks.register("operate.evaluate", "operate", self, permission=Permission.READ)
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
        self.enqueue(
            sql,
            ctx,
            memory,
            event.event_id,
            cleanup=view.get("cleanup", False),
            permanent=view.get("permanent", False),
        )

    def enqueue(
        self,
        tx: SQLiteTransaction,
        ctx: TrustedContext,
        memory: MemoryRef,
        trigger: str,
        *,
        cleanup: bool,
        permanent: bool,
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
            ),
        )
        return task_id

    def decide(self, ctx: TrustedContext, inputs: SchedulingInput) -> PlacementDecision:
        target, outcome = inputs.current_tier, "keep"
        reason = "current placement satisfies basic cache policy"
        if self.paused or inputs.coverage != "complete":
            outcome, reason = "defer", "new actions paused or observation incomplete"
        elif inputs.available_bytes < inputs.content_bytes:
            outcome, reason = "defer", "insufficient capacity for verified copy before cleanup"
        elif inputs.current_tier == Tier.COLD:
            target, outcome, reason = Tier.WARM, "promote", "new durable memory enters warm cache"
        elif inputs.current_tier == Tier.WARM and inputs.successful_reads >= 1:
            target, outcome, reason = (
                Tier.HOT,
                "promote",
                "observed successful read promotes warm copy",
            )
        elif inputs.current_tier == Tier.HOT and inputs.successful_reads == 0:
            target, outcome, reason = Tier.WARM, "demote", "no successful reads in current version"
        return PlacementDecision(
            decision_id=fingerprint(inputs.model_dump(mode="json")),
            memory=inputs.memory,
            outcome=outcome,
            current_tier=inputs.current_tier,
            target_tier=target,
            reason=reason,
            policy_version="basic_cache_v1",
            storage_watermark=inputs.storage_watermark,
            access_watermark=inputs.access_watermark,
        )

    def save_action(self, tx: SQLiteTransaction, ctx: TrustedContext, action: ActionRecord) -> None:
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
            else:
                eligible = self.memories.final_guard(tx, ctx, (intent.decision.memory,), "actuate")
                if eligible.items[0].decision != "allowed":
                    raise FoundationError(
                        ErrorCode.RESULT_INVALIDATED, "action memory is no longer eligible"
                    )
                action = ActionRecord(
                    intent=intent,
                    state=ActionState.SUBMITTED,
                    revision=1,
                    cleanup_state="pending",
                    reason="intent durable before submission",
                )
                self.save_action(tx, ctx, action)
        if previous:
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

    async def accept_feedback(
        self, ctx: TrustedContext, intent: ActionIntent, feedback: ExecutionFeedback
    ) -> ActionRecord:
        valid_binding = (
            feedback.action_id == intent.action_id
            and feedback.provider_instance_id == intent.provider_instance_id
        )
        state = ActionState.UNKNOWN
        if valid_binding and feedback.state == "succeeded":
            try:
                proof = await self.executor.verify_read(ctx, intent)
                feedback = feedback.model_copy(update={"read_proof": proof})
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
        with self.uow.transaction() as tx:
            previous = ActionRecord.model_validate(tx.read("operate_actions", intent.action_id))
            if (
                self.memories.final_guard(tx, ctx, (intent.decision.memory,), "actuate")
                .items[0]
                .decision
                != "allowed"
                and state != ActionState.UNKNOWN
            ):
                state = ActionState.CANCELLED
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
            self.save_action(tx, ctx, action)
            return action

    async def reconcile(self, ctx: TrustedContext, action_id: str) -> ActionRecord:
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
        self, tx: SQLiteTransaction, ctx: TrustedContext, task: TaskRecord, value: dict[str, Any]
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
            if valid:
                self.executor.purge(memory, permanent=bool(inputs["permanent"]))
            with self.uow.transaction() as tx:
                return self.finish(
                    tx, ctx, task, {"cache_cleanup": "completed" if valid else "ineligible"}
                )
        if not valid:
            return RunResult(
                outcome="obsolete",
                effect_status=EffectStatus.NO_EFFECT,
                reason="memory not schedulable",
            )
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
        if existing and existing["state"] in {"generated", "submitted", "unknown"}:
            intent = ActionRecord.model_validate(existing).intent
        else:
            try:
                self.executor.ensure(item)
            except CacheCapacityError:
                with self.uow.transaction() as tx:
                    return self.finish(tx, ctx, task, {"deferred": "cache_capacity"})
            observation = await self.executor.observe(ctx, memory, "original")
            resources = await self.executor.resources(ctx)
            with self.uow.transaction() as tx:
                view = tx.read("operate_views", key)
            decision = self.decide(
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
                    policy_version="basic_cache_v1",
                ),
            )
            if decision.outcome in {"keep", "defer"}:
                with self.uow.transaction() as tx:
                    return self.finish(
                        tx, ctx, task, {"decision": decision.model_dump(mode="json")}
                    )
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
        with self.uow.transaction() as tx:
            self.tasks.guard(tx, task)
            pending = tx.read("operate_pending", key)
            prior = tx.read("operate_actions", pending) if pending else None
            if prior and prior["state"] in {"generated", "submitted", "unknown"}:
                intent = ActionRecord.model_validate(prior).intent
            tx.write("operate_task_actions", task.task_id, intent.model_dump(mode="json"))
            tx.write("operate_pending", key, intent.action_id)
        action = await self.execute(ctx, intent)
        if action.state == ActionState.UNKNOWN:
            return RunResult(
                outcome="uncertain",
                effect_status=EffectStatus.UNKNOWN,
                operation_id=intent.action_id,
                reason="query original action on recovery",
            )
        with self.uow.transaction() as tx:
            return self.finish(
                tx,
                ctx,
                task,
                {
                    "action_id": intent.action_id,
                    "action_state": action.state.value,
                    "provider_mode": "real",
                },
            )

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
                    event = EventEnvelope.model_validate(view.get("scheduler_event", view["event"]))
                    ctx = event_context(tx, event, self.identity.clock())
                    self.enqueue(
                        tx,
                        ctx,
                        MemoryRef.model_validate(view["memory"]),
                        fingerprint([tick_id, key]),
                        cleanup=view.get("cleanup", False),
                        permanent=view.get("permanent", False),
                    )
                    count += 1
            except FoundationError:
                continue
        return count
