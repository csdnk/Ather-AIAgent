from __future__ import annotations

import pytest

from aether_agent_memory.adapters.context_projection_executor import (
    ProviderContextProjectionExecutor,
)
from aether_agent_memory.adapters.context_projection_queue import (
    InMemoryContextProjectionQueue,
)
from aether_agent_memory.adapters.resource_store import InMemoryResourceStore
from aether_agent_memory.adapters.session_store import InMemorySessionStore
from aether_agent_memory.adapters.skill_store import InMemorySkillStore
from aether_agent_memory.application.context_projection import ContextProjectionWorkService
from aether_agent_memory.application.resource import (
    DeleteResourceUseCase,
    RegisterResourceUseCase,
    ResourceRegistration,
)
from aether_agent_memory.application.skill import (
    DeleteSkillUseCase,
    RegisterSkillUseCase,
    SkillRegistration,
)
from aether_agent_memory.context_store import (
    ContextContent,
    ContextItem,
    ContextItemKind,
    ContextLayer,
    ContextProjectionWorkItem,
    ContextVisibility,
    agent_scope_uri,
    resource_uri,
    skill_uri,
)
from aether_agent_memory.context_store.in_memory import (
    InMemoryContextCatalog,
    InMemoryContextContentStore,
    InMemorySemanticIndex,
)
from aether_agent_memory.context_store.mapping import session_archive_uri
from aether_agent_memory.core.scope import Scope
from aether_agent_memory.runtime.request_context import RequestContext
from aether_agent_memory.session import SessionMessage, SessionMessageRole, SessionService


def _scope(*, session_id: str | None = None, agent_id: str = "agent-1") -> Scope:
    return Scope(
        tenant_id="tenant-1",
        user_id="user-1",
        agent_id=agent_id,
        session_id=session_id,
    )


def _context_item(scope: Scope) -> ContextItem:
    uri = agent_scope_uri(scope).child("skills").child("skill-1")
    return ContextItem(
        uri=uri,
        kind=ContextItemKind.SKILL,
        visibility=ContextVisibility.AGENT,
        scope=scope,
        title="Skill",
        layers={
            ContextLayer.ABSTRACT: ContextContent(
                layer=ContextLayer.ABSTRACT,
                text="review recalled context",
            ),
            ContextLayer.OVERVIEW: ContextContent(
                layer=ContextLayer.OVERVIEW,
                text="review recalled context in the current agent",
            ),
        },
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_context_projection_queue_is_revision_aware() -> None:
    queue = InMemoryContextProjectionQueue()
    item = ContextProjectionWorkItem(
        uri=skill_uri(_scope(), "skill-1"),
        source_revision=1,
        scope=_scope(),
    )

    first = await queue.enqueue(item)
    duplicate = await queue.enqueue(item.model_copy(update={"work_id": "other"}))
    newer = await queue.enqueue(
        item.model_copy(update={"work_id": "newer", "source_revision": 2})
    )

    assert duplicate.work_id == first.work_id
    assert newer.work_id != first.work_id
    assert len(await queue.pending()) == 2


@pytest.mark.unit
@pytest.mark.asyncio
async def test_context_projection_worker_reads_authoritative_content_and_indexes() -> None:
    scope = _scope()
    item = _context_item(scope)
    catalog = InMemoryContextCatalog()
    content = InMemoryContextContentStore()
    index = InMemorySemanticIndex()
    await catalog.upsert(item)
    for layer in (ContextLayer.ABSTRACT, ContextLayer.OVERVIEW):
        layer_content = item.content_for(layer)
        assert layer_content is not None
        await content.write(item.uri, layer_content)
    queue = InMemoryContextProjectionQueue()
    await queue.enqueue(
        ContextProjectionWorkItem(
            uri=item.uri,
            source_revision=item.source_revision,
            scope=scope,
        )
    )
    worker = ContextProjectionWorkService(
        queue,
        ProviderContextProjectionExecutor(
            catalog=catalog,
            content=content,
            semantic_index=index,
        ),
    )

    report = await worker.drain()

    assert report.succeeded == 1
    assert report.failed == 0
    assert index.entry_count == 2


@pytest.mark.unit
@pytest.mark.asyncio
async def test_context_projection_supersedes_old_revision_without_indexing() -> None:
    scope = _scope()
    item = _context_item(scope).model_copy(update={"source_revision": 2})
    catalog = InMemoryContextCatalog()
    content = InMemoryContextContentStore()
    index = InMemorySemanticIndex()
    await catalog.upsert(item)
    queue = InMemoryContextProjectionQueue()
    await queue.enqueue(
        ContextProjectionWorkItem(
            uri=item.uri,
            source_revision=1,
            scope=scope,
        )
    )

    worker = ContextProjectionWorkService(
        queue,
        ProviderContextProjectionExecutor(
            catalog=catalog,
            content=content,
            semantic_index=index,
        ),
    )
    report = await worker.drain()

    assert report.superseded == 1
    assert report.failed == 0
    assert report.retried == 0
    assert index.entry_count == 0
    assert await queue.pending() == []


@pytest.mark.unit
@pytest.mark.asyncio
async def test_context_projection_supersedes_fact_deleted_before_claim() -> None:
    scope = _scope()
    item = _context_item(scope)
    catalog = InMemoryContextCatalog()
    content = InMemoryContextContentStore()
    index = InMemorySemanticIndex()
    queue = InMemoryContextProjectionQueue()
    await queue.enqueue(
        ContextProjectionWorkItem(
            uri=item.uri,
            source_revision=item.source_revision,
            scope=scope,
        )
    )
    worker = ContextProjectionWorkService(
        queue,
        ProviderContextProjectionExecutor(
            catalog=catalog,
            content=content,
            semantic_index=index,
        ),
    )

    report = await worker.drain()

    assert report.superseded == 1
    assert report.failed == 0
    assert report.retried == 0
    assert index.entry_count == 0
    assert await queue.pending() == []


@pytest.mark.unit
@pytest.mark.asyncio
async def test_context_projection_invalidates_index_when_fact_is_deleted_during_write() -> None:
    scope = _scope()
    item = _context_item(scope)

    class _MutableCatalog:
        def __init__(self) -> None:
            self.item: ContextItem | None = item

        async def get(self, _uri) -> ContextItem | None:
            return self.item

        async def list_children(self, _parent, *, kind=None) -> list[ContextItem]:
            return []

    class _DeletingIndex(InMemorySemanticIndex):
        async def index(self, indexed_item, indexed_content) -> None:
            await super().index(indexed_item, indexed_content)
            catalog.item = None

    catalog = _MutableCatalog()
    content = InMemoryContextContentStore()
    layer_content = item.content_for(ContextLayer.ABSTRACT)
    assert layer_content is not None
    await content.write(item.uri, layer_content)
    index = _DeletingIndex()
    queue = InMemoryContextProjectionQueue()
    await queue.enqueue(
        ContextProjectionWorkItem(
            uri=item.uri,
            source_revision=item.source_revision,
            scope=scope,
            layers=(ContextLayer.ABSTRACT,),
        )
    )
    worker = ContextProjectionWorkService(
        queue,
        ProviderContextProjectionExecutor(
            catalog=catalog,
            content=content,
            semantic_index=index,
        ),
    )

    report = await worker.drain()

    assert report.superseded == 1
    assert report.failed == 0
    assert index.entry_count == 0
    assert await queue.pending() == []


@pytest.mark.unit
@pytest.mark.asyncio
async def test_context_projection_rejects_foreign_scope_before_indexing() -> None:
    item = _context_item(_scope())
    catalog = InMemoryContextCatalog()
    content = InMemoryContextContentStore()
    index = InMemorySemanticIndex()
    await catalog.upsert(item)
    layer_content = item.content_for(ContextLayer.ABSTRACT)
    assert layer_content is not None
    await content.write(item.uri, layer_content)
    queue = InMemoryContextProjectionQueue()
    await queue.enqueue(
        ContextProjectionWorkItem(
            uri=item.uri,
            source_revision=1,
            scope=_scope(agent_id="agent-2"),
        )
    )
    worker = ContextProjectionWorkService(
        queue,
        ProviderContextProjectionExecutor(
            catalog=catalog,
            content=content,
            semantic_index=index,
        ),
        max_attempts=1,
    )

    report = await worker.drain()

    assert report.failed == 1
    assert report.retried == 0
    assert index.entry_count == 0


@pytest.mark.unit
@pytest.mark.asyncio
async def test_resource_skill_and_session_writes_enqueue_context_projection() -> None:
    scope = _scope(session_id="session-1")
    context = RequestContext.from_values(
        request_id="request-1",
        trace_id="trace-1",
        tenant_id=scope.tenant_id,
        user_id=scope.user_id,
        agent_id=scope.agent_id,
        session_id=scope.session_id,
    )
    queue = InMemoryContextProjectionQueue()

    resources = InMemoryResourceStore()
    resource_use_case = RegisterResourceUseCase(
        resources=resources,
        context_projection_queue=queue,
    )
    await resource_use_case.execute(
        ResourceRegistration(
            resource_id="resource-1",
            name="Architecture notes",
            scope=scope,
            content="hierarchical context architecture",
        ),
        context,
    )

    skills = InMemorySkillStore()
    skill_use_case = RegisterSkillUseCase(
        skills=skills,
        context_projection_queue=queue,
    )
    await skill_use_case.execute(
        SkillRegistration(
            skill_id="skill-1",
            name="Context review",
            scope=scope,
            description="review context",
        ),
        context,
    )

    session_store = InMemorySessionStore()
    session = SessionService(session_store, context_projection_queue=queue)
    await session.append(
        scope,
        SessionMessage(role=SessionMessageRole.USER, content="persist this context"),
    )
    result = await session.commit(scope, keep_recent_count=0)
    assert result.archive_id is not None

    queued = await queue.pending()
    assert result.context_projection_status == "pending"
    assert {str(item.uri) for item in queued} == {
        str(resource_uri(scope, "resource-1")),
        str(skill_uri(scope, "skill-1")),
        str(session_archive_uri(scope, result.archive_id)),
    }


@pytest.mark.unit
@pytest.mark.asyncio
async def test_deleted_context_identity_rebuilds_with_a_new_projection_revision() -> None:
    scope = _scope()
    context = RequestContext.from_values(
        request_id="request-1",
        trace_id="trace-1",
        tenant_id=scope.tenant_id,
        user_id=scope.user_id,
        agent_id=scope.agent_id,
    )
    queue = InMemoryContextProjectionQueue()
    resources = InMemoryResourceStore()
    resource_registration = RegisterResourceUseCase(
        resources=resources,
        context_projection_queue=queue,
    )
    first_resource = await resource_registration.execute(
        ResourceRegistration(
            resource_id="resource-1",
            name="First",
            scope=scope,
            content="first content",
        ),
        context,
    )
    await DeleteResourceUseCase(resources=resources).execute("resource-1", context)
    second_resource = await resource_registration.execute(
        ResourceRegistration(
            resource_id="resource-1",
            name="Second",
            scope=scope,
            content="second content",
        ),
        context,
    )

    skills = InMemorySkillStore()
    skill_registration = RegisterSkillUseCase(
        skills=skills,
        context_projection_queue=queue,
    )
    first_skill = await skill_registration.execute(
        SkillRegistration(skill_id="skill-1", name="First", scope=scope),
        context,
    )
    await DeleteSkillUseCase(skills=skills).execute("skill-1", context)
    second_skill = await skill_registration.execute(
        SkillRegistration(skill_id="skill-1", name="Second", scope=scope),
        context,
    )

    assert (first_resource.revision, second_resource.revision) == (1, 2)
    assert (first_skill.revision, second_skill.revision) == (1, 2)
    queued = await queue.pending()
    assert sorted(item.source_revision for item in queued) == [1, 1, 2, 2]
