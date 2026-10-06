"""Explicit read-only diagnostic authority, never a general Identity grant.

Only the admin HTTP service opens this context. The original operator, request,
deadline and revocable credential remain unchanged. Business readers opt in at
their READ boundary; mutation permissions and Identity.permits are untouched.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any

from aether_agent_memory.runtime.contracts.models import ErrorCode, Permission, RecordRef

from .common import FoundationError
from .transactions import native


@dataclass(frozen=True)
class DiagnosticTarget:
    principal_id: str
    request_id: str
    tenant_id: str
    user_id: str
    application_id: str
    agent_id: str


_target: ContextVar[DiagnosticTarget | None] = ContextVar("content_diagnostic_target", default=None)


def authorize_operator(identity: Any, tx: Any, ctx: Any, tenant_id: str | None = None) -> None:
    identity.revalidate(tx, ctx)
    row = native(tx).read("identities", ctx.principal.principal_id) or {}
    roles = row.get("ruoyi_roles", [])
    grants = row.get("ruoyi_permissions", [])
    platform = "aether_platform_admin" in roles
    required = "aether:ops:read" if tenant_id is None else "aether:content:read"
    if (
        not row.get("ruoyi_source")
        or (not {required, "aether:ops:read"}.issubset(grants) and "*:*:*" not in grants)
        or (not platform and "aether_tenant_admin" not in roles)
        or (platform and ctx.principal.home_scope.tenant_id != "aether_platform_operations")
        or (not platform and (tenant_id is None or tenant_id != ctx.principal.home_scope.tenant_id))
        or tenant_id == "aether_platform_operations"
    ):
        raise FoundationError(ErrorCode.FORBIDDEN, "explicit admin diagnostic permission required")


@contextmanager
def diagnostic_read(identity: Any, ctx: Any, tenant_id: str, user_id: str) -> Iterator[None]:
    with identity.uow.transaction() as tx:
        authorize_operator(identity, tx, ctx, tenant_id)
    scope = ctx.principal.home_scope
    token = _target.set(
        DiagnosticTarget(
            ctx.principal.principal_id,
            ctx.request_id,
            tenant_id,
            user_id,
            scope.application_id,
            scope.agent_id,
        )
    )
    try:
        yield
        with identity.uow.transaction() as tx:
            authorize_operator(identity, tx, ctx, tenant_id)
    finally:
        _target.reset(token)


def permits_content_read(
    identity: Any, tx: Any, ctx: Any, permission: Permission, target: RecordRef
) -> bool:
    selected = _target.get()
    if selected is None or permission != Permission.READ:
        return bool(identity.permits(tx, ctx, permission, target))
    authorize_operator(identity, tx, ctx, selected.tenant_id)
    return (
        selected.principal_id == ctx.principal.principal_id
        and selected.request_id == ctx.request_id
        and (str(target.owner), target.object_type)
        in {("remember", "memory"), ("remember", "source"), ("recall", "recall")}
        and all(
            getattr(target.scope, name) == getattr(selected, name)
            for name in ("tenant_id", "user_id", "application_id", "agent_id")
        )
    )


def authorize_content_read(identity: Any, tx: Any, ctx: Any, target: RecordRef) -> None:
    if not permits_content_read(identity, tx, ctx, Permission.READ, target):
        raise FoundationError(
            ErrorCode.FORBIDDEN, "content target outside authorized diagnostic scope"
        )


def diagnostic_expectations(identity: Any, tx: Any, ctx: Any, expectations: Any) -> Any:
    """Rebind only the authorization stamp to the current authorized reader.

    Content versions, hashes, relations and projection manifests remain exact.
    The persisted original result/guards are never changed.
    """
    if _target.get() is None:
        return expectations
    from aether_agent_memory.remember.basic.service import memory_ref

    for guard in expectations.expected:
        authorize_content_read(identity, tx, ctx, memory_ref(guard.memory))
    return expectations.model_copy(
        update={
            "expected": tuple(
                guard.model_copy(update={"authorization_epoch": ctx.principal.auth_epoch})
                for guard in expectations.expected
            )
        }
    )
