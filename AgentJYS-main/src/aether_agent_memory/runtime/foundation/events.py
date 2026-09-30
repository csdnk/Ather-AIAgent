"""Transactional outbox, per-consumer delivery and transactional inbox.

Consumers only commit local facts or durable work in the supplied transaction;
network effects belong in tasks. Acknowledgement is a separate fenced commit.
"""

from __future__ import annotations

from collections.abc import Callable

from aether_agent_memory.runtime.contracts.models import (
    ErrorCode,
    EventEnvelope,
    Permission,
    TrustedContext,
)
from aether_agent_memory.runtime.contracts.ports import Transaction

from .common import FoundationError, fingerprint, now
from .identity import Identity
from .storage import SQLiteTransaction, SQLiteUnitOfWork, native
from .telemetry import Telemetry, observed

Consumer = Callable[[Transaction, EventEnvelope], None]


@observed("runtime.events")
class Events:
    def __init__(
        self,
        uow: SQLiteUnitOfWork,
        identity: Identity,
        *,
        clock: Callable[[], str] = now,
        lease_seconds: float = 30,
        retry_seconds: float = 1,
        max_attempts: int = 6,
    ) -> None:
        if min(lease_seconds, retry_seconds, max_attempts) <= 0:
            raise ValueError("delivery budgets must be positive")
        self.uow, self.identity, self.clock = uow, identity, clock
        self.lease_seconds, self.retry_seconds, self.max_attempts = (
            lease_seconds,
            retry_seconds,
            max_attempts,
        )
        self.validators: dict[str, Callable[[EventEnvelope], None]] = {}
        self.permissions: dict[str, Permission] = {}
        self.consumers: dict[tuple[str, str], Consumer] = {}
        self.on_delivery: (
            Callable[[SQLiteTransaction, TrustedContext, EventEnvelope, str], None] | None
        ) = None

    def register_type(
        self,
        event_type: str,
        validator: Callable[[EventEnvelope], None],
        *,
        permission: Permission = Permission.WRITE,
    ) -> None:
        if event_type in self.validators:
            raise ValueError("event type already registered")
        self.validators[event_type] = validator
        self.permissions[event_type] = permission

    def subscribe(self, event_type: str, consumer_id: str, consumer: Consumer) -> None:
        if event_type not in self.validators or (event_type, consumer_id) in self.consumers:
            raise ValueError("unknown event or duplicate consumer")
        self.consumers[(event_type, consumer_id)] = consumer

    def validate(self, event: EventEnvelope) -> EventEnvelope:
        event = EventEnvelope.model_validate_json(event.model_dump_json())
        validator = self.validators.get(event.event_type)
        if validator is None:
            raise FoundationError(ErrorCode.CONTRACT_VIOLATION, "unregistered event type")
        validator(event)
        return event

    def append(self, tx: Transaction, ctx: TrustedContext, event: EventEnvelope) -> None:
        sql = native(tx)
        backend = sql.read("meta", "execution_backend")
        if backend and backend.get("backend") == "temporal" and self.on_delivery is None:
            sql.abort(ErrorCode.CONTRACT_VIOLATION, "Temporal event admission binding is required")
        event = self.validate(event)
        self.identity.authorize(tx, ctx, self.permissions[event.event_type], event.subject)
        if (
            event.initiator_id != ctx.principal.principal_id
            or event.initiator_auth_epoch != ctx.principal.auth_epoch
            or event.request_id != ctx.request_id
            or event.trace_id != ctx.trace_id
        ):
            sql.abort(ErrorCode.CONTRACT_VIOLATION, "event does not match trusted producer context")
        signature = fingerprint(event.model_dump(mode="json"))
        existing = sql.read("outbox", event.event_id)
        if existing:
            if existing["signature"] != signature:
                sql.abort(ErrorCode.IDEMPOTENCY_CONFLICT, "event ID bound to different envelope")
            return
        sql.write(
            "outbox",
            event.event_id,
            {
                "event": event.model_dump(mode="json"),
                "signature": signature,
                "context": Telemetry.producer_context(ctx).model_dump(mode="json"),
            },
        )
        for event_type, consumer_id in self.consumers:
            if event_type == event.event_type:
                key = fingerprint([consumer_id, event.event_id])
                sql.write(
                    "deliveries",
                    key,
                    {
                        "event_id": event.event_id,
                        "consumer_id": consumer_id,
                        "state": "pending",
                        "attempt": 0,
                        "revision": 1,
                        "next_run_at": self.clock(),
                        "acknowledged_at": None,
                        "lease": None,
                        "error_code": None,
                    },
                )
                if self.on_delivery is not None:
                    self.on_delivery(sql, ctx, event, consumer_id)
        sql.before_commit.append(lambda: self.identity.revalidate(tx, ctx))

    def consume(
        self, tx: Transaction, consumer_id: str, event: EventEnvelope, apply: Consumer
    ) -> bool:
        sql = native(tx)
        event = self.validate(event)
        signature = fingerprint(event.model_dump(mode="json"))
        original = sql.read("outbox", event.event_id)
        if (
            original is None
            or original["signature"] != signature
            or (event.event_type, consumer_id) not in self.consumers
        ):
            sql.abort(ErrorCode.CONTRACT_VIOLATION, "event is not a registered durable delivery")
        key = fingerprint([consumer_id, event.event_id])
        receipt = sql.read("inbox", key)
        if receipt:
            if receipt["signature"] != signature:
                sql.abort(ErrorCode.IDEMPOTENCY_CONFLICT, "inbox event fingerprint mismatch")
            return False
        apply(tx, event)
        sql.write(
            "inbox",
            key,
            {
                "event_id": event.event_id,
                "consumer_id": consumer_id,
                "signature": signature,
                "committed_at": self.clock(),
            },
        )
        return True

    def dispatch_once(self, worker_id: str, *, lose_ack: bool = False) -> bool:
        raise RuntimeError("RF scheduling is retired; use the configured Temporal service")
