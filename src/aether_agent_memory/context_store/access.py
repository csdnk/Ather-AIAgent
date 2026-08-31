from __future__ import annotations

from aether_agent_memory.context_store.mapping import agent_scope_uri
from aether_agent_memory.context_store.models import ContextItem, ContextVisibility
from aether_agent_memory.context_store.uri import AetherUri
from aether_agent_memory.core.scope import Scope


def uri_is_visible_to_scope(uri: AetherUri, scope: Scope) -> bool:
    """Authorize canonical Agent and Session context namespaces."""

    agent_root = agent_scope_uri(scope)
    if not uri.is_within(agent_root):
        return False
    relative = uri.segments[len(agent_root.segments) :]
    if not relative or relative[0] != "sessions":
        return True
    return (
        len(relative) >= 2
        and scope.session_id is not None
        and relative[1] == scope.session_id
    )


def item_is_visible_to_scope(item: ContextItem, scope: Scope) -> bool:
    return all(
        getattr(item.scope, field) == getattr(scope, field)
        for field in _visibility_fields(item.visibility)
    )


def _visibility_fields(visibility: ContextVisibility) -> tuple[str, ...]:
    if visibility == ContextVisibility.TENANT:
        return ("tenant_id",)
    if visibility == ContextVisibility.USER:
        return ("tenant_id", "user_id")
    if visibility == ContextVisibility.AGENT:
        return ("tenant_id", "user_id", "agent_id")
    return ("tenant_id", "user_id", "agent_id", "session_id")
