"""Scheduling checks on the isolated real PostgreSQL, without starting P3."""

import asyncio
from collections import deque
from types import SimpleNamespace

import pytest
from psycopg.conninfo import make_conninfo
from test_directory import directory  # noqa: F401

from aether_agent_memory.runtime.foundation.postgres import PostgresUnitOfWork
from aether_agent_memory.runtime.temporal.bridge import IntentBridge
from aether_agent_memory.runtime.temporal.service import TemporalService


def test_later_low_hash_intents_cannot_overtake_older_tasks(directory, tmp_path):  # noqa: F811
    db, _, _ = directory
    uow = PostgresUnitOfWork(
        make_conninfo(db.dsn, options="-csearch_path=" + db.schema), tmp_path / "anchor"
    )
    try:
        with uow.transaction() as tx:
            for key, stamp in [
                ("f_old", "2026-10-05T10:00:00Z"),
                ("a_new", "2026-10-05T10:00:01Z"),
            ]:
                tx.write("tasks", key, {"created_at": stamp})
                tx.write(
                    "temporal_start_intents",
                    key,
                    {"state": "pending", "intent": {"job": {"job_id": key}}},
                )
            assert tx.pending_intent_rows("start", limit=1)[0][0] == "f_old"
    finally:
        uow.close()


def test_recall_gets_dispatch_space_amid_large_background_backlog(directory, tmp_path):  # noqa: F811
    db, _, _ = directory
    uow = PostgresUnitOfWork(
        make_conninfo(db.dsn, options="-csearch_path=" + db.schema), tmp_path / "anchor"
    )
    try:
        with uow.transaction() as tx:
            for n in range(120):
                key = f"background_{n:03}"
                tx.write("tasks", key, {"created_at": "2026-10-05T10:00:00Z"})
                tx.write(
                    "temporal_start_intents",
                    key,
                    {
                        "state": "pending",
                        "intent": {"job": {"job_id": key, "kind": "operate.evaluate"}},
                    },
                )
            tx.write("tasks", "recall", {"created_at": "2026-10-05T10:01:00Z"})
            tx.write(
                "temporal_start_intents",
                "recall",
                {
                    "state": "pending",
                    "intent": {"job": {"job_id": "recall", "kind": "recall.execute"}},
                },
            )
            result = tx.pending_intent_rows("start", limit=2)
            assert {key for key, _ in result} == {"recall", "background_000"}
    finally:
        uow.close()


def test_start_lane_fairness_survives_single_dispatch_flushes():
    bridge = IntentBridge(None, None)

    def queue():
        return deque(
            [
                ("start", "recall", {"intent": {"job": {"kind": "recall.execute"}}}),
                ("start", "background", {"intent": {"job": {"kind": "operate.evaluate"}}}),
            ]
        )

    assert [bridge.pop_start(queue())[1] for _ in range(4)] == [
        "recall",
        "background",
        "recall",
        "background",
    ]


@pytest.mark.asyncio
async def test_backlog_continues_without_another_five_second_pause():
    service = object.__new__(TemporalService)
    service.stopped = asyncio.Event()
    service.wakeup = asyncio.Event()
    service.accepting = True
    service.dispatch_progress = True
    service.config = SimpleNamespace(poll_seconds=30)
    refreshed = asyncio.Event()

    async def refresh():
        refreshed.set()

    service.refresh = refresh
    task = asyncio.create_task(service.coordinate())
    try:
        await asyncio.wait_for(refreshed.wait(), 1)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_accepted_interactive_request_wakes_coordinator_before_poll_interval():
    service = object.__new__(TemporalService)
    service.stopped = asyncio.Event()
    service.wakeup = asyncio.Event()
    service.accepting = True
    service.dispatch_progress = False
    service.config = SimpleNamespace(poll_seconds=30, http_wait_seconds=0.1)
    refreshed = asyncio.Event()

    async def refresh():
        refreshed.set()

    async def result(*args):
        return {}

    service.refresh = refresh
    service.accept = lambda *args, **kwargs: SimpleNamespace(job_id="job")
    service.await_result = result
    task = asyncio.create_task(service.coordinate())
    try:
        await service.execute(None, "recall.execute", {})
        await asyncio.wait_for(refreshed.wait(), 1)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
