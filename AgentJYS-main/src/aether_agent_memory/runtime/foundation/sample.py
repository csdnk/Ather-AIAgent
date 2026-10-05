"""Engineering-only handler: proves the shared mechanism, not Remember business."""

from __future__ import annotations

from pydantic import JsonValue

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
from aether_agent_memory.runtime.foundation.transactions import native
from aether_agent_memory.runtime.storage.ports import MetadataUnitOfWork

from .common import fingerprint
from .events import Events
from .identity import Identity
from .tasks import Tasks
from .telemetry import observed


@observed("engineering.sample")
class Sample:
    KIND = "engineering.save"
    EVENT = "engineering.saved"

    def __init__(
        self, uow: MetadataUnitOfWork, identity: Identity, tasks: Tasks, events: Events
    ) -> None:
        self.uow, self.identity, self.tasks, self.events = uow, identity, tasks, events
        tasks.register(self.KIND, "engineering", self)
        events.register_type(self.EVENT, self.validate_event)
        events.subscribe(self.EVENT, "engineering_receipt", self.consume)

    @staticmethod
    def validate_event(event: EventEnvelope) -> None:
        if (
            event.producer != Flow.RUNTIME
            or event.subject.owner != Flow.RUNTIME
            or event.subject.object_type != "engineering_input"
            or event.payload != {"input_id": event.subject.object_id, "mode": "engineering_only"}
        ):
            raise ValueError("invalid engineering event binding")

    @staticmethod
    def consume(tx: Transaction, event: EventEnvelope) -> None:
        sql = native(tx)
        ref = RecordRef(
            owner=Flow.RUNTIME,
            object_type="engineering_receipt",
            object_id=event.subject.object_id,
            scope=event.subject.scope,
        )
        prior = sql.get(ref)
        count = 0 if prior is None else prior["applications"]
        if not isinstance(count, int):
            sql.abort(ErrorCode.CONTRACT_VIOLATION, "invalid receipt counter")
        sql.put_if_revision(
            ref, {"applications": count + 1, "event_id": event.event_id}, sql.revision(ref)
        )

    def submit(self, ctx: TrustedContext, key: str, text: str) -> TaskRecord:
        if not key or len(key) > 128 or not text or len(text.encode()) > 65536:
            raise ValueError("key required (<=128 characters); text must be 1..65536 bytes")
        task_id = fingerprint(
            [ctx.principal.principal_id, ctx.principal.home_scope.model_dump(mode="json"), key]
        )
        ref = RecordRef(
            owner=Flow.RUNTIME,
            object_type="engineering_input",
            object_id=task_id,
            scope=ctx.principal.home_scope,
        )
        content: dict[str, JsonValue] = {"text": text, "mode": "engineering_only"}
        with self.uow.transaction() as tx:
            self.identity.authorize(tx, ctx, Permission.WRITE, ref)
            old = tx.get(ref)
            if old is not None:
                if old != content:
                    tx.abort(ErrorCode.IDEMPOTENCY_CONFLICT, "same key with different input")
                return self.tasks.load(tx, task_id)[1]
            tx.put_if_revision(ref, content, None)
            spec = TaskSpec(
                task_id=task_id,
                owner_flow=Flow.RUNTIME,
                kind=self.KIND,
                subject=ref,
                input_ref=ref,
                idempotency_key=task_id,
                input_hash=fingerprint(content),
                initiator_id=ctx.principal.principal_id,
                initiator_auth_epoch=ctx.principal.auth_epoch,
                deadline_at=ctx.deadline_at,
                max_attempts=self.tasks.max_attempts,
            )
            task = self.tasks.enqueue(tx, ctx, spec)
            payload = {"input_id": task_id, "mode": "engineering_only"}
            event = EventEnvelope(
                event_id=task_id,
                event_type=self.EVENT,
                producer=Flow.RUNTIME,
                subject=ref,
                subject_revision=1,
                occurred_at=self.tasks.clock(),
                request_id=ctx.request_id,
                trace_id=ctx.trace_id,
                causation_id=ctx.operation_id,
                initiator_id=ctx.principal.principal_id,
                initiator_auth_epoch=ctx.principal.auth_epoch,
                payload=payload,
                payload_hash=fingerprint(payload),
            )
            self.events.append(tx, ctx, event)
            return task

    async def run(self, ctx: TrustedContext, task: TaskRecord) -> RunResult:
        result = RecordRef(
            owner=Flow.RUNTIME,
            object_type="engineering_result",
            object_id=task.task_id,
            scope=task.subject.scope,
        )
        with self.uow.transaction() as tx:
            self.tasks.guard(tx, task)
            self.identity.authorize(tx, ctx, Permission.READ, task.input_ref)
            content = tx.get(task.input_ref)
            if content is None or fingerprint(content) != task.input_hash:
                return RunResult(
                    outcome="obsolete", effect_status=EffectStatus.NO_EFFECT, reason="input changed"
                )
            # This handler's only effect is the transaction below. No network calls.
            tx.put_if_revision(
                result, {"input_hash": task.input_hash, "mode": "engineering_only"}, None
            )
            self.tasks.complete(tx, ctx, task, result)
        return RunResult(
            outcome="committed",
            effect_status=EffectStatus.CONFIRMED,
            result_ref=result,
            reason="engineering result committed",
        )

    async def recover(self, ctx: TrustedContext, task: TaskRecord) -> RecoveryDecision:
        result = RecordRef(
            owner=Flow.RUNTIME,
            object_type="engineering_result",
            object_id=task.task_id,
            scope=task.subject.scope,
        )
        with self.uow.transaction() as tx:
            self.tasks.guard(tx, task)
            self.identity.authorize(tx, ctx, Permission.READ, task.input_ref)
            if tx.get(result) is not None:
                # A result without successful task is impossible for this handler.
                return RecoveryDecision(
                    action=RecoveryAction.ATTENTION,
                    effect_status=EffectStatus.UNKNOWN,
                    reason="inconsistent sample result requires investigation",
                    evidence=(result,),
                )
            return RecoveryDecision(
                action=RecoveryAction.RESUME,
                effect_status=EffectStatus.NO_EFFECT,
                reason="local-only atomic handler; no committed result exists",
                evidence=(task.input_ref,),
            )
