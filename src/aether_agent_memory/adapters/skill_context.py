from __future__ import annotations

from aether_agent_memory.context_store.mapping import (
    agent_scope_uri,
    context_directory_item,
    scope_uri,
    skill_root_uri,
    skill_uri,
)
from aether_agent_memory.context_store.models import (
    ContextContent,
    ContextItem,
    ContextItemKind,
    ContextLayer,
    ContextVisibility,
)
from aether_agent_memory.context_store.uri import AetherUri
from aether_agent_memory.core.scope import Scope
from aether_agent_memory.skill.models import SkillRecord
from aether_agent_memory.skill.ports import SkillStorePort

_MISSING_SCOPE = "_"


class SkillContextReader:
    """Project a Skill registry into the common Context Catalog."""

    def __init__(self, store: SkillStorePort) -> None:
        self._store = store

    async def get(self, uri: AetherUri) -> ContextItem | None:
        scope = _agent_scope_from_skill_root(uri)
        if scope is not None:
            return context_directory_item(
                uri,
                scope,
                "Skills",
                directory_type="skill_root",
                visibility=ContextVisibility.AGENT,
            )
        parsed = _skill_from_uri(uri)
        if parsed is None:
            return None
        scope, skill_id = parsed
        skill = await self._store.get(scope, skill_id)
        return _skill_to_context_item(skill) if skill is not None else None

    async def list_children(
        self,
        parent: AetherUri,
        *,
        kind: ContextItemKind | None = None,
    ) -> list[ContextItem]:
        scope = _agent_scope_from_uri(parent)
        if scope is not None:
            if kind is not None and kind != ContextItemKind.DIRECTORY:
                return []
            return [
                context_directory_item(
                    skill_root_uri(scope),
                    scope,
                    "Skills",
                    directory_type="skill_root",
                    visibility=ContextVisibility.AGENT,
                )
            ]
        session_scope = _session_scope_from_uri(parent)
        if session_scope is not None:
            if kind is not None and kind != ContextItemKind.DIRECTORY:
                return []
            agent_scope = _agent_scope(session_scope)
            return [
                context_directory_item(
                    skill_root_uri(agent_scope),
                    agent_scope,
                    "Skills",
                    directory_type="skill_root",
                    visibility=ContextVisibility.AGENT,
                    mounted_from=scope_uri(session_scope),
                )
            ]
        scope = _agent_scope_from_skill_root(parent)
        if scope is None or (
            kind is not None and kind != ContextItemKind.SKILL
        ):
            return []
        skills = await self._store.list_scoped(
            tenant_id=scope.tenant_id,
            user_id=scope.user_id,
            agent_id=scope.agent_id,
        )
        return [_skill_to_context_item(skill) for skill in skills if skill.enabled]

    async def read(self, uri: AetherUri, layer: ContextLayer) -> ContextContent | None:
        item = await self.get(uri)
        if item is None:
            return None
        content = item.content_for(layer)
        return content.model_copy(deep=True) if content is not None else None

    async def close(self) -> None:
        close = getattr(self._store, "close", None)
        if close is not None:
            await close()


def _skill_to_context_item(skill_record: SkillRecord) -> ContextItem:
    description = skill_record.description or skill_record.name
    return ContextItem(
        uri=skill_uri(skill_record.scope, skill_record.skill_id),
        kind=ContextItemKind.SKILL,
        visibility=ContextVisibility.AGENT,
        scope=skill_record.scope,
        title=skill_record.name,
        layers={
            ContextLayer.ABSTRACT: ContextContent(
                layer=ContextLayer.ABSTRACT,
                text=description,
                token_estimate=max(len(description) // 4, 1),
                derived=False,
            ),
            ContextLayer.OVERVIEW: ContextContent(
                layer=ContextLayer.OVERVIEW,
                text=description,
                token_estimate=max(len(description) // 4, 1),
                derived=False,
            ),
            ContextLayer.DETAIL: ContextContent(
                layer=ContextLayer.DETAIL,
                text=skill_record.instructions or description,
                token_estimate=max(
                    len(skill_record.instructions or description) // 4, 1
                ),
                derived=False,
            ),
        },
        created_at=skill_record.created_at,
        updated_at=skill_record.updated_at,
        source_revision=skill_record.revision,
        metadata={
            "skill_id": skill_record.skill_id,
            "version": skill_record.version,
            "enabled": skill_record.enabled,
            "source": skill_record.source,
        },
    )


def _agent_scope_from_uri(uri: AetherUri) -> Scope | None:
    if len(uri.segments) != 6 or uri.segments[0::2] != (
        "tenants",
        "users",
        "agents",
    ):
        return None
    scope = Scope(
        tenant_id=_scope_value(uri.segments[1]),
        user_id=_scope_value(uri.segments[3]),
        agent_id=_scope_value(uri.segments[5]),
    )
    return scope if agent_scope_uri(scope) == uri else None


def _agent_scope_from_skill_root(uri: AetherUri) -> Scope | None:
    if uri.segments[-1:] != ("skills",):
        return None
    parent = uri.parent
    scope = _agent_scope_from_uri(parent) if parent is not None else None
    return scope if scope is not None and skill_root_uri(scope) == uri else None


def _session_scope_from_uri(uri: AetherUri) -> Scope | None:
    segments = uri.segments
    if len(segments) != 8 or segments[0::2] != (
        "tenants",
        "users",
        "agents",
        "sessions",
    ):
        return None
    scope = Scope(
        tenant_id=_scope_value(segments[1]),
        user_id=_scope_value(segments[3]),
        agent_id=_scope_value(segments[5]),
        session_id=_scope_value(segments[7]),
    )
    return scope if scope_uri(scope) == uri else None


def _agent_scope(scope: Scope) -> Scope:
    return Scope(
        tenant_id=scope.tenant_id,
        user_id=scope.user_id,
        agent_id=scope.agent_id,
    )


def _skill_from_uri(uri: AetherUri) -> tuple[Scope, str] | None:
    parent = uri.parent
    if parent is None:
        return None
    scope = _agent_scope_from_skill_root(parent)
    return (scope, uri.segments[-1]) if scope is not None else None


def _scope_value(value: str) -> str | None:
    return None if value == _MISSING_SCOPE else value
