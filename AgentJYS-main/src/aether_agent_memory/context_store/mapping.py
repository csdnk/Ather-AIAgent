from __future__ import annotations

from typing import TYPE_CHECKING, Any

from aether_agent_memory.context_store.models import (
    ContextContent,
    ContextItem,
    ContextItemKind,
    ContextLayer,
    ContextVisibility,
)
from aether_agent_memory.context_store.uri import AetherUri
from aether_agent_memory.core.enums import MemoryType, SourceType
from aether_agent_memory.core.memory import Memory
from aether_agent_memory.core.scope import Scope

if TYPE_CHECKING:
    # 此处只需要类型注解；运行时导入会经 context_store.__init__ 回到 session.models。
    from aether_agent_memory.session.models import SessionArchive

_MISSING_SCOPE = "_"


def memory_uri(memory: Memory) -> AetherUri:
    scope = Scope(
        tenant_id=memory.tenant_id,
        user_id=memory.user_id,
        agent_id=memory.agent_id,
        session_id=memory.session_id,
    )
    return memory_collection_uri(scope, memory.type).child(memory.id)


def memory_collection_uri(
    scope: Scope,
    memory_type: MemoryType | str,
) -> AetherUri:
    value = getattr(memory_type, "value", memory_type)
    resolved_type = MemoryType(str(value))
    root = (
        memory_root_uri(scope)
        if resolved_type == MemoryType.WORKING
        else agent_memory_root_uri(scope)
    )
    return root.child(resolved_type.value)


def memory_root_uri(scope: Scope) -> AetherUri:
    return _scoped_uri(scope, "memories")


def agent_memory_root_uri(scope: Scope) -> AetherUri:
    return agent_scope_uri(scope).child("memories")


def scope_uri(scope: Scope) -> AetherUri:
    return _scoped_uri(scope)


def agent_scope_uri(scope: Scope) -> AetherUri:
    return AetherUri.from_segments(
        "tenants",
        scope.tenant_id or _MISSING_SCOPE,
        "users",
        scope.user_id or _MISSING_SCOPE,
        "agents",
        scope.agent_id or _MISSING_SCOPE,
    )


def resource_uri(scope: Scope, resource_id: str, *, category: str = "documents") -> AetherUri:
    return resource_collection_uri(scope, category).child(resource_id)


def resource_root_uri(scope: Scope) -> AetherUri:
    return agent_scope_uri(scope).child("resources")


def resource_collection_uri(scope: Scope, category: str = "documents") -> AetherUri:
    return resource_root_uri(scope).child(category)


def skill_root_uri(scope: Scope) -> AetherUri:
    return agent_scope_uri(scope).child("skills")


def skill_uri(scope: Scope, skill_id: str) -> AetherUri:
    return skill_root_uri(scope).child(skill_id)


def session_history_root_uri(scope: Scope) -> AetherUri:
    return scope_uri(scope).child("history")


def session_archive_uri(scope: Scope, archive_id: str) -> AetherUri:
    return session_history_root_uri(scope).child(archive_id)


def context_directory_item(
    uri: AetherUri,
    scope: Scope,
    title: str,
    *,
    directory_type: str,
    visibility: ContextVisibility = ContextVisibility.SESSION,
    mounted_from: AetherUri | None = None,
) -> ContextItem:
    """Create a deterministic logical directory without a second data copy."""

    return ContextItem(
        uri=uri,
        kind=ContextItemKind.DIRECTORY,
        visibility=visibility,
        scope=scope,
        title=title,
        layers={
            ContextLayer.ABSTRACT: ContextContent(
                layer=ContextLayer.ABSTRACT,
                text=title,
                token_estimate=_estimate_tokens(title),
                derived=True,
                generator="deterministic-directory-v1",
            ),
            ContextLayer.OVERVIEW: ContextContent(
                layer=ContextLayer.OVERVIEW,
                text=f"{title} directory; child context is available for browsing.",
                token_estimate=_estimate_tokens(title) + 7,
                derived=True,
                generator="deterministic-directory-v1",
            ),
        },
        metadata={
            "directory_type": directory_type,
            "has_children": True,
            "virtual": True,
            **(
                {"mounted": True, "mounted_from": str(mounted_from)}
                if mounted_from is not None
                else {}
            ),
        },
    )


def memory_to_context_item(memory: Memory) -> ContextItem:
    content_ref = _memory_content_ref(memory)
    layers = {
        ContextLayer.ABSTRACT: ContextContent(
            layer=ContextLayer.ABSTRACT,
            text=_preview(memory.content, 256),
            token_estimate=_estimate_tokens(_preview(memory.content, 256)),
            derived=True,
            generator="deterministic-preview-v1",
        ),
        ContextLayer.DETAIL: ContextContent(
            layer=ContextLayer.DETAIL,
            text=memory.content,
            content_ref=content_ref,
            token_estimate=_estimate_tokens(memory.content),
            derived=False,
        ),
    }
    overview = _metadata_string(memory.metadata, "overview") or _preview(
        memory.content, 1024
    )
    layers[ContextLayer.OVERVIEW] = ContextContent(
        layer=ContextLayer.OVERVIEW,
        text=overview,
        token_estimate=_estimate_tokens(overview),
        derived=True,
        generator=(
            _metadata_string(memory.metadata, "overview_generator")
            or "deterministic-overview-v1"
        ),
    )
    scope = Scope(
        tenant_id=memory.tenant_id,
        user_id=memory.user_id,
        agent_id=memory.agent_id,
        session_id=memory.session_id,
        task_id=memory.task_id,
    )
    return ContextItem(
        uri=memory_uri(memory),
        kind=ContextItemKind.MEMORY,
        visibility=(
            ContextVisibility.SESSION
            if memory.type == MemoryType.WORKING
            else ContextVisibility.AGENT
        ),
        scope=scope,
        title=_metadata_string(memory.metadata, "title") or _preview(memory.content, 80),
        layers=layers,
        source_revision=memory.revision,
        created_at=memory.created_at,
        updated_at=memory.updated_at,
        metadata={
            "memory_id": memory.id,
            "memory_type": memory.type.value,
            "memory_state": memory.state.value,
            "source": memory.source.value,
            "tags": list(memory.tags),
        },
    )


def document_memory_to_resource_item(memory: Memory) -> ContextItem:
    """Project a long-document Memory record into the Resource namespace.

    The existing Memory record remains the processing fact. P2 E2 remains the
    authoritative long-text body, so L2 exposes its reference instead of making
    the Context view another full-content copy.
    """

    if not is_document_memory(memory):
        raise ValueError("only document memories can be projected as resources")
    scope = Scope(
        tenant_id=memory.tenant_id,
        user_id=memory.user_id,
        agent_id=memory.agent_id,
        session_id=memory.session_id,
        task_id=memory.task_id,
    )
    content_ref = _memory_content_ref(memory)
    layers = {
        ContextLayer.ABSTRACT: ContextContent(
            layer=ContextLayer.ABSTRACT,
            text=_preview(memory.content, 256),
            token_estimate=_estimate_tokens(_preview(memory.content, 256)),
            derived=True,
            generator="deterministic-preview-v1",
        ),
    }
    if content_ref is not None:
        layers[ContextLayer.DETAIL] = ContextContent(
            layer=ContextLayer.DETAIL,
            content_ref=content_ref,
            derived=False,
        )
    overview = _metadata_string(memory.metadata, "overview") or _preview(
        memory.content, 1024
    )
    layers[ContextLayer.OVERVIEW] = ContextContent(
        layer=ContextLayer.OVERVIEW,
        text=overview,
        token_estimate=_estimate_tokens(overview),
        derived=True,
        generator=(
            _metadata_string(memory.metadata, "overview_generator")
            or "deterministic-overview-v1"
        ),
    )
    return ContextItem(
        uri=resource_uri(scope, memory.id),
        kind=ContextItemKind.RESOURCE,
        visibility=ContextVisibility.AGENT,
        scope=scope,
        title=(
            _metadata_string(memory.metadata, "title")
            or memory.source_id
            or memory.object_id
            or _preview(memory.content, 80)
        ),
        layers=layers,
        source_revision=memory.revision,
        created_at=memory.created_at,
        updated_at=memory.updated_at,
        metadata={
            "source_memory_id": memory.id,
            "task_id": memory.task_id,
            "source_id": memory.source_id,
            "object_id": memory.object_id,
            "source": memory.source.value,
            "content_authority": "p2-object" if content_ref else "unresolved",
            "processing_status": memory.metadata.get("pipeline_status"),
            "tags": list(memory.tags),
        },
    )


def is_document_memory(memory: Memory) -> bool:
    return (
        memory.source == SourceType.DOCUMENT
        or "document" in memory.tags
        or memory.metadata.get("ingest_path") == "celery-long-text"
    )


def session_archive_to_context_item(
    scope: Scope,
    archive: SessionArchive,
    *,
    source_revision: int | None = None,
) -> ContextItem:
    transcript = "\n".join(
        f"{message.role.value}: {message.content}" for message in archive.messages
    )
    return ContextItem(
        uri=session_archive_uri(scope, archive.archive_id),
        kind=ContextItemKind.SESSION,
        visibility=ContextVisibility.SESSION,
        scope=scope,
        title=_preview(archive.abstract, 80),
        layers={
            ContextLayer.ABSTRACT: ContextContent(
                layer=ContextLayer.ABSTRACT,
                text=archive.abstract,
                token_estimate=_estimate_tokens(archive.abstract),
                derived=True,
                generator="session-archive-deterministic-v1",
                generated_at=archive.created_at,
            ),
            ContextLayer.OVERVIEW: ContextContent(
                layer=ContextLayer.OVERVIEW,
                text=archive.overview,
                token_estimate=_estimate_tokens(archive.overview),
                derived=True,
                generator="session-archive-deterministic-v1",
                generated_at=archive.created_at,
            ),
            ContextLayer.DETAIL: ContextContent(
                layer=ContextLayer.DETAIL,
                text=transcript,
                token_estimate=_estimate_tokens(transcript),
                derived=False,
            ),
        },
        source_revision=(
            archive.source_revision if source_revision is None else source_revision
        ),
        created_at=archive.created_at,
        updated_at=archive.created_at,
        metadata={
            "archive_id": archive.archive_id,
            "message_count": len(archive.messages),
            "memory_extraction_status": archive.extraction_status,
        },
    )


def _scoped_uri(scope: Scope, *tail: str) -> AetherUri:
    return AetherUri.from_segments(
        "tenants",
        scope.tenant_id or _MISSING_SCOPE,
        "users",
        scope.user_id or _MISSING_SCOPE,
        "agents",
        scope.agent_id or _MISSING_SCOPE,
        "sessions",
        scope.session_id or _MISSING_SCOPE,
        *tail,
    )


def _memory_content_ref(memory: Memory) -> str | None:
    if memory.placement is not None and memory.placement.content_ref:
        return memory.placement.content_ref
    value = memory.metadata.get("content_ref")
    return str(value) if value is not None else None


def _metadata_string(metadata: dict[str, Any], key: str) -> str | None:
    value = metadata.get(key)
    return value.strip() if isinstance(value, str) and value.strip() else None


def _preview(text: str, limit: int) -> str:
    normalized = " ".join(text.split())
    return normalized if len(normalized) <= limit else normalized[: limit - 1].rstrip() + "..."


def _estimate_tokens(text: str) -> int:
    return max(len(text) // 4, 1)
