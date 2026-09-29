"""Regression for dashboard reads stalling writers and historical queue polling."""

import asyncio
import copy
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager

import pytest
from test_foundation import app as app
from test_foundation import submit

from aether_agent_memory.runtime.contracts.models import EventEnvelope, TaskRecord


def test_slow_trace_reader_does_not_block_span_writer(app, monkeypatch):
    log = app.telemetry
    ctx = app.identity.context("alice")
    with log.span(ctx, "remember.test"):
        pass
    reader_started, release_reader = threading.Event(), threading.Event()
    original = log.reader

    @contextmanager
    def slow_reader():
        with original() as db:
            db.execute("SELECT count(*) FROM node_logs").fetchone()
            reader_started.set()
            assert release_reader.wait(5)
            yield db

    monkeypatch.setattr(log, "reader", slow_reader)

    def write():
        with log.span(ctx, "remember.concurrent_write"):
            pass

    with ThreadPoolExecutor(max_workers=2) as pool:
        catalog = pool.submit(log.traces, ctx)
        try:
            assert reader_started.wait(2)
            # This must finish while the catalog still holds its read snapshot.
            pool.submit(write).result(timeout=2)
            assert not catalog.done()
            assert log.dropped == 0
        finally:
            release_reader.set()
        assert catalog.result(timeout=2)["items"]






def test_claim_avoids_terminal_history_and_index_follows_rollback(app, monkeypatch):
    submit(app)
    asyncio.run(app.tasks.run_once("history-worker", "engineering"))
    with app.uow.transaction() as tx:
        original = tx.rows("tasks")[0][1]
        assert original["record"]["state"] == "succeeded"
        for index in range(1500):
            row = copy.deepcopy(original)
            row["record"]["task_id"] = f"history_{index}"
            tx.write("tasks", row["record"]["task_id"], row)
        assert tx.active_task_rows() == []
    calls = []
    validate = TaskRecord.model_validate

    def counted(value, *args, **kwargs):
        calls.append(value["task_id"])
        return validate(value, *args, **kwargs)

    monkeypatch.setattr(TaskRecord, "model_validate", counted)
    assert app.tasks.claim("idle", "engineering", app.test_clock()) is None
    assert calls == []
    # The queue projection is transactional, including failed writes and restart.
    with pytest.raises(RuntimeError), app.uow.transaction() as tx:
        row = copy.deepcopy(original)
        row["record"]["state"] = "pending"
        tx.write("tasks", row["record"]["task_id"], row)
        assert len(tx.active_task_rows()) == 1
        raise RuntimeError("rollback")
    with app.uow.transaction() as tx:
        assert tx.active_task_rows() == []
    submit(app, key="after-history")
    claimed = app.tasks.claim("next", "engineering", app.test_clock())
    assert claimed and claimed.state == "running"
    assert not any(task_id.startswith("history_") for task_id in calls)


def test_dispatch_skips_acknowledged_event_history(app, monkeypatch):
    submit(app)
    asyncio.run(app.tasks.run_once("event-worker", "engineering"))
    assert app.events.dispatch_once("event-worker")
    with app.uow.transaction() as tx:
        original = tx.rows("deliveries")[0][1]
        assert original["state"] == "acknowledged"
        for index in range(1500):
            tx.write("deliveries", f"delivered_{index}", original)
        assert tx.pending_delivery_rows() == []

    def unexpected(*args, **kwargs):
        raise AssertionError("completed event was revalidated during idle dispatch")

    monkeypatch.setattr(EventEnvelope, "model_validate", unexpected)
    assert not app.events.dispatch_once("idle")
