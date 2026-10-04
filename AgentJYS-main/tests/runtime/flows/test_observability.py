"""Operational evidence: trace continuity, redaction, crash boundaries and probes."""

import asyncio
import json
import os
import subprocess
import sys
from contextlib import contextmanager
from hashlib import sha256
from pathlib import Path

import psycopg
import pytest
import remember_helpers
from remember_helpers import facts
from test_flows import app as app
from test_flows import context, drain, save, source

from aether_agent_memory.recall.contracts.models import RecallRequest
from aether_agent_memory.remember.contracts.models import RememberRequest, TextInput
from aether_agent_memory.runtime.contracts.models import Permission, ScopeSelector
from aether_agent_memory.runtime.foundation.common import FoundationError
from aether_agent_memory.runtime.foundation.postgres_telemetry import PostgresTelemetry
from azure_test_runtime import create_runtime, owned

pipeline_app = remember_helpers.app


async def test_health_metadata_waits_keep_transport_responsive(app, monkeypatch):
    from threading import Event

    ctx = context(app)
    loop = asyncio.get_running_loop()
    original, responsive = app.foundation.uow.transaction, []

    @contextmanager
    def slow_transaction():
        released = Event()
        loop.call_soon_threadsafe(released.set)
        responsive.append(released.wait(0.15))
        with original() as tx:
            yield tx

    monkeypatch.setattr(app.foundation.uow, "transaction", slow_transaction)
    report = await app.health.report(ctx)
    assert report["dependencies"]["database"]["state"] == "available"
    assert responsive and all(responsive), "health metadata blocked transport callbacks"


def records(host, ctx, trace_id):
    rows, after = [], 0
    while True:
        page = host.foundation.diagnostics.trace(ctx, trace_id, after=after, limit=100)
        rows.extend(page["records"])
        if page["next_after"] is None:
            return rows
        after = page["next_after"]


def test_trace_survives_restart_and_reaches_automatic_operate(
    pipeline_app, temporal_server, monkeypatch
):
    app = pipeline_app
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
    from aether_agent_memory.operate.basic.continuous import ContinuousOperate

    restarted = create_runtime(
        app.foundation.uow.path,
        app.foundation.uow.path.parent / "cache",
        embedding_profile="injected",
        operate_factory=ContinuousOperate,
    )
    from temporal_test_support import seed_driver

    seed_driver(restarted, temporal_server)
    try:
        drain(restarted)
        from aether_agent_memory.operate.basic.maintenance import CacheMaintenance
        from aether_agent_memory.operate.basic.temporal_stages import RepairStages

        item = restarted.remember.get(context(restarted), facts(restarted, receipt)[0].memory_id)
        cache = restarted.executor.cache
        key, field, _ = cache.keys(item.ref.scope, item.content_hash)
        assert cache.client.hget(key, field) == item.content.encode()
        cache.client.hset(key, field, b"corrupt")
        repair_ctx = context(restarted).model_copy(update={"trace_id": ctx.trace_id})
        maintenance = CacheMaintenance(restarted)
        samples = asyncio.run(maintenance.sample(repair_ctx))
        incidents = [
            incident
            for subject, sample in samples
            for incident in restarted.foundation.dispositions.observe(repair_ctx, subject, sample)
        ]
        incident = next(
            i for i in incidents if i.subject.object_id == restarted.executor.key(item.ref)
        )
        with restarted.foundation.uow.transaction() as tx:
            job_id = tx.read("incidents", incident.incident_id)["task_id"]
            expected_jobs = {tx.read("incidents", i.incident_id)["task_id"]: i for i in incidents}
        repair, reconcile = restarted.executor.repair, RepairStages.reconcile
        repairs, reconciliations = [], []

        def lost_confirmation(item, operation_id, ctx=None):
            repair(item, operation_id, ctx)
            repairs.append(operation_id)
            raise OSError("lost real Redis repair acknowledgement")

        async def observed_reconcile(stages, step):
            reconciliations.append(step.job.job_id)
            return await reconcile(stages, step)

        monkeypatch.setattr(restarted.executor, "repair", lost_confirmation)
        monkeypatch.setattr(RepairStages, "reconcile", observed_reconcile)
        drain(restarted)
        logs = records(restarted, context(restarted), ctx.trace_id)
        directory = os.environ.get("P3_DIAGNOSTIC_ROOT")
        if directory:
            tag = os.environ.get("P3_DIAGNOSTIC_TAG", "trace")
            (Path(directory) / (tag + "-trace-diagnostic.json")).write_text(
                json.dumps(
                    dict(
                        logs=logs,
                        repairs=repairs,
                        reconciliations=reconciliations,
                        incidents=[i.model_dump(mode="json") for i in incidents],
                    )
                ),
                encoding="utf-8",
            )
        # This manually committed sample contains both references to the digest.
        # Each actual incident must submit exactly once and reconcile its own ID.
        assert len(expected_jobs) == len(incidents) == 2
        assert len(repairs) == len(set(repairs)) == len(expected_jobs)
        assert set(repairs) == set(expected_jobs) <= set(reconciliations)
        assert restarted.executor.repair_record(job_id) == (
            item.ref.model_dump_json(),
            item.content_hash,
        )
        assert cache.client.hget(key, field) == item.content.encode()
        with restarted.foundation.uow.transaction() as tx:
            for original_job, original_incident in expected_jobs.items():
                repaired = tx.read("incidents", original_incident.incident_id)["record"]
                assert repaired["state"] == "resolved"
                assert repaired["verification"] == "passed" and repaired["verification_refs"]
                assert restarted.foundation.tasks.load(tx, original_job)[1].state == "succeeded"
        names = {r["node"] for r in logs}
        assert {
            "remember.save",
            "remember.generate_extraction",
            "extraction.extract",
            "embedding.embed",
            "remember.generate_projection",
            "vectors.project",
            "remember.commit_projection",
            "runtime.temporal.step",
            "operate.submit_evaluation",
            "executor.observe",
            "executor.resources",
        } <= names
        assert job_id in json.dumps(logs)
        starts = {
            r["span_id"]: r["input"]
            for r in logs
            if r["node"] == "runtime.temporal.step" and r["phase"] == "started"
        }
        interrupted = {
            starts[r["span_id"]]["task_id"]
            for r in logs
            if r["node"] == "runtime.temporal.step"
            and r["phase"] == "returned"
            and r["span_id"] in starts
            and starts[r["span_id"]]["stage"] == "repair"
            and starts[r["span_id"]]["mode"] == "execute"
            and r["output"]["outcome"] == "query"
            and r["output"]["effect_status"] == "unknown"
            and r["output"]["reason_code"] == "PROVIDER_INTERRUPTED"
        }
        reconciled = {
            starts[r["span_id"]]["task_id"]
            for r in logs
            if r["node"] == "runtime.temporal.step"
            and r["phase"] == "returned"
            and r["span_id"] in starts
            and starts[r["span_id"]]["stage"] == "repair"
            and starts[r["span_id"]]["mode"] == "reconcile"
            and r["output"]["outcome"] == "done"
            and r["output"]["effect_status"] == "confirmed"
        }
        assert interrupted == reconciled == set(expected_jobs), (
            "every lost acknowledgement needs persisted unknown-effect and original-ID recovery"
        )
        producer_spans = {r["span_id"] for r in logs if r["node"] == "runtime.tasks.enqueue"}
        invoke_spans = {
            r["span_id"]
            for r in logs
            if r["node"] == "runtime.temporal.step" and r["parent_span_id"] in producer_spans
        }
        assert any(
            r["parent_span_id"] in invoke_spans
            for r in logs
            if r["node"] == "remember.generate_extraction"
        )
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


def test_process_kill_leaves_open_span_not_success(app, tmp_path):
    ctx = context(app)
    path = tmp_path / "crash.logs"
    dsn = owned().dsn(str(path) + ".logs")
    script = """
import json,os,sys
from aether_agent_memory.runtime.foundation.postgres_telemetry import PostgresTelemetry
from aether_agent_memory.runtime.contracts.models import TrustedContext
payload=json.loads(sys.stdin.readline())
log=PostgresTelemetry(payload['dsn'], payload['path'])
with log.span(TrustedContext.model_validate(payload['context']), 'test.crash'):
    print('started',flush=True)
    sys.stdin.read(1)
    os._exit(23)  # Abrupt death leaves committed started evidence in real PG.
"""
    child = subprocess.Popen(
        [sys.executable, "-B", "-c", script],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=dict(os.environ),
    )
    child.stdin.write(
        json.dumps(dict(dsn=dsn, path=str(path), context=ctx.model_dump(mode="json"))) + "\n"
    )
    child.stdin.flush()
    try:
        assert child.stdout.readline().strip() == "started"
        # Windows virtualenv Popen may target a launcher, not the Python worker.
        # Ask that worker to die without unwinding, then wait for its handles to close.
        child.communicate(input="crash", timeout=10)
        assert child.returncode == 23
    finally:
        if child.poll() is None:
            child.kill()
            child.communicate(timeout=10)
    log = PostgresTelemetry(dsn, path)
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


def test_health_observes_dependencies_worker_staleness_and_no_recovery(app, monkeypatch):
    ctx = context(app)
    first = asyncio.run(app.health.report(ctx))
    assert first["runtime"]["worker_state"] == "unavailable"
    assert first["readiness"] == "ready"
    save(app)
    drain(app)
    healthy = asyncio.run(app.health.report(context(app)))
    assert (
        healthy["runtime"]["worker_state"] == "unavailable"
    )  # Ephemeral test Workers have stopped.

    def unavailable(**kwargs):
        raise OSError("controlled real Milvus collection probe outage")

    monkeypatch.setattr(app.vectors.client, "has_collection", unavailable)
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


@pytest.mark.parametrize("dependency", ["embedding", "vectors"])
def test_working_health_requires_vector_dependencies_without_blocking_saved_body(app, dependency):
    async def unavailable(ctx):
        return {"state": "unavailable"}

    receipt = save(app)
    app.health.register(dependency, unavailable, replace=True)
    ctx = context(app)
    result = asyncio.run(app.health.report(ctx))
    assert result["capabilities"]["working_read"] != "available"
    assert result["capabilities"]["save"] == "available"
    body = app.remember.get(ctx, receipt.memories[0].memory_id)
    assert "无糖咖啡" in body.content


def test_recall_trace_keeps_dependency_failure_without_lexical_fallback(app, monkeypatch):
    save(app)
    drain(app)

    def unavailable(**kwargs):
        raise OSError("controlled real Milvus search outage")

    monkeypatch.setattr(app.vectors.client, "search", unavailable)
    ctx = context(app)
    with pytest.raises(FoundationError) as failure:
        asyncio.run(
            app.recall.recall(
                ctx,
                RecallRequest(
                    query="咖啡",
                    selection=ScopeSelector(session_id="session_1"),
                    sources="both",
                    token_budget=1024,
                ),
            )
        )
    assert failure.value.code.value == "DEPENDENCY_UNAVAILABLE"
    logs = records(app, ctx, ctx.trace_id)
    assert any(r["node"] == "vectors.search" and r["phase"] == "failed" for r in logs)
    assert any(r["node"] == "recall.recall" and r["phase"] == "failed" for r in logs)


def test_log_failure_does_not_rollback_business_and_is_unhealthy(app, monkeypatch):
    @contextmanager
    def unavailable():
        raise psycopg.OperationalError("connection failed secret must never leak")
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
