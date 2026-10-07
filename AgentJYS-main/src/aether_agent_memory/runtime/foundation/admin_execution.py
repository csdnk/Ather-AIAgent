"""Durable, action-scoped admin delegation; the authenticated actor never changes."""

import secrets
from typing import Any

from aether_agent_memory.runtime.contracts.models import ErrorCode, Permission, TrustedContext

from .common import FoundationError, fingerprint
from .content_diagnostics import authorize_operator
from .transactions import native

ACTION_GRANTS = {
    "create": "aether:memory:create",
    "update": "aether:memory:update",
    "delete": "aether:memory:delete",
    "archive": "aether:memory:update",
    "activate": "aether:memory:update",
    "recall": "aether:recall:execute",
}


def authorize_admin_execution(
    identity: Any, tx: Any, ctx: TrustedContext, tenant: str, user: str, action: str
) -> None:
    authorize_operator(identity, tx, ctx, tenant)
    row = native(tx).read("identities", ctx.principal.principal_id)
    required = {"aether:memories:execute", ACTION_GRANTS[action]}
    grants = row.get("ruoyi_permissions", [])
    if not required.issubset(grants) and "*:*:*" not in grants:
        raise FoundationError(ErrorCode.FORBIDDEN, "explicit admin action permission required")
    check = getattr(identity, "admin_target_check", None)
    if check is None:
        raise FoundationError(ErrorCode.FORBIDDEN, "admin target authority unavailable")
    check(tenant, user)


def bind_admin_context(
    identity: Any, ctx: TrustedContext, tenant: str, user: str, action: str
) -> TrustedContext:
    """Only called after the HTTP adapter has authenticated the original operator.

    A fresh server-generated request ID separates delegation from caller-supplied
    request IDs. No identity row, static grant, or target credential is created.
    """
    with identity.uow.transaction() as tx:
        if tx.read("admin_execution_bindings", ctx.request_id):
            raise FoundationError(ErrorCode.FORBIDDEN, "delegation cannot be nested")
        authorize_admin_execution(identity, tx, ctx, tenant, user, action)
        permissions = [Permission.READ, Permission.WRITE]
        if action == "update":
            permissions.append(Permission.CORRECT)
        if action == "delete":
            permissions.append(Permission.DELETE)
        scope = ctx.principal.home_scope.model_copy(
            update={
                "tenant_id": tenant,
                "user_id": user,
                "session_id": None,
                "task_id": None,
            }
        )
        projected = ctx.principal.model_copy(
            update={
                "home_scope": scope,
                "permissions": tuple(permissions),
            }
        )
        bound = ctx.model_copy(
            update={
                "principal": projected,
                "request_id": secrets.token_hex(32),
                "operation_id": fingerprint(
                    ["admin", ctx.principal.principal_id, ctx.operation_id]
                ),
            }
        )
        tx.write(
            "admin_execution_bindings",
            bound.request_id,
            {
                "actor_context": ctx.model_dump(mode="json"),
                "projected_principal": projected.model_dump(mode="json"),
                "action": action,
            },
        )
        return bound


def revalidate_admin_context(identity: Any, tx: Any, ctx: TrustedContext) -> bool:
    binding = native(tx).read("admin_execution_bindings", ctx.request_id)
    if binding is None:
        return False
    original = TrustedContext.model_validate(binding["actor_context"])
    if (
        ctx.principal.model_dump(mode="json") != binding["projected_principal"]
        or ctx.principal.principal_id != original.principal.principal_id
        or ctx.principal.auth_epoch != original.principal.auth_epoch
        or native(tx).read("admin_execution_bindings", original.request_id)
    ):
        raise FoundationError(ErrorCode.FORBIDDEN, "admin execution binding changed")
    # Workers extend their task deadline; they never extend the credential expiry.
    original = original.model_copy(update={"deadline_at": ctx.deadline_at})
    authorize_admin_execution(
        identity,
        tx,
        original,
        ctx.principal.home_scope.tenant_id,
        ctx.principal.home_scope.user_id,
        binding["action"],
    )
    return True
