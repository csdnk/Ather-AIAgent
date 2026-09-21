"""Shared acceptance contract for all three asynchronous delivery mechanisms."""

from datetime import UTC, datetime, timedelta

import pytest

from aether_agent_memory.adapters.context_projection_queue import InMemoryContextProjectionQueue
from aether_agent_memory.adapters.projection_queue import InMemoryProjectionQueue
from aether_agent_memory.adapters.session_extraction_queue import InMemorySessionExtractionQueue
from aether_agent_memory.adapters.session_store import InMemorySessionStore
from aether_agent_memory.context_store.mapping import skill_uri
from aether_agent_memory.context_store.models import ContextProjectionWorkItem
from aether_agent_memory.core.scope import Scope
from aether_agent_memory.memory.projection import ProjectionWorkItem, ProjectionWorkKind
from aether_agent_memory.session.models import SessionExtractionWorkItem, SessionMessage
from aether_agent_memory.session.service import SessionService


def scope():
    return Scope(tenant_id="t", user_id="u", agent_id="a", session_id="s")


def item(kind):
    if kind == "memory":
        return ProjectionWorkItem(
            memory_id="m", revision=1, kind=ProjectionWorkKind.EMBEDDING, scope=scope()
        )
    if kind == "context":
        return ContextProjectionWorkItem(
            uri=skill_uri(scope(), "skill"), source_revision=1, scope=scope()
        )
    return SessionExtractionWorkItem(scope=scope(), archive_id="archive")


@pytest.fixture(params=["memory", "context", "session"])
def queue_case(request):
    classes = {
        "memory": InMemoryProjectionQueue,
        "context": InMemoryContextProjectionQueue,
        "session": InMemorySessionExtractionQueue,
    }
    return classes[request.param](), item(request.param)


@pytest.mark.parametrize("operation", ["complete", "fail", "supersede"])
async def test_late_owner_cannot_finish_reclaimed_work(queue_case, operation):
    queue, work = queue_case
    await queue.enqueue(work)
    first = await queue.claim(work.work_id)
    queue._items[work.work_id].lease_until = datetime.now(UTC) - timedelta(seconds=1)
    assert len(await queue.pending(limit=1)) == 1
    second = await queue.claim(work.work_id)
    assert first.claim_token and second.claim_token != first.claim_token
    args = (work.work_id, "late failure") if operation == "fail" else (work.work_id,)
    assert await getattr(queue, operation)(*args, claim_token=first.claim_token) is None
    assert await queue.complete(work.work_id, claim_token=second.claim_token) is not None


async def test_expired_owner_without_reclaim_cannot_finish(queue_case):
    queue, work = queue_case
    await queue.enqueue(work)
    claimed = await queue.claim(work.work_id)
    queue._items[work.work_id].lease_until = datetime.now(UTC) - timedelta(seconds=1)
    assert await queue.complete(work.work_id, claim_token=claimed.claim_token) is None


async def test_poll_is_bounded_and_retry_returns_to_due_index(queue_case):
    queue, work = queue_case
    await queue.enqueue(work)
    first = await queue.claim(work.work_id)
    assert await queue.pending(limit=1) == []
    await queue.fail(work.work_id, "retryable", claim_token=first.claim_token)
    await queue.retry(work.work_id)
    assert len(await queue.pending(limit=1)) == 1
    with pytest.raises(ValueError):
        await queue.pending(limit=0)


async def test_session_archive_recovers_lost_enqueue_after_service_restart():
    class BrokenQueue(InMemorySessionExtractionQueue):
        async def enqueue(self, work):
            raise ConnectionError("lost enqueue")

    store = InMemorySessionStore()
    sessions = SessionService(store, extraction_queue=BrokenQueue())
    await sessions.append(scope(), SessionMessage(role="user", content="Remember this"))
    commit = await sessions.commit(scope(), keep_recent_count=0)
    assert commit.memory_extraction_status == "queue_failed"
    queue = InMemorySessionExtractionQueue()
    restarted = SessionService(store, extraction_queue=queue)
    cursor, recovered = await restarted.reconcile_extractions(limit=1)
    assert cursor == 0 and recovered == 1
    assert (await queue.pending())[0].archive_id == commit.archive_id
    await restarted.reconcile_extractions(limit=1)
    assert len(await queue.pending()) == 1


async def test_completed_archive_cannot_be_downgraded_by_late_worker():
    sessions = SessionService(
        InMemorySessionStore(), extraction_queue=InMemorySessionExtractionQueue()
    )
    await sessions.append(scope(), SessionMessage(role="user", content="fact"))
    committed = await sessions.commit(scope(), keep_recent_count=0)
    await sessions.set_archive_extraction_status(scope(), committed.archive_id, "succeeded")
    await sessions.set_archive_extraction_status(scope(), committed.archive_id, "failed")
    assert (
        await sessions.get_archive(scope(), committed.archive_id)
    ).extraction_status == "succeeded"


async def test_unprocessed_archive_is_not_evicted_during_queue_outage():
    sessions = SessionService(
        InMemorySessionStore(), max_archives=1, extraction_queue=InMemorySessionExtractionQueue()
    )
    await sessions.append(scope(), SessionMessage(role="user", content="first"))
    first = await sessions.commit(scope(), keep_recent_count=0)
    await sessions.append(scope(), SessionMessage(role="user", content="second"))
    with pytest.raises(ValueError, match="unprocessed"):
        await sessions.commit(scope(), keep_recent_count=0)
    assert await sessions.get_archive(scope(), first.archive_id) is not None
