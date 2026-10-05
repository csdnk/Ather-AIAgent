"""Scope restrictions and durable request identity shared by flow services."""

import secrets
from hashlib import sha256

from aether_agent_memory.runtime.contracts.models import (
    ErrorCode,
    EventEnvelope,
    Principal,
    Scope,
    ScopeSelector,
    TrustedContext,
)
from aether_agent_memory.runtime.storage.ports import MetadataTransaction

from .common import FoundationError, fingerprint, later


def text_hash(text: str) -> str:
    return sha256(text.encode("utf-8")).hexdigest()


def select_scope(ctx: TrustedContext, selection: ScopeSelector) -> Scope:
    home = ctx.principal.home_scope
    updates = selection.model_dump(exclude_none=True)
    for key, value in updates.items():
        current = getattr(home, key)
        if current != value and (key not in {"session_id", "task_id"} or current is not None):
            raise FoundationError(ErrorCode.FORBIDDEN, "selection cannot expand principal scope")
    return Scope.model_validate({**home.model_dump(), **updates})


def matches(actual: Scope, requested: Scope) -> bool:
    return all(
        value is None or getattr(actual, key) == value
        for key, value in requested.model_dump().items()
    )


def request_key(ctx: TrustedContext, endpoint: str) -> str:
    return fingerprint([ctx.principal.principal_id, endpoint, ctx.operation_id])


def event_context(tx: MetadataTransaction, event: EventEnvelope, now: str) -> TrustedContext:
    identity = tx.read("identities", event.initiator_id)
    if (
        not identity
        or not identity["enabled"]
        or identity["principal"]["auth_epoch"] != event.initiator_auth_epoch
    ):
        raise FoundationError(ErrorCode.FORBIDDEN, "event initiator revoked")
    original = tx.read("outbox", event.event_id)
    parent = (original or {}).get("context", {}).get("span_id", secrets.token_hex(8))
    return TrustedContext(
        principal=Principal.model_validate(identity["principal"]),
        request_id=event.request_id,
        operation_id=event.event_id,
        trace_id=event.trace_id,
        span_id=parent,
        deadline_at=later(now, 300),
    )
