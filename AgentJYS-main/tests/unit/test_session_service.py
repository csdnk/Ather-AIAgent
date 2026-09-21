from __future__ import annotations

import pytest

from aether_agent_memory.adapters.session_extraction_queue import (
    InMemorySessionExtractionQueue,
)
from aether_agent_memory.adapters.session_store import InMemorySessionStore
from aether_agent_memory.core.scope import Scope
from aether_agent_memory.session import (
    SessionExtractionWorkItem,
    SessionMessage,
    SessionMessageRole,
    SessionService,
)


def _scope() -> Scope:
    return Scope(
        tenant_id="tenant-1",
        user_id="user-1",
        agent_id="agent-1",
        session_id="session-1",
    )


def _message(index: int) -> SessionMessage:
    return SessionMessage(
        role=SessionMessageRole.USER if index % 2 == 0 else SessionMessageRole.ASSISTANT,
        content=f"session message {index}",
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_session_append_and_commit_archive_with_l0_l1() -> None:
    service = SessionService(InMemorySessionStore())
    for index in range(4):
        await service.append(_scope(), _message(index))

    result = await service.commit(_scope(), keep_recent_count=1)
    record = await service.get(_scope())

    assert result.status == "committed"
    assert result.archived_messages == 3
    assert result.retained_messages == 1
    assert result.memory_extraction_status == "not_implemented"
    assert record is not None
    assert record.revision == 5
    assert len(record.messages) == 1
    assert len(record.archives) == 1
    assert "session message 0" in record.archives[0].abstract
    assert "user: session message 0" in record.archives[0].overview
    assert record.archives[0].extraction_status == "not_implemented"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_session_commit_is_noop_inside_keep_recent_window() -> None:
    service = SessionService(InMemorySessionStore())
    await service.append(_scope(), _message(0))

    result = await service.commit(_scope(), keep_recent_count=5)

    assert result.status == "skipped"
    assert result.archived_messages == 0
    assert result.revision == 1


@pytest.mark.unit
@pytest.mark.asyncio
async def test_session_store_rejects_stale_revision() -> None:
    store = InMemorySessionStore()
    service = SessionService(store)
    record = await service.append(_scope(), _message(0))

    stale = record.model_copy(update={"revision": record.revision + 1})

    assert await store.save(stale, expected_revision=0) is False


@pytest.mark.unit
@pytest.mark.asyncio
async def test_commit_enqueues_archive_extraction_after_cas() -> None:
    queue = InMemorySessionExtractionQueue()
    service = SessionService(InMemorySessionStore(), extraction_queue=queue)
    await service.append(_scope(), _message(0))

    result = await service.commit(_scope(), keep_recent_count=0)
    pending = await queue.pending()
    record = await service.get(_scope())

    assert result.memory_extraction_status == "pending"
    assert result.archive_id is not None
    assert len(pending) == 1
    assert pending[0].archive_id == result.archive_id
    assert record is not None
    assert record.archives[0].extraction_status == "pending"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_extraction_queue_deduplicates_archive_jobs() -> None:
    queue = InMemorySessionExtractionQueue()
    first = await queue.enqueue(
        SessionExtractionWorkItem(scope=_scope(), archive_id="archive-1")
    )
    duplicate = await queue.enqueue(
        first.model_copy(update={"work_id": "different-work-id"})
    )

    assert duplicate.work_id == first.work_id
