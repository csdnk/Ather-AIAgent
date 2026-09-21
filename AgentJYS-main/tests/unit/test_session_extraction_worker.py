from __future__ import annotations

import pytest

from aether_agent_memory.adapters.session_extraction_queue import (
    InMemorySessionExtractionQueue,
)
from aether_agent_memory.application.session import SessionExtractionWorker
from aether_agent_memory.core.scope import Scope
from aether_agent_memory.session import SessionExtractionWorkItem


def _scope() -> Scope:
    return Scope(
        tenant_id="tenant-1",
        user_id="user-1",
        agent_id="agent-1",
        session_id="session-1",
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_session_extraction_worker_claims_completes_and_retries() -> None:
    queue = InMemorySessionExtractionQueue()
    await queue.enqueue(
        SessionExtractionWorkItem(scope=_scope(), archive_id="archive-1")
    )
    calls: list[str] = []
    statuses: list[str] = []

    class _Consolidate:
        async def mark_archive_extraction_status(self, scope, archive_id, status):
            statuses.append(status)
            return True

        async def execute(self, context, *, archive_id):
            calls.append(archive_id)

    worker = SessionExtractionWorker(
        queue=queue,
        consolidate=_Consolidate(),
    )
    report = await worker.drain(limit=10)
    pending = await queue.pending()

    assert report.requested == 1
    assert report.claimed == 1
    assert report.succeeded == 1
    assert report.failed == 0
    assert calls == ["archive-1"]
    assert statuses == ["processing", "succeeded"]
    assert pending == []
