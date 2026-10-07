"""Server-owned, one-memory placement authority, separate from login credentials.

Only the durable Operate due queue enrolls these bindings. Enrollment also covers
existing due entries: it verifies the persisted source event and current authority,
and records that adoption. No login grant is renewed or made reusable.
"""

import secrets
from typing import Any

from aether_agent_memory.runtime.contracts.models import (
    ErrorCode,
    EventEnvelope,
    Flow,
    Permission,
    TrustedContext,
)

from .admin_execution import authorize_admin_grants
from .common import FoundationError, fingerprint
from .requests import event_context


def denied() -> None:
    raise FoundationError(ErrorCode.FORBIDDEN, "placement authority revoked or outside binding")


def placement_context(identity: Any, tx: Any, key: str, task_id: str) -> TrustedContext:
    view = tx.read("operate_views", key) or {}
    if view.get("cleanup") or view.get("cleanup_completed") or not view.get("scheduler_event"):
        denied()
    event = EventEnvelope.model_validate(view["scheduler_event"])
    ctx = event_context(tx, event, identity.clock())
    row = tx.read("identities", ctx.principal.principal_id) or {}
    if not row.get("ruoyi_source"):
        return ctx
    memory = view["memory"]
    source = tx.read("outbox", event.event_id) or {}
    if (
        source.get("event") != event.model_dump(mode="json")
        or event.payload.get("memory") != memory
        or event.event_type not in {"memory.changed", "recall.access"}
        or (
            event.event_type == "recall.access"
            and (
                event.payload.get("stage") != "read" or event.payload.get("outcome") != "succeeded"
            )
        )
    ):
        denied()
    delegation = tx.read("admin_execution_bindings", event.request_id)
    actor = TrustedContext.model_validate(delegation["actor_context"]) if delegation else ctx
    # An established event must retain the original credential binding, even when
    # expired; this is provenance, never authorization to replay that credential.
    grant = tx.read("ruoyi_request_grants", actor.request_id) or {}
    if (
        grant.get("principal_id") != actor.principal.principal_id
        or grant.get("auth_epoch") != actor.principal.auth_epoch
    ):
        denied()
    previous = tx.read("operate_scheduler_tasks", task_id)
    if previous:
        bound = TrustedContext.model_validate(
            tx.read("operate_scheduler_bindings", previous)["context"]
        )
        identity.revalidate(tx, bound.model_copy(update={"deadline_at": ctx.deadline_at}))
        return bound.model_copy(update={"deadline_at": ctx.deadline_at})
    bound = ctx.model_copy(
        update={
            "request_id": secrets.token_hex(32),
            "principal": ctx.principal.model_copy(update={"permissions": (Permission.READ,)}),
        }
    )
    binding = {
        "purpose": "operate_placement",
        "memory": memory,
        "memory_key": key,
        "task_id": task_id,
        "context": bound.model_dump(mode="json"),
        "actor_context": actor.model_dump(mode="json"),
        "source_principal": ctx.principal.model_dump(mode="json"),
        "admin_action": delegation["action"] if delegation else None,
        "source_event_id": event.event_id,
        "source_event_hash": fingerprint(view["scheduler_event"]),
        "authority": row.get("ruoyi_authority"),
        "policy": row.get("ruoyi_policy"),
        "enrolled_at": identity.clock(),
        "adopted_existing_due": True,
    }
    tx.write("operate_scheduler_bindings", bound.request_id, binding)
    tx.write("operate_scheduler_tasks", task_id, bound.request_id)
    revalidate_placement_context(identity, tx, bound)
    return bound


def revalidate_placement_context(identity: Any, tx: Any, ctx: TrustedContext) -> bool:
    binding = tx.read("operate_scheduler_bindings", ctx.request_id)
    if binding is None:
        return False
    if (
        binding.get("purpose") != "operate_placement"
        or ctx.principal.model_dump(mode="json") != binding["context"]["principal"]
    ):
        denied()
    actor = TrustedContext.model_validate(binding["actor_context"])
    row = tx.read("identities", actor.principal.principal_id) or {}
    view = tx.read("operate_views", binding["memory_key"]) or {}
    if (
        not row.get("enabled")
        or row.get("principal") != actor.principal.model_dump(mode="json")
        or row.get("ruoyi_authority") != binding["authority"]
        or row.get("ruoyi_policy") != binding["policy"]
        or view.get("memory") != binding["memory"]
        or view.get("cleanup")
        or view.get("cleanup_completed")
        or identity.placement_revalidate is None
    ):
        denied()
    if identity.clock() >= ctx.deadline_at:
        raise FoundationError(ErrorCode.DEADLINE_EXCEEDED, "placement task deadline expired")
    identity.placement_revalidate(tx, row, actor)
    scope = binding["memory"]["scope"]
    if binding["admin_action"]:
        authorize_admin_grants(
            identity, tx, actor, scope["tenant_id"], scope["user_id"], binding["admin_action"]
        )
    else:
        # Shared-memory grants cannot establish an owner's autonomous scheduler.
        home = actor.principal.home_scope.model_dump(mode="json")
        if (
            Permission.READ not in actor.principal.permissions
            or any(
                home[k] != scope[k] for k in ("tenant_id", "user_id", "application_id", "agent_id")
            )
            or any(home[k] is not None and home[k] != scope[k] for k in ("session_id", "task_id"))
        ):
            denied()
    source_scope = binding["source_principal"]["home_scope"]
    if any(
        source_scope[k] != scope[k] for k in ("tenant_id", "user_id", "application_id", "agent_id")
    ):
        denied()
    return True


def permits_placement(
    tx: Any, ctx: TrustedContext, permission: Permission, target: Any
) -> bool | None:
    binding = tx.read("operate_scheduler_bindings", ctx.request_id)
    if binding is None:
        return None
    memory = binding["memory"]
    if permission != Permission.READ or target.scope.model_dump(mode="json") != memory["scope"]:
        return False
    if target.owner == Flow.REMEMBER:
        return (
            target.object_type == "memory"
            and target.object_id == memory["memory_id"]
            and target.version in (None, memory["version"])
        )
    if target.owner != Flow.OPERATE:
        return False
    if target.object_type in {"evaluation", "evaluation_result"}:
        return target.object_id == binding["task_id"]
    if target.object_type == "action":
        row = tx.read("operate_actions", target.object_id) or {}
        intent = row.get("intent", {})
        decision = intent.get("decision", {})
        if decision.get("memory") != memory:
            return False
        own_action = target.object_id == fingerprint(
            [
                memory,
                decision.get("current_tier"),
                decision.get("target_tier"),
                intent.get("expected_epoch"),
                binding["task_id"],
            ]
        )
        # Recovery adopts the exact durable pending intent instead of submitting
        # a replacement when an earlier evaluation lost its response or timed out.
        adopted = (
            tx.read("operate_task_actions", binding["task_id"]) == intent
            and tx.read("operate_pending", binding["memory_key"]) == target.object_id
        )
        return own_action or adopted
    return False
