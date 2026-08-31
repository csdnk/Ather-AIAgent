from __future__ import annotations

import pytest

from aether_agent_memory.adapters.context_catalog import CompositeContextReader
from aether_agent_memory.adapters.context_store import MemoryStoreContextReader
from aether_agent_memory.adapters.session_context import SessionContextReader
from aether_agent_memory.adapters.session_store import InMemorySessionStore
from aether_agent_memory.context_store import (
    ContextItemKind,
    ContextLayer,
    memory_root_uri,
    resource_root_uri,
    scope_uri,
    session_archive_uri,
    session_history_root_uri,
)
from aether_agent_memory.core.scope import Scope
from aether_agent_memory.persistence.memory_store import InMemoryMemoryStore
from aether_agent_memory.session import SessionMessage, SessionMessageRole, SessionService


def _scope() -> Scope:
    return Scope(
        tenant_id="tenant-1",
        user_id="user-1",
        agent_id="agent-1",
        session_id="session-1",
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_composite_catalog_exposes_memory_resource_and_session_roots() -> None:
    session_store = InMemorySessionStore()
    memory_reader = MemoryStoreContextReader(InMemoryMemoryStore())
    session_reader = SessionContextReader(session_store)
    catalog = CompositeContextReader(
        [memory_reader, session_reader],
        [memory_reader, session_reader],
    )

    children = await catalog.list_children(scope_uri(_scope()))

    assert [item.uri for item in children] == sorted(
        [
            memory_root_uri(_scope()),
            resource_root_uri(_scope()),
            session_history_root_uri(_scope()),
        ],
        key=str,
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_session_archive_is_readable_as_l0_l1_l2_context() -> None:
    store = InMemorySessionStore()
    service = SessionService(store)
    reader = SessionContextReader(store)
    for content in ("design the context database", "use hierarchical retrieval"):
        await service.append(
            _scope(),
            SessionMessage(role=SessionMessageRole.USER, content=content),
        )
    result = await service.commit(_scope(), keep_recent_count=0)
    assert result.archive_id is not None
    uri = session_archive_uri(_scope(), result.archive_id)

    item = await reader.get(uri)
    abstract = await reader.read(uri, ContextLayer.ABSTRACT)
    overview = await reader.read(uri, ContextLayer.OVERVIEW)
    detail = await reader.read(uri, ContextLayer.DETAIL)

    assert item is not None and item.kind == ContextItemKind.SESSION
    assert abstract is not None and "context database" in (abstract.text or "")
    assert overview is not None and "hierarchical retrieval" in (overview.text or "")
    assert detail is not None and "user: design" in (detail.text or "")
    assert item.metadata["memory_extraction_status"] == "not_implemented"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_archive_projection_revision_survives_worker_status_updates() -> None:
    store = InMemorySessionStore()
    service = SessionService(store)
    await service.append(
        _scope(),
        SessionMessage(role=SessionMessageRole.USER, content="stable archive"),
    )
    result = await service.commit(_scope(), keep_recent_count=0)
    assert result.archive_id is not None

    record = await store.get(_scope())
    assert record is not None
    archive = record.archives[0]
    assert archive.source_revision == result.revision

    await service.set_archive_extraction_status(
        _scope(), result.archive_id, "processing"
    )
    updated = await store.get(_scope())
    assert updated is not None
    assert updated.revision == result.revision + 1
    assert updated.archives[0].source_revision == archive.source_revision

    item = await SessionContextReader(store).get(
        session_archive_uri(_scope(), result.archive_id)
    )
    assert item is not None
    assert item.source_revision == archive.source_revision
