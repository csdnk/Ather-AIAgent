from __future__ import annotations

from aether_agent_memory.context_store.mapping import (
    context_directory_item,
    scope_uri,
    session_archive_to_context_item,
    session_archive_uri,
    session_history_root_uri,
)
from aether_agent_memory.context_store.models import (
    ContextContent,
    ContextItem,
    ContextItemKind,
    ContextLayer,
)
from aether_agent_memory.context_store.uri import AetherUri
from aether_agent_memory.core.scope import Scope
from aether_agent_memory.session.ports import SessionStorePort

_MISSING_SCOPE = "_"


class SessionContextReader:
    def __init__(self, store: SessionStorePort) -> None:
        self._store = store

    async def get(self, uri: AetherUri) -> ContextItem | None:
        scope = _history_root_from_uri(uri)
        if scope is not None:
            return context_directory_item(
                uri,
                scope,
                "Session history",
                directory_type="session_history",
            )
        parsed = _archive_from_uri(uri)
        if parsed is None:
            return None
        scope, archive_id = parsed
        record = await self._store.get(scope)
        if record is None:
            return None
        archive = next(
            (item for item in record.archives if item.archive_id == archive_id),
            None,
        )
        return (
            session_archive_to_context_item(
                scope,
                archive,
            )
            if archive is not None
            else None
        )

    async def list_children(
        self,
        parent: AetherUri,
        *,
        kind: ContextItemKind | None = None,
    ) -> list[ContextItem]:
        scope = _scope_from_uri(parent)
        if scope is not None:
            if kind is not None and kind != ContextItemKind.DIRECTORY:
                return []
            return [
                context_directory_item(
                    session_history_root_uri(scope),
                    scope,
                    "Session history",
                    directory_type="session_history",
                )
            ]
        scope = _history_root_from_uri(parent)
        if scope is None or (kind is not None and kind != ContextItemKind.SESSION):
            return []
        record = await self._store.get(scope)
        if record is None:
            return []
        return [
            session_archive_to_context_item(
                scope,
                item,
            )
            for item in record.archives
        ]

    async def read(self, uri: AetherUri, layer: ContextLayer) -> ContextContent | None:
        item = await self.get(uri)
        if item is None:
            return None
        content = item.content_for(layer)
        return content.model_copy(deep=True) if content is not None else None


def _scope_from_uri(uri: AetherUri) -> Scope | None:
    segments = uri.segments
    if len(segments) != 8:
        return None
    if segments[0::2] != ("tenants", "users", "agents", "sessions"):
        return None
    scope = Scope(
        tenant_id=_scope_value(segments[1]),
        user_id=_scope_value(segments[3]),
        agent_id=_scope_value(segments[5]),
        session_id=_scope_value(segments[7]),
    )
    return scope if scope_uri(scope) == uri else None


def _history_root_from_uri(uri: AetherUri) -> Scope | None:
    parent = uri.parent
    if parent is None or uri.segments[-1] != "history":
        return None
    scope = _scope_from_uri(parent)
    return scope if scope is not None and session_history_root_uri(scope) == uri else None


def _archive_from_uri(uri: AetherUri) -> tuple[Scope, str] | None:
    parent = uri.parent
    if parent is None:
        return None
    scope = _history_root_from_uri(parent)
    if scope is None or session_archive_uri(scope, uri.segments[-1]) != uri:
        return None
    return scope, uri.segments[-1]


def _scope_value(value: str) -> str | None:
    return None if value == _MISSING_SCOPE else value
