from __future__ import annotations

import pytest

from aether_agent_memory.adapters.skill_context import SkillContextReader
from aether_agent_memory.adapters.skill_store import InMemorySkillStore
from aether_agent_memory.application.skill import (
    DeleteSkillUseCase,
    RegisterSkillUseCase,
    SkillRegistration,
)
from aether_agent_memory.context_store import (
    ContextItemKind,
    ContextLayer,
    scope_uri,
    skill_root_uri,
    skill_uri,
)
from aether_agent_memory.context_store.in_memory import InMemorySemanticIndex
from aether_agent_memory.core.scope import Scope
from aether_agent_memory.runtime.errors import ScopeError
from aether_agent_memory.runtime.request_context import RequestContext
from aether_agent_memory.skill.models import SkillRecord


def _scope() -> Scope:
    return Scope(tenant_id="tenant-1", user_id="user-1", agent_id="agent-1")


@pytest.mark.unit
@pytest.mark.asyncio
async def test_skill_reader_exposes_agent_level_l0_l1_l2_context() -> None:
    skill = SkillRecord(
        skill_id="retrieval-review",
        name="Retrieval Review",
        description="Review recalled context for relevance.",
        instructions="Inspect the ranked context and remove unsupported claims.",
        scope=_scope(),
        version="2",
    )
    reader = SkillContextReader(InMemorySkillStore([skill]))

    root = await reader.get(skill_root_uri(_scope()))
    children = await reader.list_children(skill_root_uri(_scope()))
    item = await reader.get(skill_uri(_scope(), skill.skill_id))

    assert root is not None and root.kind == ContextItemKind.DIRECTORY
    assert children == [item]
    assert item is not None and item.kind == ContextItemKind.SKILL
    assert item.visibility.value == "agent"
    assert item.content_for(ContextLayer.ABSTRACT).text == skill.description
    assert item.content_for(ContextLayer.DETAIL).text == skill.instructions


@pytest.mark.unit
@pytest.mark.asyncio
async def test_skill_registration_rejects_mismatched_request_scope() -> None:
    store = InMemorySkillStore()
    context = RequestContext.from_values(
        tenant_id="tenant-1",
        user_id="user-1",
        agent_id="agent-2",
    )

    with pytest.raises(ScopeError, match="does not match"):
        await RegisterSkillUseCase(skills=store).execute(
            SkillRegistration(skill_id="private", name="Private", scope=_scope()),
            context,
        )

    assert await store.get(_scope(), "private") is None


@pytest.mark.unit
@pytest.mark.asyncio
async def test_skill_reader_does_not_leak_other_agent_skills() -> None:
    skill = SkillRecord(
        skill_id="private",
        name="Private",
        scope=Scope(tenant_id="tenant-1", user_id="user-1", agent_id="agent-2"),
    )
    reader = SkillContextReader(InMemorySkillStore([skill]))

    assert await reader.list_children(skill_root_uri(_scope())) == []


@pytest.mark.unit
@pytest.mark.asyncio
async def test_skill_root_is_mounted_from_a_session_context() -> None:
    session_scope = Scope(
        tenant_id="tenant-1",
        user_id="user-1",
        agent_id="agent-1",
        session_id="session-1",
    )
    reader = SkillContextReader(InMemorySkillStore())

    children = await reader.list_children(scope_uri(session_scope))

    assert len(children) == 1
    assert children[0].metadata["mounted"] is True
    assert children[0].metadata["mounted_from"] == str(scope_uri(session_scope))


@pytest.mark.unit
@pytest.mark.asyncio
async def test_skill_delete_removes_fact_and_derived_index_entry() -> None:
    skill = SkillRecord(
        skill_id="delete-skill",
        name="Delete skill",
        description="temporary skill",
        scope=_scope(),
    )
    store = InMemorySkillStore([skill])
    reader = SkillContextReader(store)
    item = await reader.get(skill_uri(_scope(), skill.skill_id))
    assert item is not None
    index = InMemorySemanticIndex()
    await index.index(item, item.layers[ContextLayer.OVERVIEW])
    context = RequestContext.from_values(
        tenant_id="tenant-1",
        user_id="user-1",
        agent_id="agent-1",
    )

    result = await DeleteSkillUseCase(
        skills=store,
        semantic_index=index,
    ).execute(skill.skill_id, context)

    assert result.deleted is True
    assert result.index_invalidated is True
    assert await store.get(_scope(), skill.skill_id) is None
