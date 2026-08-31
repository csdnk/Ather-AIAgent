from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import TypeAdapter, ValidationError

from aether_agent_memory.context_store import (
    AetherUri,
    ContextItemKind,
    ContextLayer,
    memory_to_context_item,
    memory_uri,
)
from aether_agent_memory.context_store.in_memory import (
    InMemoryContextCatalog,
    InMemoryContextContentStore,
    InMemorySemanticIndex,
)
from aether_agent_memory.context_store.mapping import context_directory_item
from aether_agent_memory.context_store.models import ContextSearchQuery
from aether_agent_memory.context_store.reindex import ReindexService
from aether_agent_memory.core.enums import MemoryType
from aether_agent_memory.core.memory import Memory
from aether_agent_memory.core.scope import Scope


@pytest.mark.unit
def test_aether_uri_is_canonical_and_hierarchical() -> None:
    root = AetherUri.from_segments("tenants", "公司 A", "agents", "agent-1")
    child = root.child("memories", "memory/with/slash".replace("/", "-"))

    assert str(root) == "aether://tenants/%E5%85%AC%E5%8F%B8%20A/agents/agent-1"
    assert child.parent == root.child("memories")
    assert child.is_within(root)
    assert child.namespace == "tenants"
    assert TypeAdapter(AetherUri).dump_python(root, mode="json") == str(root)


@pytest.mark.unit
@pytest.mark.parametrize(
    "value",
    [
        "p2://bucket/object",
        "aether://tenants/../secret",
        "aether://tenants/%2E%2E/secret",
        "aether://tenants//memory",
        "aether://tenants/item?query=true",
    ],
)
def test_aether_uri_rejects_provider_and_unsafe_paths(value: str) -> None:
    with pytest.raises(ValidationError):
        AetherUri.model_validate(value)


@pytest.mark.unit
def test_memory_maps_to_provider_neutral_context_item() -> None:
    created_at = datetime(2026, 8, 30, tzinfo=UTC)
    memory = Memory(
        id="memory-1",
        type=MemoryType.SEMANTIC,
        session_id="session-1",
        agent_id="agent-1",
        user_id="user-1",
        tenant_id="tenant-1",
        content="AetherStore keeps authoritative memory separate from derived indexes.",
        metadata={
            "overview": "A provider-neutral context database design.",
            "content_ref": "p2://p3-memory/object-1",
        },
        created_at=created_at,
        updated_at=created_at,
    )

    item = memory_to_context_item(memory)

    assert item.kind == ContextItemKind.MEMORY
    assert item.uri == memory_uri(memory)
    assert item.scope.tenant_id == "tenant-1"
    assert item.content_for(ContextLayer.ABSTRACT) is not None
    assert item.content_for(ContextLayer.OVERVIEW) is not None
    detail = item.content_for(ContextLayer.DETAIL)
    assert detail is not None
    assert detail.text == memory.content
    assert detail.content_ref == "p2://p3-memory/object-1"
    assert detail.derived is False
    assert item.metadata["memory_type"] == "semantic"


@pytest.mark.unit
def test_memory_context_identity_does_not_use_provider_location() -> None:
    memory = Memory(
        id="memory-2",
        type=MemoryType.WORKING,
        session_id="session-1",
        agent_id="agent-1",
        content="short memory",
        placement={
            "provider": "p2",
            "segment_id": "segment-1",
            "object_key": "object-1",
            "content_ref": "p2://bucket/object-1",
        },
    )

    item = memory_to_context_item(memory)

    assert str(item.uri).startswith("aether://")
    assert "p2" not in item.uri.segments
    assert item.content_for(ContextLayer.DETAIL).content_ref == "p2://bucket/object-1"


@pytest.mark.unit
def test_memory_without_explicit_overview_still_has_deterministic_l1() -> None:
    memory = Memory(
        id="memory-l1-default",
        type=MemoryType.SEMANTIC,
        session_id="session-1",
        agent_id="agent-1",
        content="A memory needs a stable overview for hierarchical retrieval.",
    )

    overview = memory_to_context_item(memory).content_for(ContextLayer.OVERVIEW)

    assert overview is not None
    assert overview.text == memory.content
    assert overview.generator == "deterministic-overview-v1"
    assert overview.derived is True


@pytest.mark.unit
def test_virtual_context_directory_has_deterministic_l0_and_l1() -> None:
    item = context_directory_item(
        AetherUri.from_segments("tenants", "tenant-1", "resources"),
        Scope(tenant_id="tenant-1", user_id="user-1", agent_id="agent-1"),
        "Resources",
        directory_type="resource_root",
    )

    assert item.layers[ContextLayer.ABSTRACT].text == "Resources"
    assert item.layers[ContextLayer.OVERVIEW].generator == "deterministic-directory-v1"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_derived_index_can_be_rebuilt_from_authoritative_context() -> None:
    memory = Memory(
        id="memory-3",
        type=MemoryType.SEMANTIC,
        session_id="session-1",
        agent_id="agent-1",
        user_id="user-1",
        tenant_id="tenant-1",
        content="AetherStore is a context database for AI agents.",
    )
    item = memory_to_context_item(memory)
    catalog = InMemoryContextCatalog()
    content_store = InMemoryContextContentStore()
    semantic_index = InMemorySemanticIndex()
    await catalog.upsert(item)
    for content in item.layers.values():
        await content_store.write(item.uri, content)

    report = await ReindexService(catalog, content_store, semantic_index).rebuild(
        item.uri,
        layers=(ContextLayer.ABSTRACT, ContextLayer.DETAIL),
    )
    result = await semantic_index.search(
        ContextSearchQuery(
            query="context database",
            scope=Scope(tenant_id="tenant-1", user_id="user-1", agent_id="agent-1"),
            root_uri=item.uri,
            layers=[ContextLayer.ABSTRACT, ContextLayer.DETAIL],
        )
    )

    assert report.scanned_items == 1
    assert report.indexed_layers == 2
    assert semantic_index.entry_count == 2
    assert result.hits
    assert all(hit.uri == item.uri for hit in result.hits)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_reindex_reports_provider_failures_without_aborting_other_layers() -> None:
    memory = Memory(
        id="memory-reindex-failure",
        type=MemoryType.SEMANTIC,
        session_id="session-1",
        agent_id="agent-1",
        user_id="user-1",
        tenant_id="tenant-1",
        content="reindex should preserve partial progress",
    )
    item = memory_to_context_item(memory)
    catalog = InMemoryContextCatalog()
    content_store = InMemoryContextContentStore()
    await catalog.upsert(item)
    for content in item.layers.values():
        await content_store.write(item.uri, content)

    class _PartiallyBrokenIndex(InMemorySemanticIndex):
        async def index(self, item, content) -> None:  # type: ignore[no-untyped-def]
            if content.layer == ContextLayer.OVERVIEW:
                raise RuntimeError("provider timeout")
            await super().index(item, content)

    index = _PartiallyBrokenIndex()
    report = await ReindexService(catalog, content_store, index).rebuild(
        item.uri,
        layers=(ContextLayer.ABSTRACT, ContextLayer.OVERVIEW),
    )

    assert report.indexed_layers == 1
    assert report.failed_layers == 1
    assert report.errors[f"{item.uri}#L1"] == "RuntimeError: provider timeout"
    assert index.entry_count == 1


@pytest.mark.unit
@pytest.mark.asyncio
async def test_reference_index_enforces_scope_and_uri_root() -> None:
    memory = Memory(
        id="memory-4",
        type=MemoryType.WORKING,
        session_id="session-1",
        agent_id="agent-1",
        user_id="user-1",
        tenant_id="tenant-1",
        content="private project context",
    )
    item = memory_to_context_item(memory)
    index = InMemorySemanticIndex()
    await index.index(item, item.layers[ContextLayer.ABSTRACT])

    wrong_scope = await index.search(
        ContextSearchQuery(
            query="project context",
            scope=Scope(tenant_id="tenant-2"),
            root_uri=AetherUri.from_segments("tenants", "tenant-1"),
        )
    )
    wrong_root = await index.search(
        ContextSearchQuery(
            query="project context",
            scope=Scope(tenant_id="tenant-1"),
            root_uri=AetherUri.from_segments("tenants", "tenant-2"),
        )
    )

    assert wrong_scope.hits == []
    assert wrong_root.hits == []


@pytest.mark.unit
@pytest.mark.asyncio
async def test_reindex_reports_bounded_traversal() -> None:
    root = AetherUri.from_segments("tenants", "tenant-1", "context")
    root_item = context_directory_item(
        root,
        Scope(tenant_id="tenant-1", user_id="user-1", agent_id="agent-1"),
        "Context",
        directory_type="test-root",
    )
    catalog = InMemoryContextCatalog()
    content_store = InMemoryContextContentStore()
    await catalog.upsert(root_item)
    for item_id in ("one", "two"):
        item = memory_to_context_item(
            Memory(
                id=item_id,
                type=MemoryType.SEMANTIC,
                tenant_id="tenant-1",
                user_id="user-1",
                agent_id="agent-1",
                session_id="session-1",
                content=f"bounded item {item_id}",
            )
        ).model_copy(update={"uri": root.child(item_id)})
        await catalog.upsert(item)
        for content in item.layers.values():
            await content_store.write(item.uri, content)

    service = ReindexService(
        catalog,
        content_store,
        InMemorySemanticIndex(),
        max_children_per_directory=1,
    )
    report = await service.rebuild(root, layers=(ContextLayer.ABSTRACT,))

    assert report.complete is False
    assert report.truncated_items == 1
    assert report.scanned_items == 1
    assert report.next_cursor is not None

    resumed = await service.rebuild(
        root,
        layers=(ContextLayer.ABSTRACT,),
        cursor=report.next_cursor,
    )
    assert resumed.complete is True
    assert resumed.next_cursor is None
    assert resumed.scanned_items == 2
