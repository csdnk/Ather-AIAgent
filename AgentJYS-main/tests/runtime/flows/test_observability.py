"""Operational evidence: trace continuity, redaction, crash boundaries and probes."""

import asyncio
import json
import os
import sqlite3
import subprocess
import sys
from contextlib import contextmanager
from hashlib import sha256

import pytest
from test_flows import app as app
from test_flows import context, drain, save, source

from aether_agent_memory.recall.contracts.models import RecallRequest
from aether_agent_memory.remember.contracts.models import RememberRequest, TextInput
from aether_agent_memory.runtime.contracts.models import Permission, ScopeSelector
from aether_agent_memory.runtime.flows.host import ThreeFlows
from aether_agent_memory.runtime.foundation.common import FoundationError
from aether_agent_memory.runtime.foundation.telemetry import Telemetry


def records(host, ctx, trace_id):
    rows, after = [], 0
    while True:
        page = host.foundation.diagnostics.trace(ctx, trace_id, after=after, limit=100)
        rows.extend(page["records"])
        if page["next_after"] is None:
            return rows
        after = page["next_after"]


def test_trace_survives_restart_and_reaches_automatic_operate(app):
    ctx = context(app)
    receipt = asyncio.run(
        app.remember.save(
            ctx,
            RememberRequest(
                source=source(),
                selection=ScopeSelector(),
                content=TextInput(kind="text", text="不要记录到技术日志的私人内容"),
            ),
        )
    )
    restarted = ThreeFlows(app.foundation.uow.path, app.executor.root, embedding_profile="lexical")
    try:
        restarted.executor.drop_next_response = True
        drain(restarted)
        logs = records(restarted, context(restarted), ctx.trace_id)
        names = {r["node"] for r in logs}
        assert {
            "remember.save",
            "remember.run",
            "extraction.extract",
            "embedding.embed",
            "vectors.project",
            "runtime.events.deliver",
            "operate.run",
            "executor.submit",
            "executor.verify_read",
        } <= names
        assert {"operate.recover", "operate.reconcile", "executor.query"} <= names
        producer_spans = {r["span_id"] for r in logs if r["node"] == "runtime.tasks.enqueue"}
        invoke_spans = {
            r["span_id"]
            for r in logs
            if r["node"] == "runtime.tasks.invoke" and r["parent_span_id"] in producer_spans
        }
        assert any(r["parent_span_id"] in invoke_spans for r in logs if r["node"] == "remember.run")
        assert receipt.task_ids[0] in json.dumps(logs)
        assert "不要记录到技术日志的私人内容" not in json.dumps(logs, ensure_ascii=False)
        assert any(r["phase"] == "committed" for r in logs)
        assert all(len(r["span_id"]) == 16 and r["trace_id"] == ctx.trace_id for r in logs)
    finally:
        restarted.close()


def test_trace_query_does_not_cross_users_or_tenants(app):
    ctx = context(app)
    with app.foundation.telemetry.span(ctx, "test.private"):
        pass
    for user in ("bob", "carol"):
        result = app.foundation.diagnostics.trace(context(app, user), ctx.trace_id)
        assert result["records"] == [] and result["overview"]["span_count"] == 0
    app.foundation.identity.provision([])
    with pytest.raises(FoundationError):
        app.foundation.diagnostics.trace(ctx, ctx.trace_id)


def test_failed_transaction_keeps_redacted_failure_log(app):
    ctx = context(app)
    with (
        pytest.raises(RuntimeError),
        app.foundation.telemetry.span(
            ctx, "test.rollback", {"password": "secret-password", "text": "private body"}
        ),
        app.foundation.uow.transaction() as tx,
    ):
        tx.write("test_not_committed", "key", {"value": 1})
        raise RuntimeError("api_key=secret-password private body")
    with app.foundation.uow.transaction() as tx:
        assert tx.read("test_not_committed", "key") is None
    logs = records(app, ctx, ctx.trace_id)
    assert {"started", "rolled_back", "failed"} <= {r["phase"] for r in logs}
    assert "private body" not in json.dumps(logs)
    assert "secret-password" not in json.dumps(logs)
    error = next(r for r in logs if r["phase"] == "failed")
    assert error["error_type"] == "RuntimeError" and error["stack"]


def test_diagnostics_require_maintenance_permission(app):
    previous = context(app)
    principal = previous.principal.model_copy(
        update={"permissions": (Permission.READ,), "auth_epoch": 2}
    )
    app.foundation.identity.provision([(sha256(b"alice").hexdigest(), principal)])
    ctx = context(app)
    with pytest.raises(FoundationError):
        app.foundation.diagnostics.trace(ctx, previous.trace_id)
    with pytest.raises(FoundationError):
        asyncio.run(app.health.report(ctx))
    with pytest.raises(FoundationError):
        app.health.runtime(ctx)


def test_cli_failure_exposes_trace_id_and_health_is_live_probe(app):
    base = [
        sys.executable,
        "-m",
        "aether_agent_memory.runtime.flows",
        "--embedding-profile",
        "lexical",
        "--db",
        str(app.foundation.uow.path),
        "--cache-root",
        str(app.executor.root),
    ]
    env = {**os.environ, "PYTHONPATH": "src", "P3_API_KEY": "alice"}
    failed = subprocess.run(
        [*base, "memory", "--memory-id", "absent"],
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=20,
    )
    assert failed.returncode == 2
    trace_id = json.loads(failed.stderr)["trace_id"]
    trace = subprocess.run(
        [*base, "trace", "--trace-id", trace_id],
        env=env,
        capture_output=True,
        text=True,
        check=True,
        timeout=20,
    )
    assert json.loads(trace.stdout)["overview"]["failed_span_count"] >= 1
    health = subprocess.run(
        [*base, "health"], env=env, capture_output=True, text=True, check=True, timeout=20
    )
    assert json.loads(health.stdout)["dependencies"]["database"]["state"] == "available"


def test_process_kill_leaves_open_span_not_success(app, tmp_path):
    ctx = context(app)
    path = tmp_path / "crash.logs.db"
    script = """
import sys,time
from aether_agent_memory.runtime.foundation.telemetry import Telemetry
from aether_agent_memory.runtime.contracts.models import TrustedContext
log=Telemetry(sys.argv[1])
with log.span(TrustedContext.model_validate_json(sys.argv[2]), 'test.crash'):
    print('started',flush=True)
    time.sleep(60)
"""
    env = {**os.environ, "PYTHONPATH": "src"}
    child = subprocess.Popen(
        [sys.executable, "-c", script, str(path), ctx.model_dump_json()],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )
    try:
        assert child.stdout.readline().strip() == "started"
    finally:
        child.kill()
        child.communicate(timeout=10)
    log = Telemetry(path)
    try:
        page = log.page(ctx, ctx.trace_id)
        assert page["overview"]["open_span_count"] == 1
        assert [r["phase"] for r in page["records"]] == ["started"]
    finally:
        log.close()


def test_async_cancellation_is_logged(app):
    ctx = context(app)

    async def work():
        with app.foundation.telemetry.span(ctx, "test.cancel"):
            await asyncio.sleep(30)

    async def run():
        task = asyncio.create_task(work())
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(run())
    assert any(r["phase"] == "cancelled" for r in records(app, ctx, ctx.trace_id))


def test_health_observes_dependencies_worker_staleness_and_no_recovery(app):
    ctx = context(app)
    first = asyncio.run(app.health.report(ctx))
    assert first["runtime"]["worker_state"] == "unavailable"
    assert first["readiness"] == "ready"
    save(app)
    drain(app)
    healthy = asyncio.run(app.health.report(context(app)))
    assert healthy["runtime"]["worker_state"] == "available"
    app.vectors.available = False
    with app.foundation.uow.transaction() as tx:
        for key, row in tx.rows("workers"):
            tx.write("workers", key, {**row, "last_seen": "2000-01-01T00:00:00.000000Z"})
        before = tx.rows("tasks")
    report = asyncio.run(app.health.report(context(app)))
    assert report["dependencies"]["vectors"]["state"] == "unavailable"
    assert report["capabilities"]["long_term"] == "degraded"
    assert report["runtime"]["worker_state"] == "unavailable"
    assert report["liveness"]["state"] == "alive"
    with app.foundation.uow.transaction() as tx:
        assert tx.rows("tasks") == before


def test_health_timeout_and_unconfigured_provider(app):
    async def slow(ctx):
        await asyncio.sleep(30)
        return {"state": "available"}

    app.health.register("executor", slow, replace=True)
    app.remember.extraction = object()
    result = asyncio.run(app.health.report(context(app), timeout_seconds=0.02))
    assert result["dependencies"]["executor"]["reason"] == "timeout"
    assert result["dependencies"]["extraction"]["state"] == "unknown"


def test_recall_trace_keeps_dependency_failure_and_degraded_outcome(app):
    save(app)
    drain(app)
    app.vectors.available = False
    ctx = context(app)
    pack = asyncio.run(
        app.recall.recall(
            ctx,
            RecallRequest(
                query="咖啡", selection=ScopeSelector(), sources="both", token_budget=1024
            ),
        )
    )
    assert pack.outcome == "degraded"
    logs = records(app, ctx, ctx.trace_id)
    assert any(r["node"] == "vectors.search" and r["phase"] == "failed" for r in logs)
    assert any(
        r["node"] == "recall.recall"
        and r["phase"] == "returned"
        and r["output"]["outcome"] == "degraded"
        for r in logs
    )


def test_log_failure_does_not_rollback_business_and_is_unhealthy(app, monkeypatch):
    @contextmanager
    def unavailable():
        raise sqlite3.OperationalError("disk full secret must never leak")
        yield

    with monkeypatch.context() as patch:
        patch.setattr(app.foundation.telemetry, "connect", unavailable)
        receipt = save(app)
    assert receipt.task_ids
    result = asyncio.run(app.health.report(context(app)))
    assert result["dependencies"]["logs"]["state"] == "degraded"
    assert result["readiness"] == "not_ready"
    assert result["dependencies"]["logs"]["dropped_records_this_process"] > 0


def test_retention_and_pagination_report_partial_coverage(app):
    ctx = context(app)
    log = app.foundation.telemetry
    log.max_records = 100
    for _ in range(60):
        with log.span(ctx, "test.retention"):
            pass
    page = app.foundation.diagnostics.trace(ctx, ctx.trace_id, limit=10)
    assert len(page["records"]) == 10 and page["next_after"]
    assert page["coverage"] == "retained_records_only"
    logs = records(app, ctx, ctx.trace_id)
    assert len(logs) == 100 and len({r["sequence"] for r in logs}) == 100
    # Reads enforce the logical limit without taking the writer lock or deleting.
    log.prune()
    assert log.page(ctx, ctx.trace_id)["last_pruned_at"]
    with log.connect() as db:
        db.execute("UPDATE node_logs SET occurred_at='2000-01-01T00:00:00.000000Z'")
    assert records(app, ctx, ctx.trace_id) == []
