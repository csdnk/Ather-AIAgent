"""One immutable event/consumer pair per Workflow; Inbox owns business deduplication."""

from functools import partial
from typing import Any

from aether_agent_memory.runtime.contracts.models import (
    EffectStatus,
    ErrorCode,
    EventEnvelope,
    Flow,
    Permission,
    RecordRef,
    TaskSpec,
    TrustedContext,
)
from aether_agent_memory.runtime.foundation.common import fingerprint, later
from aether_agent_memory.runtime.foundation.events import Events
from aether_agent_memory.runtime.foundation.storage import SQLiteTransaction

from .activities import StageContext
from .ingress import WorkflowOnlyHandler
from .ledger import ExecutionLedger
from .models import StepRequest, StepResult, WorkflowInput
from .registry import StageRegistry
from .workflows import EventDeliveryWorkflow as EventDeliveryWorkflow


class EventAdmission:
    def __init__(
        self, ledger: ExecutionLedger, events: Events, *, delivery_seconds: float = 86400
    ) -> None:
        if delivery_seconds <= 0:
            raise ValueError("event delivery deadline must be positive")
        self.ledger, self.events, self.delivery_seconds = ledger, events, delivery_seconds
        ledger.tasks.register(
            "runtime.event_delivery",
            "engineering",
            WorkflowOnlyHandler(),
            permission=Permission.READ,
        )
        if events.on_delivery is not None:
            raise ValueError("event scheduler already registered")
        events.on_delivery = self.on_delivery

    def on_delivery(
        self, tx: SQLiteTransaction, ctx: TrustedContext, event: EventEnvelope, consumer_id: str
    ) -> None:
        self.admit(tx, ctx, event, consumer_id)

    def admit(
        self, tx: SQLiteTransaction, ctx: TrustedContext, event: EventEnvelope, consumer_id: str
    ) -> WorkflowInput:
        key = fingerprint([consumer_id, event.event_id])
        subject = RecordRef(
            owner=Flow.RUNTIME,
            object_type="event_delivery",
            object_id=key,
            # Delivery metadata belongs to its initiator. A grant on the source
            # memory does not grant access to the owner's runtime resources.
            # EventStages.checked separately reauthorizes the original subject.
            scope=ctx.principal.home_scope,
        )
        input_ref = subject.model_copy(update={"object_type": "event_delivery_input"})
        value: dict[str, Any] = {
            "event_id": event.event_id,
            "consumer_id": consumer_id,
            "signature": fingerprint(event.model_dump(mode="json")),
        }
        previous = tx.get(input_ref)
        if previous is None:
            tx.put_if_revision(input_ref, value, None)
        elif previous != value:
            tx.abort(ErrorCode.IDEMPOTENCY_CONFLICT, "event delivery input changed")
        if tx.read("tasks", key) is not None:
            _, task = self.ledger.tasks.load(tx, key)
            if (
                task.kind != "runtime.event_delivery"
                or task.subject != subject
                or task.input_ref != input_ref
                or task.input_hash != fingerprint(value)
                or task.initiator_id != ctx.principal.principal_id
                or task.initiator_auth_epoch != ctx.principal.auth_epoch
            ):
                tx.abort(ErrorCode.IDEMPOTENCY_CONFLICT, "original event admission changed")
            self.events.identity.authorize(tx, ctx, Permission.READ, subject)
            tx.before_commit.append(lambda: self.events.identity.revalidate(tx, ctx))
            # An offline-migrated delivery retains its original deadline and budget.
            return self.ledger.bind_admitted(tx, task)
        ctx = ctx.model_copy(
            update={
                "operation_id": event.event_id,
                "deadline_at": later(event.occurred_at, self.delivery_seconds),
            }
        )
        return self.ledger.admit(
            tx,
            ctx,
            TaskSpec(
                task_id=key,
                owner_flow=Flow.RUNTIME,
                kind="runtime.event_delivery",
                subject=subject,
                input_ref=input_ref,
                idempotency_key=key,
                input_hash=fingerprint(value),
                initiator_id=ctx.principal.principal_id,
                initiator_auth_epoch=ctx.principal.auth_epoch,
                deadline_at=ctx.deadline_at,
                max_attempts=min(self.events.max_attempts, self.ledger.tasks.max_attempts),
            ),
        )


class EventStages:
    def __init__(self, events: Events) -> None:
        self.events = events

    def checked(self, tx: SQLiteTransaction) -> tuple[str, dict[str, Any], EventEnvelope, str]:
        c = StageContext.current()
        c.guard(tx)
        value = tx.get(c.task.input_ref)
        if value is None:
            tx.abort(ErrorCode.CONTRACT_VIOLATION, "delivery input missing")
        event_id, consumer_id = str(value["event_id"]), str(value["consumer_id"])
        key = fingerprint([consumer_id, event_id])
        row, original = tx.read("deliveries", key), tx.read("outbox", event_id)
        if key != c.task.task_id or row is None or original is None:
            tx.abort(ErrorCode.CONTRACT_VIOLATION, "delivery binding missing")
        event = self.events.validate(EventEnvelope.model_validate(original["event"]))
        if (
            row["event_id"] != event_id
            or row["consumer_id"] != consumer_id
            or original["signature"] != value["signature"]
            or fingerprint(event.model_dump(mode="json")) != value["signature"]
        ):
            tx.abort(ErrorCode.IDEMPOTENCY_CONFLICT, "delivery event fingerprint changed")
        if row.get("lease") is not None:
            tx.abort(ErrorCode.VERSION_CONFLICT, "historical delivery still has an RF lease")
        self.events.identity.authorize(
            tx, c.context, self.events.permissions[event.event_type], event.subject
        )
        return key, row, event, consumer_id

    def receipt(
        self, tx: SQLiteTransaction, key: str, event: EventEnvelope, consumer_id: str
    ) -> RecordRef:
        value = tx.read("inbox", key)
        if not value or value["signature"] != fingerprint(event.model_dump(mode="json")):
            tx.abort(ErrorCode.IDEMPOTENCY_CONFLICT, "Inbox receipt missing or changed")
        ref = StageContext.current().task.subject.model_copy(
            update={"object_type": "event_delivery_receipt"}
        )
        expected = {
            "event_id": event.event_id,
            "consumer_id": consumer_id,
            "signature": value["signature"],
        }
        prior = tx.get(ref)
        if prior is None:
            tx.put_if_revision(ref, expected, None)
        elif prior != expected:
            tx.abort(ErrorCode.IDEMPOTENCY_CONFLICT, "delivery receipt changed")
        return ref

    async def deliver_event(self, step: StepRequest) -> StepResult:
        return await StageContext.current().blocking(partial(self.deliver_in, step))

    def deliver_in(self, step: StepRequest) -> StepResult:
        c = StageContext.current()
        with self.events.uow.transaction() as tx:
            key, row, event, consumer_id = self.checked(tx)
            inbox = tx.read("inbox", key)
            if inbox is None:
                callback = self.events.consumers.get((event.event_type, consumer_id))
                if callback is None:
                    tx.abort(ErrorCode.CONTRACT_VIOLATION, "event subscriber unavailable")
                tx.write(
                    "deliveries",
                    key,
                    {
                        **row,
                        "state": "sent",
                        "revision": row["revision"] + 1,
                        "attempt": c.task.attempt,
                        "execution": c.execution.model_dump(mode="json"),
                    },
                )
                self.events.consume(tx, consumer_id, event, callback)
            ref = self.receipt(tx, key, event, consumer_id)
        return StepResult(
            outcome="done",
            result_ref=ref,
            next_stage="acknowledge",
            effect_status=EffectStatus.CONFIRMED,
            reason_code="CONSUMER_COMMITTED",
        )

    async def acknowledge(self, step: StepRequest) -> StepResult:
        c = StageContext.current()
        with self.events.uow.transaction() as tx:
            key, row, event, consumer_id = self.checked(tx)
            ref = self.receipt(tx, key, event, consumer_id)
            if row["state"] != "acknowledged":
                tx.write(
                    "deliveries",
                    key,
                    {
                        **row,
                        "state": "acknowledged",
                        "revision": row["revision"] + 1,
                        "acknowledged_at": self.events.clock(),
                        "lease": None,
                        "error_code": None,
                        "execution": c.execution.model_dump(mode="json"),
                    },
                )
            c.ledger.complete(tx, c.task.task_id, c.execution, ref)
        return StepResult(
            outcome="done",
            result_ref=ref,
            effect_status=EffectStatus.CONFIRMED,
            reason_code="DELIVERY_ACKNOWLEDGED",
        )


def register_events(
    registry: StageRegistry, ledger: ExecutionLedger, events: Events
) -> EventAdmission:
    admission, stages = EventAdmission(ledger, events), EventStages(events)
    registry.register(
        "runtime.event_delivery",
        "deliver",
        stages.deliver_event,
        stages.deliver_event,
        Permission.READ,
        "idempotent",
    )
    registry.register(
        "runtime.event_delivery",
        "acknowledge",
        stages.acknowledge,
        stages.acknowledge,
        Permission.READ,
        "idempotent",
    )
    return admission
