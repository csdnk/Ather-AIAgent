from __future__ import annotations

from aether_agent_memory.context_store.mapping import (
    agent_memory_root_uri,
    agent_scope_uri,
    context_directory_item,
    document_memory_to_resource_item,
    is_document_memory,
    memory_collection_uri,
    memory_root_uri,
    memory_to_context_item,
    memory_uri,
    resource_collection_uri,
    resource_root_uri,
    resource_uri,
    scope_uri,
)
from aether_agent_memory.context_store.models import (
    ContextContent,
    ContextItem,
    ContextItemKind,
    ContextLayer,
    ContextVisibility,
)
from aether_agent_memory.context_store.uri import AetherUri
from aether_agent_memory.core.enums import MemoryType
from aether_agent_memory.core.memory import Memory
from aether_agent_memory.core.scope import Scope
from aether_agent_memory.runtime.ports import MemoryStorePort

_MISSING_SCOPE = "_"


class MemoryStoreContextReader:
    """Read ContextItem views directly from the authoritative MemoryStore."""

    def __init__(self, store: MemoryStorePort) -> None:
        self._store = store

    async def get(self, uri: AetherUri) -> ContextItem | None:
        directory = _directory_from_uri(uri)
        if directory is not None:
            scope, title, directory_type = directory
            return context_directory_item(
                uri,
                scope,
                title,
                directory_type=directory_type,
                visibility=(
                    ContextVisibility.AGENT
                    if scope.session_id is None
                    else ContextVisibility.SESSION
                ),
            )
        memory_id = _memory_id_from_uri(uri)
        resource_id = _resource_id_from_uri(uri)
        item_id = memory_id or resource_id
        if item_id is None:
            return None
        memory = await self._store.get(item_id)
        if memory is None:
            return None
        if memory_id is not None and memory_uri(memory) == uri:
            return memory_to_context_item(memory)
        if (
            resource_id is not None
            and is_document_memory(memory)
            and resource_uri(_memory_scope(memory), memory.id) == uri
        ):
            return document_memory_to_resource_item(memory)
        return None

    async def list_children(
        self,
        parent: AetherUri,
        *,
        kind: ContextItemKind | None = None,
    ) -> list[ContextItem]:
        agent_scope = _agent_scope_from_uri(parent)
        if agent_scope is not None:
            if kind is not None and kind != ContextItemKind.DIRECTORY:
                return []
            return [
                context_directory_item(
                    agent_memory_root_uri(agent_scope),
                    agent_scope,
                    "Agent memories",
                    directory_type="agent_memory_root",
                    visibility=ContextVisibility.AGENT,
                ),
                context_directory_item(
                    resource_root_uri(agent_scope),
                    agent_scope,
                    "Resources",
                    directory_type="resource_root",
                    visibility=ContextVisibility.AGENT,
                ),
            ]
        scope = _scope_from_uri(parent)
        if scope is not None:
            if kind is not None and kind != ContextItemKind.DIRECTORY:
                return []
            return [
                context_directory_item(
                    memory_root_uri(scope),
                    scope,
                    "Memories",
                    directory_type="memory_root",
                ),
                context_directory_item(
                    resource_root_uri(scope),
                    _agent_scope(scope),
                    "Resources",
                    directory_type="resource_root",
                    visibility=ContextVisibility.AGENT,
                    mounted_from=scope_uri(scope),
                ),
            ]

        scope = _memory_root_from_uri(parent)
        if scope is not None:
            if kind is not None and kind != ContextItemKind.DIRECTORY:
                return []
            directories: list[ContextItem] = []
            for memory_type in MemoryType:
                visibility = (
                    ContextVisibility.SESSION
                    if memory_type == MemoryType.WORKING
                    else ContextVisibility.AGENT
                )
                item_scope = (
                    scope
                    if visibility == ContextVisibility.SESSION
                    else _agent_scope(scope)
                )
                directories.append(
                    context_directory_item(
                        memory_collection_uri(scope, memory_type),
                        item_scope,
                        f"{memory_type.value.title()} memories",
                        directory_type="memory_collection",
                        visibility=visibility,
                        mounted_from=(
                            memory_root_uri(scope)
                            if visibility == ContextVisibility.AGENT
                            else None
                        ),
                    )
                )
            return directories

        scope = _agent_memory_root_from_uri(parent)
        if scope is not None:
            if kind is not None and kind != ContextItemKind.DIRECTORY:
                return []
            return [
                context_directory_item(
                    memory_collection_uri(scope, memory_type),
                    scope,
                    f"{memory_type.value.title()} memories",
                    directory_type="memory_collection",
                    visibility=ContextVisibility.AGENT,
                )
                for memory_type in (MemoryType.EPISODIC, MemoryType.SEMANTIC)
            ]

        scope = _resource_root_from_uri(parent)
        if scope is not None:
            if kind is not None and kind != ContextItemKind.DIRECTORY:
                return []
            return [
                context_directory_item(
                    resource_collection_uri(scope),
                    _agent_scope(scope),
                    "Documents",
                    directory_type="resource_collection",
                    visibility=ContextVisibility.AGENT,
                )
            ]

        scope = _resource_collection_from_uri(parent)
        if scope is not None:
            if kind is not None and kind != ContextItemKind.RESOURCE:
                return []
            memories = await self._list_scope(scope)
            return [
                document_memory_to_resource_item(memory)
                for memory in memories
                if is_document_memory(memory)
            ]

        if kind is not None and kind != ContextItemKind.MEMORY:
            return []
        parsed = _memory_collection_from_uri(parent)
        if parsed is None:
            return []
        scope, memory_type = parsed
        memories = await self._list_scope(scope)
        return [
            memory_to_context_item(memory)
            for memory in memories
            if memory.type == memory_type and memory_uri(memory).parent == parent
        ]

    async def _list_scope(self, scope: Scope) -> list[Memory]:
        return await self._store.list_scoped(
            tenant_id=scope.tenant_id,
            user_id=scope.user_id,
            agent_id=scope.agent_id,
            session_id=scope.session_id,
        )

    async def read(self, uri: AetherUri, layer: ContextLayer) -> ContextContent | None:
        item = await self.get(uri)
        if item is None:
            return None
        content = item.content_for(layer)
        return content.model_copy(deep=True) if content is not None else None


def _memory_id_from_uri(uri: AetherUri) -> str | None:
    segments = uri.segments
    if (
        len(segments) == 11
        and segments[8:10] == ("memories", MemoryType.WORKING.value)
    ):
        return segments[10]
    if (
        len(segments) == 9
        and segments[6] == "memories"
        and segments[7] in {MemoryType.EPISODIC.value, MemoryType.SEMANTIC.value}
    ):
        return segments[8]
    return None


def _resource_id_from_uri(uri: AetherUri) -> str | None:
    segments = uri.segments
    if len(segments) != 9 or segments[6:8] != ("resources", "documents"):
        return None
    return segments[8]


def _directory_from_uri(uri: AetherUri) -> tuple[Scope, str, str] | None:
    scope = _agent_scope_from_uri(uri)
    if scope is not None:
        return scope, "Agent context", "agent_root"
    scope = _scope_from_uri(uri)
    if scope is not None:
        return scope, "Session context", "scope_root"
    scope = _memory_root_from_uri(uri)
    if scope is not None:
        return scope, "Memories", "memory_root"
    scope = _agent_memory_root_from_uri(uri)
    if scope is not None:
        return scope, "Agent memories", "agent_memory_root"
    scope = _resource_root_from_uri(uri)
    if scope is not None:
        return scope, "Resources", "resource_root"
    scope = _resource_collection_from_uri(uri)
    if scope is not None:
        return scope, "Documents", "resource_collection"
    parsed = _memory_collection_from_uri(uri)
    if parsed is None:
        return None
    scope, memory_type = parsed
    return scope, f"{memory_type.value.title()} memories", "memory_collection"


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


def _agent_scope_from_uri(uri: AetherUri) -> Scope | None:
    segments = uri.segments
    if len(segments) != 6 or segments[0::2] != ("tenants", "users", "agents"):
        return None
    scope = Scope(
        tenant_id=_scope_value(segments[1]),
        user_id=_scope_value(segments[3]),
        agent_id=_scope_value(segments[5]),
    )
    return scope if agent_scope_uri(scope) == uri else None


def _memory_root_from_uri(uri: AetherUri) -> Scope | None:
    parent = uri.parent
    if parent is None or uri.segments[-1] != "memories":
        return None
    scope = _scope_from_uri(parent)
    return scope if scope is not None and memory_root_uri(scope) == uri else None


def _agent_memory_root_from_uri(uri: AetherUri) -> Scope | None:
    parent = uri.parent
    if parent is None or uri.segments[-1] != "memories":
        return None
    scope = _agent_scope_from_uri(parent)
    return scope if scope is not None and agent_memory_root_uri(scope) == uri else None


def _resource_root_from_uri(uri: AetherUri) -> Scope | None:
    parent = uri.parent
    if parent is None or uri.segments[-1] != "resources":
        return None
    scope = _agent_scope_from_uri(parent)
    return scope if scope is not None and resource_root_uri(scope) == uri else None


def _resource_collection_from_uri(uri: AetherUri) -> Scope | None:
    parent = uri.parent
    if parent is None or uri.segments[-1] != "documents":
        return None
    scope = _resource_root_from_uri(parent)
    return (
        scope
        if scope is not None and resource_collection_uri(scope) == uri
        else None
    )


def _memory_collection_from_uri(
    uri: AetherUri,
) -> tuple[Scope, MemoryType] | None:
    segments = uri.segments
    if len(segments) == 10 and segments[0::2] == (
        "tenants",
        "users",
        "agents",
        "sessions",
        "memories",
    ):
        if segments[9] != MemoryType.WORKING.value:
            return None
        memory_type = MemoryType.WORKING
        scope = Scope(
            tenant_id=_scope_value(segments[1]),
            user_id=_scope_value(segments[3]),
            agent_id=_scope_value(segments[5]),
            session_id=_scope_value(segments[7]),
        )
    elif (
        len(segments) == 8
        and segments[0::2] == ("tenants", "users", "agents", "memories")
        and segments[7] in {MemoryType.EPISODIC.value, MemoryType.SEMANTIC.value}
    ):
        memory_type = MemoryType(segments[7])
        scope = Scope(
            tenant_id=_scope_value(segments[1]),
            user_id=_scope_value(segments[3]),
            agent_id=_scope_value(segments[5]),
        )
    else:
        return None
    if memory_collection_uri(scope, memory_type) != uri:
        return None
    return scope, memory_type


def _scope_value(value: str) -> str | None:
    return None if value == _MISSING_SCOPE else value


def _memory_scope(memory: Memory) -> Scope:
    return Scope(
        tenant_id=memory.tenant_id,
        user_id=memory.user_id,
        agent_id=memory.agent_id,
        session_id=memory.session_id,
        task_id=memory.task_id,
    )


def _agent_scope(scope: Scope) -> Scope:
    return Scope(
        tenant_id=scope.tenant_id,
        user_id=scope.user_id,
        agent_id=scope.agent_id,
    )
