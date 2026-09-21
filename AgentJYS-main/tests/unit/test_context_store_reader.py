from __future__ import annotations

import pytest

from aether_agent_memory.adapters.context_store import MemoryStoreContextReader
from aether_agent_memory.application import (
    GetContextItemUseCase,
    ListContextChildrenUseCase,
)
from aether_agent_memory.b2 import MemoryEvent, MemoryEventType
from aether_agent_memory.bootstrap.container import build_runtime
from aether_agent_memory.config.app_settings import AppSettings
from aether_agent_memory.context_store import (
    ContextItemKind,
    ContextLayer,
    agent_memory_root_uri,
    memory_collection_uri,
    memory_root_uri,
    memory_uri,
    resource_collection_uri,
    resource_root_uri,
    resource_uri,
    scope_uri,
)
from aether_agent_memory.core.enums import MemoryType, SourceType
from aether_agent_memory.core.memory import Memory
from aether_agent_memory.persistence.memory_store import InMemoryMemoryStore
from aether_agent_memory.runtime.dependencies import RuntimeDependencies
from aether_agent_memory.runtime.errors import ScopeError
from aether_agent_memory.runtime.request_context import RequestContext


def _memory(*, memory_id: str = "memory-1", tenant_id: str = "tenant-1") -> Memory:
    return Memory(
        id=memory_id,
        type=MemoryType.SEMANTIC,
        tenant_id=tenant_id,
        user_id="user-1",
        agent_id="agent-1",
        session_id="session-1",
        content=f"content for {memory_id}",
    )


def _context(*, tenant_id: str = "tenant-1") -> RequestContext:
    return RequestContext.from_values(
        tenant_id=tenant_id,
        user_id="user-1",
        agent_id="agent-1",
        session_id="session-1",
    )


def _context_for_session(session_id: str) -> RequestContext:
    return RequestContext.from_values(
        tenant_id="tenant-1",
        user_id="user-1",
        agent_id="agent-1",
        session_id=session_id,
    )


def _document_memory(*, memory_id: str = "document-1") -> Memory:
    return Memory(
        id=memory_id,
        type=MemoryType.EPISODIC,
        tenant_id="tenant-1",
        user_id="user-1",
        agent_id="agent-1",
        session_id="session-1",
        task_id="task-1",
        source_id="architecture.pdf",
        object_id="b2/long-text/architecture.pdf",
        content="AetherStore architecture document body",
        source=SourceType.DOCUMENT,
        tags=["long_text", "document"],
        metadata={
            "content_ref": "p2://p3-memory/b2/long-text/architecture.pdf",
            "pipeline_status": "succeeded",
            "ingest_path": "celery-long-text",
        },
    )


def _dependencies(reader: MemoryStoreContextReader) -> RuntimeDependencies:
    return RuntimeDependencies(
        embedding=None,  # type: ignore[arg-type]
        memory_events=None,  # type: ignore[arg-type]
        context_builder=None,  # type: ignore[arg-type]
        context_catalog=reader,
        context_content=reader,
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_memory_store_reader_maps_authoritative_memory_without_dual_write() -> None:
    store = InMemoryMemoryStore()
    memory = _memory()
    await store.upsert(memory)
    reader = MemoryStoreContextReader(store)

    item = await reader.get(memory_uri(memory))
    detail = await reader.read(memory_uri(memory), ContextLayer.DETAIL)

    assert item is not None
    assert item.kind == ContextItemKind.MEMORY
    assert item.metadata["memory_id"] == memory.id
    assert detail is not None
    assert detail.text == memory.content


@pytest.mark.unit
@pytest.mark.asyncio
async def test_memory_collection_lists_only_exact_scope_and_type() -> None:
    store = InMemoryMemoryStore()
    expected = _memory(memory_id="expected")
    other_tenant = _memory(memory_id="other-tenant", tenant_id="tenant-2")
    working = _memory(memory_id="working").model_copy(
        update={"type": MemoryType.WORKING}
    )
    for memory in (expected, other_tenant, working):
        await store.upsert(memory)
    reader = MemoryStoreContextReader(store)
    parent = memory_collection_uri(_context().scope, MemoryType.SEMANTIC)

    items = await reader.list_children(parent)

    assert [item.metadata["memory_id"] for item in items] == ["expected"]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_agent_memory_is_visible_across_sessions_but_working_isolated() -> None:
    store = InMemoryMemoryStore()
    semantic = _memory(memory_id="semantic-old")
    working = _memory(memory_id="working-old").model_copy(
        update={"type": MemoryType.WORKING}
    )
    await store.upsert(semantic)
    await store.upsert(working)
    reader = MemoryStoreContextReader(store)
    current = _context_for_session("session-new")

    semantic_parent = memory_collection_uri(current.scope, MemoryType.SEMANTIC)
    working_parent = memory_collection_uri(current.scope, MemoryType.WORKING)
    semantic_items = await reader.list_children(semantic_parent)
    working_items = await reader.list_children(working_parent)

    assert semantic_parent.segments[6:] == ("memories", "semantic")
    assert "sessions" not in semantic_parent.segments
    assert [item.metadata["memory_id"] for item in semantic_items] == ["semantic-old"]
    assert working_items == []
    assert "sessions" in working_parent.segments


@pytest.mark.unit
@pytest.mark.asyncio
async def test_reader_exposes_memory_as_a_browsable_virtual_tree() -> None:
    store = InMemoryMemoryStore()
    memory = _memory()
    await store.upsert(memory)
    reader = MemoryStoreContextReader(store)
    scope = _context().scope

    scope_children = await reader.list_children(scope_uri(scope))
    memory_type_directories = await reader.list_children(memory_root_uri(scope))
    semantic_memories = await reader.list_children(
        memory_collection_uri(scope, MemoryType.SEMANTIC)
    )

    assert [item.uri for item in scope_children] == [
        memory_root_uri(scope),
        resource_root_uri(scope),
    ]
    assert all(item.kind == ContextItemKind.DIRECTORY for item in scope_children)
    assert [item.metadata["directory_type"] for item in memory_type_directories] == [
        "memory_collection",
        "memory_collection",
        "memory_collection",
    ]
    assert [item.uri.segments[-1] for item in memory_type_directories] == [
        "working",
        "episodic",
        "semantic",
    ]
    assert [item.metadata["memory_id"] for item in semantic_memories] == [memory.id]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_reader_gets_virtual_directory_nodes_without_storing_them() -> None:
    reader = MemoryStoreContextReader(InMemoryMemoryStore())
    scope = _context().scope

    root = await reader.get(scope_uri(scope))
    memories = await reader.get(memory_root_uri(scope))
    semantic = await reader.get(memory_collection_uri(scope, MemoryType.SEMANTIC))

    assert root is not None and root.metadata["directory_type"] == "scope_root"
    assert memories is not None and memories.metadata["directory_type"] == "memory_root"
    assert semantic is not None and semantic.kind == ContextItemKind.DIRECTORY


@pytest.mark.unit
@pytest.mark.asyncio
async def test_agent_root_is_a_valid_context_search_root() -> None:
    reader = MemoryStoreContextReader(InMemoryMemoryStore())
    scope = _context().scope

    root = await reader.get(agent_memory_root_uri(scope).parent)
    children = await reader.list_children(agent_memory_root_uri(scope).parent)
    memory_root = await reader.get(agent_memory_root_uri(scope))

    assert root is not None and root.metadata["directory_type"] == "agent_root"
    assert [item.uri for item in children] == [
        agent_memory_root_uri(scope),
        resource_root_uri(scope),
    ]
    assert memory_root is not None
    assert memory_root.metadata["directory_type"] == "agent_memory_root"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_reader_projects_long_document_as_resource_without_copying_l2() -> None:
    store = InMemoryMemoryStore()
    document = _document_memory()
    await store.upsert(document)
    reader = MemoryStoreContextReader(store)
    scope = _context().scope

    roots = await reader.list_children(resource_root_uri(scope))
    resources = await reader.list_children(resource_collection_uri(scope))
    item = await reader.get(resource_uri(scope, document.id))
    detail = await reader.read(resource_uri(scope, document.id), ContextLayer.DETAIL)

    assert [root.uri for root in roots] == [resource_collection_uri(scope)]
    assert [resource.kind for resource in resources] == [ContextItemKind.RESOURCE]
    assert item is not None
    assert item.metadata["source_memory_id"] == document.id
    assert item.metadata["content_authority"] == "p2-object"
    assert detail is not None
    assert detail.text is None
    assert detail.content_ref == "p2://p3-memory/b2/long-text/architecture.pdf"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_reindex_can_traverse_from_scope_root() -> None:
    from aether_agent_memory.context_store.in_memory import InMemorySemanticIndex
    from aether_agent_memory.context_store.reindex import ReindexService

    store = InMemoryMemoryStore()
    memory = _memory()
    await store.upsert(memory)
    reader = MemoryStoreContextReader(store)
    index = InMemorySemanticIndex()

    report = await ReindexService(reader, reader, index).rebuild(
        scope_uri(_context().scope),
        layers=(ContextLayer.ABSTRACT, ContextLayer.DETAIL),
    )

    assert report.scanned_items == 8
    assert report.indexed_layers == 2
    assert index.entry_count == 2


@pytest.mark.unit
@pytest.mark.asyncio
async def test_reader_rejects_forged_uri_for_existing_memory() -> None:
    store = InMemoryMemoryStore()
    memory = _memory()
    await store.upsert(memory)
    reader = MemoryStoreContextReader(store)
    forged = memory_uri(memory).model_copy(
        update={
            "root": str(memory_uri(memory)).replace("tenant-1", "tenant-2")
        }
    )

    assert await reader.get(forged) is None


@pytest.mark.unit
@pytest.mark.asyncio
async def test_application_catalog_use_cases_enforce_scope() -> None:
    store = InMemoryMemoryStore()
    memory = _memory()
    await store.upsert(memory)
    reader = MemoryStoreContextReader(store)
    dependencies = _dependencies(reader)

    item = await GetContextItemUseCase(dependencies).execute(
        memory_uri(memory),
        _context(),
    )
    items = await ListContextChildrenUseCase(dependencies).execute(
        memory_collection_uri(_context().scope, MemoryType.SEMANTIC),
        _context(),
    )

    assert item is not None
    assert len(items) == 1
    with pytest.raises(ScopeError):
        await GetContextItemUseCase(dependencies).execute(
            memory_uri(memory),
            _context(tenant_id="tenant-2"),
        )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_composition_root_wires_context_reader_to_authoritative_store(tmp_path) -> None:
    runtime = build_runtime(
        AppSettings(
            profile="demo",
            memory_store="sqlite",
            data_dir=str(tmp_path),
        )
    )
    context = _context()
    try:
        memory = await runtime.write_memory(
            MemoryEvent(
                event_type=MemoryEventType.AFTER_TURN,
                tenant_id=context.tenant_id,
                user_id=context.user_id,
                agent_id=context.agent_id or "",
                session_id=context.session_id or "",
                content="runtime context catalog",
            ),
            context,
        )

        item = await runtime.get_context_item(memory_uri(memory), context)
        children = await runtime.list_context_children(
            memory_collection_uri(context.scope, memory.type),
            context,
        )

        assert item is not None
        assert item.metadata["memory_id"] == memory.id
        assert [child.metadata["memory_id"] for child in children] == [memory.id]
    finally:
        await runtime.close()
