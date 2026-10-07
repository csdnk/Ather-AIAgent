"""Dashboard performance aggregates execute bounded queries against real SQLite."""

import json
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from aether_agent_memory.runtime.foundation.telemetry import Telemetry, observed, summary

OBSERVED_AT = "2026-10-07T12:00:00.000Z"


class LocalLogs:
    backend = "sqlite"

    def __init__(self, row_factory=None):
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = row_factory
        self.db.executescript(
            "CREATE TABLE node_logs (sequence INTEGER PRIMARY KEY, occurred_at TEXT, "
            "phase TEXT, data TEXT); CREATE INDEX logs_age ON node_logs(occurred_at);"
        )

    @contextmanager
    def reader(self):
        yield self.db

    def add(
        self,
        node="embedding.native.embed",
        *,
        elapsed=100,
        count=2,
        phase="returned",
        at="2026-10-07T11:00:00.000Z",
        operation="op_business",
    ):
        output = (
            summary({"operation_id": operation, "items": [None] * count})
            if (isinstance(count, int) and not isinstance(count, bool) and 0 <= count < 100)
            else {"operation_id": operation, "items": {"count": count}}
        )
        if node == "remember.read_body":
            output.update(outcome="read", memory={"memory_id": "wm1"})
        data = {
            "node": node,
            "elapsed_ms": elapsed,
            "output": output,
            "input": {"body": "NEVER_EXPOSE_BODY"},
            "secret": "NEVER_EXPOSE_SECRET",
        }
        self.db.execute(
            "INSERT INTO node_logs(occurred_at,phase,data) VALUES (?,?,?)",
            (at, phase, json.dumps(data)),
        )


@pytest.fixture
def logs():
    logs = LocalLogs()
    yield logs
    logs.db.close()


def observe(logs, observed_at=OBSERVED_AT):
    from aether_agent_memory.runtime.flows.performance_observations import performance_observations

    return performance_observations(logs, observed_at, working_memory_ids={"wm1"})


@pytest.mark.parametrize(
    "factory",
    [
        None,
        sqlite3.Row,
        lambda cursor, row: dict(zip([c[0] for c in cursor.description], row, strict=True)),
    ],
)
def test_rate_counts_vectors_and_weighted_batch_time_across_row_shapes(factory):
    logs = LocalLogs(factory)
    try:
        logs.add(elapsed=100, count=2)
        logs.add("embedding.embed", elapsed=300, count=6, at="2026-10-07T11:30:00.000Z")
        result = observe(logs)
        metric = result["embedding"]
        assert metric["rate"] == 20
        assert metric["batch_count"] == 2
        assert metric["items"] == 8
        assert metric["elapsed_ms"] == 400
        assert metric["last_observed"] == "2026-10-07T11:30:00.000Z"
        assert metric["window_seconds"] == 86400
        assert metric["from"] == "2026-10-06T12:00:00.000Z"
        assert metric["to"] == OBSERVED_AT
        assert metric["status"] == "available"
        assert not metric["truncated"]
        assert "NEVER_EXPOSE" not in json.dumps(result)
    finally:
        logs.db.close()


def test_window_success_and_health_filters_do_not_inflate_speed(logs):
    logs.add(at="2026-10-06T12:00:00.000Z")
    logs.add(at="2026-10-06T11:59:59.999Z", count=90)
    logs.add(at="2026-10-07T12:00:00.001Z", count=90)
    logs.add(phase="failed", count=90)
    logs.add(phase="started", count=90)
    logs.add(operation="health_embedding", count=90)
    logs.add("embedding.native.health", count=90)
    result = observe(logs)["embedding"]
    assert result["items"] == 2
    assert result["batch_count"] == 1
    assert result["rate"] == 20
    assert observe(logs, "2026-10-10T12:00:00.000Z")["embedding"]["rate"] is None


@pytest.mark.parametrize(
    "bad", [True, False, None, "12", "NaN", -1, float("nan"), float("inf"), {}, []]
)
def test_invalid_elapsed_and_counts_do_not_become_samples(logs, bad):
    logs.add(elapsed=bad)
    logs.add(count=bad)
    logs.add("remember.read_body", elapsed=bad)
    result = observe(logs)
    assert result["embedding"]["rate"] is None
    assert result["embedding"]["batch_count"] == 0
    assert result["working_memory"]["p99_ms"] is None


def test_zero_and_fractional_counts_cannot_create_embedding_rate(logs):
    logs.add(count=0)
    logs.add(count=1.5)
    logs.add(elapsed=0)
    metric = observe(logs)["embedding"]
    assert metric["rate"] is None
    assert metric["batch_count"] == 0


def test_working_p99_uses_nearest_rank_and_preserves_zero(logs):
    for elapsed in range(100):
        logs.add("remember.read_body", elapsed=elapsed)
    logs.add("remember.read_body", elapsed=99999, phase="failed")
    logs.add("remember.read_body", elapsed=99999, operation="health_working")
    logs.add("remember.working_async", elapsed=99999)
    metric = observe(logs)["working_memory"]
    assert metric["p99_ms"] == 98
    assert metric["samples"] == 100
    logs.db.execute("DELETE FROM node_logs")
    logs.add("remember.read_body", elapsed=0)
    assert observe(logs)["working_memory"]["p99_ms"] == 0


def test_body_reads_require_working_memory_membership_and_read_outcome(logs):
    from aether_agent_memory.runtime.flows.performance_observations import performance_observations

    logs.add("remember.read_body", elapsed=3)
    assert performance_observations(logs, OBSERVED_AT)["working_memory"]["samples"] == 0
    assert (
        performance_observations(logs, OBSERVED_AT, working_memory_ids={"other"})["working_memory"][
            "samples"
        ]
        == 0
    )
    assert observe(logs)["working_memory"]["p99_ms"] == 3
    logs.db.execute("UPDATE node_logs SET data=json_set(data,'$.output.outcome','not_found')")
    assert observe(logs)["working_memory"]["p99_ms"] is None


def test_missing_samples_and_corrupt_logs_remain_unknown(logs):
    logs.db.execute(
        "INSERT INTO node_logs(occurred_at,phase,data) VALUES (?,?,?)",
        ("2026-10-07T11:00:00.000Z", "returned", "not-json-secret"),
    )
    result = observe(logs)
    assert result["embedding"]["status"] == "no_samples"
    assert result["embedding"]["rate"] is None
    assert result["embedding"]["elapsed_ms"] is None
    assert result["working_memory"]["p99_ms"] is None
    assert result["working_memory"]["last_observed"] is None


def test_bounded_read_explicitly_marks_partial_window(logs):
    logs.add()
    logs.db.execute(
        "INSERT INTO node_logs(occurred_at,phase,data) SELECT occurred_at,phase,data FROM node_logs"
    )
    for _ in range(13):
        logs.db.execute(
            "INSERT INTO node_logs(occurred_at,phase,data) "
            "SELECT occurred_at,phase,data FROM node_logs"
        )
    result = observe(logs)
    assert result["embedding"]["batch_count"] == 10000
    assert result["embedding"]["truncated"] is True
    assert result["embedding"]["status"] == "partial"
    assert result["working_memory"]["truncated"] is True


def test_database_failure_is_unavailable_without_exception_details(logs):
    logs.db.close()
    result = observe(logs)
    assert result["embedding"]["status"] == "unavailable"
    assert result["embedding"]["rate"] is None
    assert result["working_memory"]["status"] == "unavailable"


def test_production_telemetry_decorator_is_readable_without_raw_payloads():
    class EngineeringTelemetry(LocalLogs, Telemetry):
        def __init__(self):
            super().__init__()
            self.instance_id = "test"
            self.tracer = None
            self.dropped = 0

        def _write(self, ctx, data):
            self.db.execute(
                "INSERT INTO node_logs(occurred_at,phase,data) VALUES (?,?,?)",
                (data["occurred_at"], data["phase"], json.dumps(data)),
            )

    telemetry = EngineeringTelemetry()

    @observed("embedding.native")
    class Provider:
        _telemetry = telemetry

        def embed(self, ctx):
            return {
                "operation_id": "op_real",
                "usage": "query",
                "items": [{"index": 0, "vector": [0.2] * 512}],
            }

    ctx = SimpleNamespace(
        trace_id="a" * 32, span_id="b" * 16, request_id="request_1", operation_id="op_real"
    )
    try:
        Provider().embed(ctx)
        result = observe(telemetry, (datetime.now(UTC) + timedelta(seconds=1)).isoformat())
        assert telemetry.dropped == 0
        assert result["embedding"]["items"] == 1
        assert result["embedding"]["batch_count"] == 1
        assert result["embedding"]["rate"] > 0
    finally:
        telemetry.db.close()
