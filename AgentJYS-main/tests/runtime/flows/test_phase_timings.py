"""Bounded inclusive phase measurements on existing terminal node logs."""

import asyncio
import copy
import time
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from threading import Event, RLock

import pytest

from aether_agent_memory.runtime.contracts.models import Principal, Scope, TrustedContext
from aether_agent_memory.runtime.foundation.postgres import PostgresCapabilityStore
from aether_agent_memory.runtime.foundation.telemetry import Telemetry, current_node


class RecordingTelemetry(Telemetry):
    def __init__(self):
        self.instance_id = "timing-test"
        self.dropped = 0
        self.last_error = None
        self.tracer = None
        self.records = []

    def _write(self, ctx, data):
        time.sleep(0.002)
        self.records.append(copy.deepcopy(data))


@pytest.fixture
def ctx():
    return TrustedContext(
        principal=Principal(
            principal_id="alice",
            home_scope=Scope(
                tenant_id="tenant", application_id="app", user_id="alice", agent_id="agent"
            ),
            permissions=(),
            auth_epoch=1,
        ),
        request_id="request",
        operation_id="operation",
        trace_id="1" * 32,
        span_id="2" * 16,
        deadline_at=(datetime.now(UTC) + timedelta(minutes=1))
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z"),
    )


def stage():
    from aether_agent_memory.runtime.foundation.timings import measure_stage

    return measure_stage


async def test_async_stage_aggregates_threads_and_only_adds_numeric_terminal_metadata(ctx):
    measure = stage()
    telemetry = RecordingTelemetry()

    def worker():
        with measure("memory_fetch"):
            time.sleep(0.003)

    async with telemetry.async_span(ctx, "memory.get"):
        with measure("memory_prepare"):
            await asyncio.gather(*(asyncio.to_thread(worker) for _ in range(4)))
        with measure("user secret must not become a field"):
            pass
    assert [row["phase"] for row in telemetry.records] == ["started", "returned"]
    assert "timings_ms" not in telemetry.records[0]
    final = telemetry.records[-1]
    assert final["timing_counts"]["memory_fetch"] == 4
    assert final["timing_counts"]["memory_prepare"] == 1
    assert final["timing_counts"]["log_write"] == 1
    assert final["timings_ms"]["memory_fetch"] >= 12
    assert final["timings_ms"]["log_write"] >= 2
    assert final["elapsed_ms"] >= final["timings_ms"]["memory_prepare"]
    assert set(final["timings_ms"]) == {"memory_fetch", "memory_prepare", "log_write"}
    assert all(isinstance(v, (int, float)) and v >= 0 for v in final["timings_ms"].values())
    assert final["timings_inclusive"] is True
    assert final["timings_exclude_terminal_write"] is True
    assert final["timings_partial"] is False
    assert current_node.get() is None


async def test_nested_observed_nodes_restore_isolated_collectors(ctx):
    measure = stage()
    telemetry = RecordingTelemetry()
    async with telemetry.async_span(ctx, "parent"):
        with (
            measure("memory_prepare"),
            telemetry.span(ctx, "child"),
            measure("memory_fetch"),
        ):
            time.sleep(0.002)
        with measure("memory_validate"):
            pass
    child, parent = [row for row in telemetry.records if row["phase"] == "returned"]
    assert "memory_fetch" in child["timings_ms"]
    assert "memory_fetch" not in parent["timings_ms"]
    assert "memory_prepare" not in child["timings_ms"]
    assert parent["timing_counts"]["memory_validate"] == 1
    assert parent["timings_ms"]["memory_prepare"] >= child["timings_ms"]["memory_fetch"]


async def test_cancelled_worker_cannot_mutate_closed_terminal_snapshot(ctx):
    measure = stage()
    telemetry = RecordingTelemetry()
    entered, release, done = Event(), Event(), Event()

    def worker():
        try:
            with measure("embedding_compute"):
                entered.set()
                release.wait(2)
        finally:
            done.set()

    async def operation():
        async with telemetry.async_span(ctx, "embedding.embed"):
            with measure("embedding_prepare"):
                await asyncio.to_thread(worker)

    task = asyncio.create_task(operation())
    while not entered.is_set():
        await asyncio.sleep(0)
    task.cancel()
    try:
        with pytest.raises(asyncio.CancelledError):
            await task
        frozen = copy.deepcopy(telemetry.records[-1])
        assert frozen["phase"] == "cancelled"
        assert frozen["timings_partial"] is True
        assert "embedding_compute" not in frozen["timings_ms"]
        assert frozen["timing_counts"]["embedding_prepare"] == 1
    finally:
        release.set()
        await asyncio.to_thread(done.wait, 2)
    assert telemetry.records[-1] == frozen
    async with telemetry.async_span(ctx, "next"):
        pass
    assert "embedding_compute" not in telemetry.records[-1]["timings_ms"]
    assert current_node.get() is None


def test_timing_failure_does_not_change_business_exception(ctx, monkeypatch):
    measure = stage()
    from aether_agent_memory.runtime.foundation import timings

    telemetry = RecordingTelemetry()
    with pytest.raises(ValueError, match="business failure"), telemetry.span(ctx, "node"):
        monkeypatch.setattr(
            timings, "perf_counter", lambda: (_ for _ in ()).throw(RuntimeError("clock"))
        )
        with measure("memory_fetch"):
            raise ValueError("business failure")
    assert telemetry.records[-1]["phase"] == "failed"
    assert telemetry.records[-1]["timings_partial"] is True
    assert current_node.get() is None


@pytest.mark.parametrize("fail", [False, True])
def test_postgres_wait_and_transaction_through_commit_are_timed_without_changing_lock_order(
    ctx, fail
):
    stage()
    telemetry = RecordingTelemetry()
    events = []

    class Connection:
        closed = broken = False

        @contextmanager
        def transaction(self):
            events.append("begin")
            try:
                yield
            except BaseException:
                events.append("rollback")
                raise
            else:
                time.sleep(0.003)
                events.append("commit")

        def execute(self, sql, params):
            assert "pg_advisory_xact_lock" in sql
            time.sleep(0.002)
            events.append("lock")

        @contextmanager
        def pipeline(self):
            events.append("pipeline")
            try:
                yield
            finally:
                events.append("drain")

    store = object.__new__(PostgresCapabilityStore)
    store._lock, store._closed, store._connection = RLock(), False, Connection()
    try:
        with telemetry.span(ctx, "postgres-test"), store.transaction() as raw:
            events.append("body")
            assert raw.open
            if fail:
                raise ValueError("reject")
    except ValueError:
        assert fail
    assert events == [
        "begin",
        "lock",
        "pipeline",
        "body",
        "drain",
        "rollback" if fail else "commit",
    ]
    assert raw.open is False
    final = telemetry.records[-1]
    assert final["timing_counts"]["postgres_transaction"] == 1
    assert final["timing_counts"]["postgres_lock_wait"] == 1
    assert final["timing_counts"]["postgres_local_wait"] == 1
    assert final["timings_ms"]["postgres_lock_wait"] >= 2
    assert final["timings_ms"]["postgres_transaction"] >= (2 if fail else 5)
