from __future__ import annotations

import pytest

from aether_agent_memory.adapters.context_catalog import CompositeContextReader
from aether_agent_memory.adapters.context_store import MemoryStoreContextReader
from aether_agent_memory.adapters.resource_context import ResourceContextReader
from aether_agent_memory.adapters.resource_store import InMemoryResourceStore
from aether_agent_memory.adapters.session_context import SessionContextReader
from aether_agent_memory.adapters.session_store import InMemorySessionStore
from aether_agent_memory.adapters.skill_context import SkillContextReader
from aether_agent_memory.adapters.skill_store import InMemorySkillStore
from aether_agent_memory.context import ContextRequest
from aether_agent_memory.context_store import (
    ContextItemKind,
    ContextLayer,
    ContextSearchHit,
    ContextSearchQuery,
    ContextSearchResult,
    HierarchicalContextSearchService,
    agent_scope_uri,
    scope_uri,
)
from aether_agent_memory.memory.retrieval import (
    ContextCatalogRecallSource,
    ContextRetrievalService,
)
from aether_agent_memory.persistence.memory_store import InMemoryMemoryStore
from aether_agent_memory.resource.models import ResourceRecord
from aether_agent_memory.runtime.request_context import RequestContext
from aether_agent_memory.session import SessionMessage, SessionMessageRole, SessionService
from aether_agent_memory.skill.models import SkillRecord


def _context() -> RequestContext:
    return RequestContext.from_values(
        request_id="request-1",
        trace_id="trace-1",
        tenant_id="tenant-1",
        user_id="user-1",
        agent_id="agent-1",
        session_id="session-1",
    )


def _request() -> ContextRequest:
    return ContextRequest(
        tenant_id="tenant-1",
        user_id="user-1",
        agent_id="agent-1",
        session_id="session-1",
        query="deployment",
    )


class _CatalogSearch:
    def __init__(self) -> None:
        self.queries: list[ContextSearchQuery] = []

    async def search(self, query: ContextSearchQuery) -> ContextSearchResult:
        self.queries.append(query)
        root = agent_scope_uri(query.scope)
        hits = [
            ContextSearchHit(
                uri=root.child("resources", "documents", "resource-1"),
                kind=ContextItemKind.RESOURCE,
                layer=ContextLayer.OVERVIEW,
                score=0.92,
                text="deployment resource",
                source="catalog-hierarchical",
                source_revision=2,
            ),
            ContextSearchHit(
                uri=root.child("skills", "skill-1"),
                kind=ContextItemKind.SKILL,
                layer=ContextLayer.ABSTRACT,
                score=0.88,
                text="deployment skill",
                source="semantic-index",
                source_revision=1,
            ),
            ContextSearchHit(
                uri=root.child("sessions", "session-1", "history", "archive-1"),
                kind=ContextItemKind.SESSION,
                layer=ContextLayer.OVERVIEW,
                score=0.81,
                text="deployment session",
                source="catalog-hierarchical",
                source_revision=3,
            ),
        ]
        return ContextSearchResult(
            hits=[
                hit
                for hit in hits
                if hit.kind in query.kinds
                and query.root_uri is not None
                and hit.uri.is_within(query.root_uri)
            ],
            backend="catalog-hierarchical+semantic-v1",
            complete=False,
            missing_sources=["semantic_index"],
            metadata={"semantic_index_error": "provider unavailable"},
        )


@pytest.mark.asyncio
async def test_catalog_source_feeds_resource_skill_and_session_into_main_recall() -> None:
    search = _CatalogSearch()
    result = await ContextRetrievalService(
        [ContextCatalogRecallSource(search)]
    ).recall(_request(), _context())

    assert {candidate.context_kind for candidate in result.candidates} == {
        ContextItemKind.RESOURCE,
        ContextItemKind.SKILL,
        ContextItemKind.SESSION,
    }
    assert result.complete is False
    assert result.missing_sources == ["semantic_index"]
    assert result.degraded_reasons == {"semantic_index": "provider unavailable"}
    assert search.queries[0].root_uri == agent_scope_uri(_context().scope)
    assert search.queries[0].trace_id == "trace-1:catalog:agent"
    assert set(search.queries[0].kinds) == {
        ContextItemKind.RESOURCE,
        ContextItemKind.SKILL,
    }
    assert search.queries[1].root_uri == scope_uri(_context().scope)
    assert search.queries[1].trace_id == "trace-1:catalog:session"
    assert search.queries[1].kinds == [ContextItemKind.SESSION]


@pytest.mark.asyncio
async def test_real_context_stores_feed_the_main_recall_pipeline() -> None:
    context = _context()
    scope = context.scope
    resource_reader = ResourceContextReader(
        InMemoryResourceStore(
            [
                ResourceRecord(
                    resource_id="resource-1",
                    name="Deployment resource",
                    description="deployment architecture resource",
                    content="deployment details",
                    scope=scope,
                )
            ]
        )
    )
    skill_reader = SkillContextReader(
        InMemorySkillStore(
            [
                SkillRecord(
                    skill_id="skill-1",
                    name="Deployment skill",
                    description="deployment verification skill",
                    instructions="verify deployment",
                    scope=scope,
                )
            ]
        )
    )
    session_store = InMemorySessionStore()
    sessions = SessionService(session_store)
    await sessions.append(
        scope,
        SessionMessage(
            role=SessionMessageRole.USER,
            content="review the deployment history",
        ),
    )
    await sessions.commit(scope, keep_recent_count=0)
    session_reader = SessionContextReader(session_store)
    memory_reader = MemoryStoreContextReader(InMemoryMemoryStore())
    reader = CompositeContextReader(
        [memory_reader, resource_reader, session_reader, skill_reader],
        [memory_reader, resource_reader, session_reader, skill_reader],
    )
    service = ContextRetrievalService(
        [
            ContextCatalogRecallSource(
                HierarchicalContextSearchService(reader, reader)
            )
        ]
    )

    result = await service.recall(_request(), context)

    assert {candidate.context_kind for candidate in result.candidates} == {
        ContextItemKind.RESOURCE,
        ContextItemKind.SKILL,
        ContextItemKind.SESSION,
    }
    assert result.complete is True
