from __future__ import annotations

from aether_agent_memory.core.scope import Scope
from aether_agent_memory.runtime.errors import ScopeError
from aether_agent_memory.runtime.request_context import RequestContext


def require_agent_scope(
    context: RequestContext,
    *,
    expected: Scope | None = None,
    operation: str = "operation",
) -> Scope:
    """Resolve and authorize an agent-scoped application operation."""
    if context.project_id is not None or (expected is not None and expected.project_id is not None):
        raise ScopeError(
            f"{operation} requires a project-aware authorization adapter; "
            "legacy scope is insufficient",
            trace_id=context.trace_id,
        )
    if not context.tenant_id or not context.user_id or not context.agent_id:
        raise ScopeError(
            f"{operation} requires tenant/user/agent scope",
            trace_id=context.trace_id,
        )
    scope = Scope(
        tenant_id=context.tenant_id,
        user_id=context.user_id,
        agent_id=context.agent_id,
    )
    if expected is not None and any(
        getattr(scope, field) != getattr(expected, field)
        for field in ("tenant_id", "user_id", "agent_id")
    ):
        raise ScopeError(
            f"{operation} scope does not match request context",
            trace_id=context.trace_id,
        )
    return scope


def require_session_scope(
    context: RequestContext,
    *,
    expected: Scope | None = None,
    operation: str = "session operation",
) -> Scope:
    """Resolve a complete session scope after agent-level authorization."""
    scope = require_agent_scope(context, expected=expected, operation=operation)
    if not context.session_id:
        raise ScopeError(
            f"{operation} requires session_id",
            trace_id=context.trace_id,
        )
    resolved = Scope(
        tenant_id=scope.tenant_id,
        user_id=scope.user_id,
        agent_id=scope.agent_id,
        session_id=context.session_id,
        task_id=context.task_id,
    )
    if expected is not None and (
        expected.session_id != resolved.session_id or expected.task_id != resolved.task_id
    ):
        raise ScopeError(
            f"{operation} scope does not match request context",
            trace_id=context.trace_id,
        )
    return resolved


__all__ = ["require_agent_scope", "require_session_scope"]
