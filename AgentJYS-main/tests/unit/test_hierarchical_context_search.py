from __future__ import annotations

import pytest

from aether_agent_memory.adapters.context_store import MemoryStoreContextReader
from aether_agent_memory.application import SearchContextUseCase
from aether_agent_memory.context_store import (
    AetherUri,
    ContextItemKind,
    ContextLayer,
    ContextSearchHit,
    ContextSearchQuery,
    ContextSearchResult,
    ContextVisibility,
    HierarchicalContextSearchService,
    HierarchicalSearchPolicy,
    agent_scope_uri,
    resource_root_uri,
    scope_uri,
)
from aether_agent_memory.context_store.in_memory import (
    InMemoryRetrievalTraceStore,
    InMemorySemanticIndex,
)
from aether_agent_memory.context_store.mapping import memory_to_context_item
from aether_agent_memory.core.enums import MemoryType, SourceType
from aether_agent_memory.core.memory import Memory
from aether_agent_memory.core.scope import Scope
from aether_agent_memory.persistence.memory_store import InMemoryMemoryStore
from aether_agent_memory.runtime.dependencies import RuntimeDependencies
from aether_agent_memory.runtime.errors import ScopeError
from aether_agent_memory.runtime.request_context import RequestContext


def _context(*, tenant_id: str = "tenant-1") -> RequestContext:
    return RequestContext.from_values(
        tenant_id=tenant_id,
        user_id="user-1",
        agent_id="agent-1",
        session_id="session-1",
    )


def _scope() -> Scope:
    return _context().scope


def _memory(memory_id: str, content: str, *, document: bool = False) -> Memory:
    return Memory(
        id=memory_id,
        type=MemoryType.EPISODIC if document else MemoryType.SEMANTIC,
        tenant_id="tenant-1",
        user_id="user-1",
        agent_id="agent-1",
        session_id="session-1",
        content=content,
        source=SourceType.DOCUMENT if document else SourceType.USER,
        content_ref="p2://p3-memory/document" if document else None,
        tags=["document"] if document else [],
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_hierarchical_search_traverses_memory_and_resource_directories() -> None:
    store = InMemoryMemoryStore()
    await store.upsert(_memory("memory-1", "user prefers deterministic systems"))
    await store.upsert(
        _memory("document-1", "AetherStore architecture and deployment", document=True)
    )
    reader = MemoryStoreContextReader(store)
    search = HierarchicalContextSearchService(reader, reader)

    result = await search.search(
        ContextSearchQuery(
            query="AetherStore architecture",
            scope=_scope(),
            root_uri=scope_uri(_scope()),
            kinds=[ContextItemKind.RESOURCE],
            layers=[ContextLayer.ABSTRACT],
        )
    )

    assert [hit.kind for hit in result.hits] == [ContextItemKind.RESOURCE]
    assert result.hits[0].uri.is_within(resource_root_uri(_scope()))
    assert result.hits[0].score == pytest.approx(1.0)
    assert result.complete is True
    assert result.metadata["expanded_directories"] >= 3


@pytest.mark.unit
@pytest.mark.asyncio
async def test_hierarchical_search_reports_bounded_traversal() -> None:
    store = InMemoryMemoryStore()
    for index in range(5):
        await store.upsert(_memory(f"memory-{index}", f"bounded search item {index}"))
    reader = MemoryStoreContextReader(store)
    search = HierarchicalContextSearchService(
        reader,
        reader,
        policy=HierarchicalSearchPolicy(max_visited_nodes=2),
    )

    result = await search.search(
        ContextSearchQuery(
            query="bounded",
            scope=_scope(),
            root_uri=scope_uri(_scope()),
        )
    )

    assert result.complete is False
    assert result.missing_sources == ["catalog_truncated"]
    assert result.metadata["pending_directories"] > 0


@pytest.mark.unit
@pytest.mark.asyncio
async def test_hierarchical_search_merges_optional_semantic_index_hits() -> None:
    store = InMemoryMemoryStore()
    memory = _memory("memory-semantic", "ordinary deployment notes").model_copy(
        update={"revision": 2}
    )
    await store.upsert(memory)
    item = memory_to_context_item(memory)

    class _SemanticIndex:
        async def index(self, item, content) -> None:  # type: ignore[no-untyped-def]
            return None

        async def remove(self, uri: AetherUri) -> None:
            return None

        async def search(self, query) -> ContextSearchResult:  # type: ignore[no-untyped-def]
            return ContextSearchResult(
                hits=[
                    ContextSearchHit(
                        uri=item.uri,
                        kind=item.kind,
                        layer=ContextLayer.OVERVIEW,
                        score=0.91,
                        text="semantic match",
                        source="test-semantic-index",
                        source_revision=item.source_revision,
                    ),
                    ContextSearchHit(
                        uri=item.uri,
                        kind=item.kind,
                        layer=ContextLayer.OVERVIEW,
                        score=0.99,
                        text="stale semantic match",
                        source="test-semantic-index",
                        source_revision=1,
                    ),
                    ContextSearchHit(
                        uri=AetherUri.from_segments(
                            "tenants",
                            "tenant-2",
                            "users",
                            "user-1",
                            "agents",
                            "agent-1",
                            "memories",
                            "semantic",
                            "foreign",
                        ),
                        kind=ContextItemKind.MEMORY,
                        layer=ContextLayer.OVERVIEW,
                        score=1.0,
                        text="foreign match",
                        source="test-semantic-index",
                    ),
                    ContextSearchHit(
                        uri=agent_scope_uri(_scope()).child(
                            "memories", "semantic", "stale"
                        ),
                        kind=ContextItemKind.MEMORY,
                        layer=ContextLayer.OVERVIEW,
                        score=0.99,
                        text="stale match",
                        source="test-semantic-index",
                    ),
                ],
                backend="test-semantic",
            )

    reader = MemoryStoreContextReader(store)
    search = HierarchicalContextSearchService(
        reader,
        reader,
        semantic_index=_SemanticIndex(),  # type: ignore[arg-type]
    )

    result = await search.search(
        ContextSearchQuery(
            query="query with no lexical overlap",
            scope=_scope(),
            root_uri=agent_scope_uri(_scope()),
        )
    )

    assert result.backend == "catalog-hierarchical+semantic-v1"
    assert result.metadata["semantic_index_hits"] == 1
    assert len(result.hits) == 1
    assert result.hits[0].source == "test-semantic-index"
    assert result.hits[0].text == "ordinary deployment notes"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_hierarchical_search_keeps_catalog_available_when_semantic_index_fails() -> None:
    store = InMemoryMemoryStore()
    await store.upsert(_memory("memory-fallback", "fallback deployment notes"))
    reader = MemoryStoreContextReader(store)

    class _BrokenSemanticIndex(InMemorySemanticIndex):
        async def search(self, query: ContextSearchQuery) -> ContextSearchResult:
            raise RuntimeError("index unavailable")

    result = await HierarchicalContextSearchService(
        reader,
        reader,
        semantic_index=_BrokenSemanticIndex(),
    ).search(
        ContextSearchQuery(
            query="fallback",
            scope=_scope(),
            root_uri=scope_uri(_scope()),
        )
    )

    assert result.hits
    assert result.complete is False
    assert result.missing_sources == ["semantic_index"]
    assert result.metadata["semantic_index_error"] == "RuntimeError: index unavailable"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_hierarchical_search_persists_a_trace_for_catalog_and_semantic_sources() -> None:
    store = InMemoryMemoryStore()
    await store.upsert(_memory("memory-traced", "traceable deployment notes"))
    reader = MemoryStoreContextReader(store)
    traces = InMemoryRetrievalTraceStore()
    search = HierarchicalContextSearchService(reader, reader, trace_store=traces)

    result = await search.search(
        ContextSearchQuery(
            query="traceable",
            scope=_scope(),
            root_uri=scope_uri(_scope()),
            trace_id="trace-catalog-search",
        )
    )

    trace = await traces.get("trace-catalog-search")
    assert result.metadata["trace_id"] == "trace-catalog-search"
    assert trace is not None
    assert trace.complete is True
    assert trace.finished_at is not None
    assert {step.source for step in trace.steps} == {"catalog-hierarchical"}
    assert any(
        step.action.value == "context_selected" for step in trace.steps
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_hierarchical_search_filters_invisible_catalog_children() -> None:
    root = scope_uri(_scope())
    foreign_scope = Scope(
        tenant_id="tenant-1",
        user_id="user-2",
        agent_id="agent-1",
        session_id="session-2",
    )
    foreign_item = memory_to_context_item(
        Memory(
            id="foreign-catalog-item",
            type=MemoryType.SEMANTIC,
            tenant_id=foreign_scope.tenant_id,
            user_id=foreign_scope.user_id,
            agent_id=foreign_scope.agent_id,
            session_id=foreign_scope.session_id,
            content="secret catalog content",
        )
    ).model_copy(
        update={
            "uri": root.child("memories", "semantic", "foreign-catalog-item"),
            "visibility": ContextVisibility.AGENT,
        }
    )

    class _Catalog:
        async def get(self, uri):
            return foreign_item if uri == foreign_item.uri else None

        async def list_children(self, parent, *, kind=None):
            return [foreign_item] if parent == root else []

    class _Content:
        async def read(self, uri, layer):
            return foreign_item.content_for(layer)

    result = await HierarchicalContextSearchService(_Catalog(), _Content()).search(
        ContextSearchQuery(
            query="secret",
            scope=_scope(),
            root_uri=root,
            kinds=[ContextItemKind.MEMORY],
        )
    )

    assert result.hits == []


@pytest.mark.unit
@pytest.mark.asyncio
async def test_application_search_uses_authenticated_scope_and_rejects_foreign_root() -> None:
    reader = MemoryStoreContextReader(InMemoryMemoryStore())
    dependencies = RuntimeDependencies(
        embedding=None,  # type: ignore[arg-type]
        memory_events=None,  # type: ignore[arg-type]
        context_builder=None,  # type: ignore[arg-type]
        context_search=HierarchicalContextSearchService(reader, reader),
    )
    use_case = SearchContextUseCase(dependencies)

    with pytest.raises(ScopeError):
        await use_case.execute(
            ContextSearchQuery(
                query="secret",
                scope=_scope(),
                root_uri=scope_uri(
                    Scope(
                        tenant_id="tenant-2",
                        user_id="user-1",
                        agent_id="agent-1",
                        session_id="session-1",
                    )
                ),
            ),
            _context(),
        )
