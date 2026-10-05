"""Tenant, log and trace integration with the upstream PostgreSQL store only."""

import hashlib
import json
import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import make_conninfo

from aether_agent_memory.runtime.contracts.models import Permission, Principal, Scope
from aether_agent_memory.runtime.flows.health import storage_probe
from azure_test_runtime import Foundation


@pytest.fixture
def dsns():
    dsn = os.environ.get("P3_TEST_STATE_DSN")
    if not dsn:
        if os.environ.get("P3_REQUIRE_POSTGRES") == "1":
            pytest.fail("P3_TEST_STATE_DSN is required; database tests must not skip")
        pytest.skip("P3_TEST_STATE_DSN requires a disposable test database")
    with psycopg.connect(dsn, autocommit=True) as db:
        name = db.execute("SELECT current_database()").fetchone()[0]
        assert name.startswith("p3_test_"), "Refusing to use a non-test database"
        schema = "observability_" + uuid4().hex
        db.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
    try:
        yield {"state": make_conninfo(dsn, options="-csearch_path=" + schema)}
    finally:
        with psycopg.connect(dsn, autocommit=True) as db:
            db.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


def open_host(tmp_path, dsns, **kwargs):
    return Foundation(tmp_path / "unused.db", postgres_dsn=dsns["state"], **kwargs)


def people(host):
    principals = [
        Principal(
            principal_id=name,
            home_scope=Scope(
                tenant_id=tenant, application_id="app", user_id=name, agent_id="agent"
            ),
            permissions=tuple(Permission),
            auth_epoch=1,
        )
        for name, tenant in (("alice", "t1"), ("bob", "t2"))
    ]
    host.identity.provision(
        [(hashlib.sha256(p.principal_id.encode()).hexdigest(), p) for p in principals]
    )


def test_failed_foundation_initialization_closes_acquired_postgres_resources(
    tmp_path, dsns, monkeypatch
):
    from psycopg_pool import PoolClosed

    from aether_agent_memory.runtime.foundation import host as host_module
    from aether_agent_memory.runtime.foundation.common import FoundationError
    from aether_agent_memory.runtime.foundation.postgres import PostgresUnitOfWork
    from aether_agent_memory.runtime.foundation.postgres_telemetry import PostgresTelemetry

    uow = PostgresUnitOfWork(dsns["state"], tmp_path / "metadata-anchor")
    telemetry = PostgresTelemetry(dsns["state"], tmp_path / "log-anchor")

    def fail_tasks(*args, **kwargs):
        raise RuntimeError("task initialization failed")

    monkeypatch.setattr(host_module, "Tasks", fail_tasks)
    try:
        with pytest.raises(RuntimeError, match="task initialization failed"):
            Foundation(tmp_path / "unused.db", uow=uow, telemetry=telemetry)
        with pytest.raises(FoundationError, match="closed"), uow.transaction():
            pass
        with pytest.raises(PoolClosed), telemetry.reader():
            pass
    finally:
        uow.close()
        telemetry.close()


def test_external_postgres_interfaces_preserve_commit_guards_and_restart(
    tmp_path, dsns, monkeypatch
):
    import sqlite3

    from aether_agent_memory.runtime.foundation.postgres import PostgresUnitOfWork
    from aether_agent_memory.runtime.foundation.postgres_telemetry import PostgresTelemetry

    def reject_sqlite(*args, **kwargs):
        raise AssertionError("external PostgreSQL provider must never open SQLite")

    monkeypatch.setattr(sqlite3, "connect", reject_sqlite)

    def reopen():
        return Foundation(
            tmp_path / "no-reference.db",
            uow=PostgresUnitOfWork(dsns["state"], tmp_path / "metadata-anchor"),
            telemetry=PostgresTelemetry(dsns["state"], tmp_path / "logs-anchor"),
        )

    host = reopen()
    try:
        people(host)
        assert host.identity.context("alice").principal.home_scope.tenant_id == "t1"
        with host.uow.transaction() as tx:
            tx.write("contract_probe", "original-op", {"value": "原始内容", "revision": 1})

        def lost_ownership():
            raise RuntimeError("old owner")

        with pytest.raises(RuntimeError, match="old owner"), host.uow.transaction() as tx:
            tx.write("contract_probe", "original-op", {"revision": 2})
            tx.write("contract_probe", "uncommitted-outbox", {"event": "must-rollback"})
            tx.before_commit.append(lost_ownership)
    finally:
        host.close()

    with psycopg.connect(dsns["state"]) as db:
        rows = db.execute(
            "SELECT key,value FROM capability_records WHERE namespace=%s ORDER BY key",
            ("p3_rf_contract_probe",),
        ).fetchall()
        assert len(rows) == 1 and rows[0][0] == "original-op"
        assert json.loads(rows[0][1]) == {"value": "原始内容", "revision": 1}

    host = reopen()
    try:
        assert host.identity.context("alice").principal.home_scope.tenant_id == "t1"
        with host.uow.transaction() as tx:
            assert tx.read("contract_probe", "original-op") == {"value": "原始内容", "revision": 1}
    finally:
        host.close()
    assert not list(tmp_path.rglob("*.db"))


def test_tenant_logs_trace_restart_and_no_sqlite(tmp_path, dsns):
    host = open_host(tmp_path, dsns)
    people(host)
    ctx = host.identity.context("alice")
    with (
        host.telemetry.span(ctx, "operate.evaluate", {"content": "private-content"}),
        host.uow.transaction() as tx,
    ):
        tx.write("test", "persist", {"ok": True})
    trace_id = ctx.trace_id
    assert host.telemetry.dropped == 0
    assert storage_probe(host.uow, write=True)["backend"] == "postgresql"
    assert storage_probe(host.telemetry, write=True)["backend"] == "postgresql"
    host.close()
    host = open_host(tmp_path, dsns)
    try:
        alice, bob = host.identity.context("alice"), host.identity.context("bob")
        page = host.diagnostics.trace(alice, trace_id, limit=1)
        assert page["records"] and page["next_after"]
        assert not host.diagnostics.trace(bob, trace_id)["records"]
        assert "private-content" not in json.dumps(page)
        assert host.telemetry.traces(alice, flow="operate")["items"]
        assert host.telemetry.traces(alice, flow="business")["items"]
        assert not host.telemetry.traces(alice, flow="recall")["items"]
        assert host.telemetry.traces(alice)["items"]
        with host.uow.transaction() as tx:
            assert tx.read("test", "persist") == {"ok": True}
    finally:
        host.close()
    assert not list(tmp_path.rglob("*.db"))


def test_retention_and_log_failure_does_not_rollback_business(tmp_path, dsns):
    host = open_host(tmp_path, dsns, log_max_records=100)
    try:
        people(host)
        ctx = host.identity.context("alice")
        for _ in range(55):
            with host.telemetry.span(ctx, "operate.evaluate"):
                pass
        host.telemetry.prune()
        with host.telemetry.reader() as db:
            assert db.execute("SELECT count(*) FROM node_logs").fetchone()[0] == 100
        with host.telemetry.connect() as db:
            db.execute(
                "UPDATE node_logs SET occurred_at=%s",
                ((datetime.now(UTC) - timedelta(days=30)).isoformat(),),
            )
        host.telemetry.prune()
        assert not host.telemetry.page(ctx, ctx.trace_id)["records"]
        with host.telemetry.connect() as db:
            db.execute("ALTER TABLE node_logs RENAME TO offline_logs")
        with host.telemetry.span(ctx, "operate.evaluate"), host.uow.transaction() as tx:
            tx.write("test", "committed", True)
        assert host.telemetry.dropped > 0
        with host.uow.transaction() as tx:
            assert tx.read("test", "committed") is True
    finally:
        host.close()


def test_log_reader_and_span_writer_have_independent_ready_connections(tmp_path, dsns):
    host = open_host(tmp_path, dsns)
    try:
        people(host)
        ctx = host.identity.context("alice")
        with host.telemetry.reader() as reader:
            assert reader.execute("SELECT count(*) FROM node_logs").fetchone() == (0,)
            with host.telemetry.span(ctx, "remember.while_reading"):
                pass
        assert host.telemetry.dropped == 0
        with psycopg.connect(dsns["state"]) as db:
            phases = db.execute(
                "SELECT phase FROM node_logs WHERE trace_id=%s ORDER BY sequence",
                (ctx.trace_id,),
            ).fetchall()
            assert phases == [("started",), ("returned",)]
    finally:
        host.close()


def test_two_postgres_providers_cannot_both_commit_the_same_revision(tmp_path, dsns):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    from aether_agent_memory.runtime.contracts.models import ErrorCode, Flow, RecordRef
    from aether_agent_memory.runtime.foundation.common import FoundationError
    from aether_agent_memory.runtime.foundation.postgres import PostgresUnitOfWork

    providers = [
        PostgresUnitOfWork(dsns["state"], tmp_path / f"provider-{index}") for index in range(2)
    ]
    ref = RecordRef(
        owner=Flow.RUNTIME,
        object_type="contract_concurrency",
        object_id="same-original-operation",
        scope=Scope(tenant_id="t1", application_id="app", user_id="alice", agent_id="agent"),
    )
    barrier = Barrier(2, timeout=10)
    try:
        with providers[0].transaction() as tx:
            assert tx.put_if_revision(ref, {"version": 1}, None) == 1

        def update(provider):
            barrier.wait()
            try:
                with provider.transaction() as tx:
                    tx.put_if_revision(ref, {"version": 2}, 1)
                return "committed"
            except FoundationError as exc:
                assert exc.code == ErrorCode.VERSION_CONFLICT
                return "conflict"

        with ThreadPoolExecutor(max_workers=2) as executor:
            assert sorted(executor.map(update, providers)) == ["committed", "conflict"]
        with providers[1].transaction() as tx:
            assert tx.revision(ref) == 2
            assert tx.get(ref) == {"version": 2}
    finally:
        for provider in providers:
            provider.close()


def test_upstream_postgres_otel_and_trace_persistence(tmp_path, dsns):
    from aether_agent_memory.runtime.flows.observability import configure_tracing

    path = tmp_path / "metadata.db"
    host = Foundation(path, postgres_dsn=dsns["state"])
    people(host)
    ctx = host.identity.context("alice")
    # Base span contract must work both before and after attaching OpenTelemetry.
    with host.telemetry.span(ctx, "remember.before_otel"):
        pass
    tracing = configure_tracing(host.telemetry)
    try:
        with (
            host.telemetry.span(ctx, "remember.after_otel", {"body": "private source"}),
            host.uow.transaction() as tx,
        ):
            tx.write("compatibility", "history", {"version": 1})
        assert host.telemetry.dropped == 0
        assert storage_probe(host.uow, write=True)["state"] == "available"
        assert storage_probe(host.telemetry, write=True)["state"] == "available"
        assert host.telemetry.traces(ctx, flow="remember")["items"]
        assert host.telemetry.traces(ctx, flow="business")["items"]
        page = host.diagnostics.trace(ctx, ctx.trace_id, limit=1)
        assert page["records"] and page["next_after"]
        assert "private source" not in json.dumps(page)
        assert not host.diagnostics.trace(host.identity.context("bob"), ctx.trace_id)["records"]
    finally:
        tracing.shutdown()
        host.close()
    host = Foundation(path, postgres_dsn=dsns["state"])
    try:
        with host.uow.transaction() as tx:
            assert tx.read("compatibility", "history") == {"version": 1}
        assert host.telemetry.traces(host.identity.context("alice"))["items"]
    finally:
        host.close()
    assert not list(tmp_path.rglob("*.db"))


def test_component_service_keeps_pg_auth_health_and_trace_http(tmp_path, dsns):
    import asyncio

    from fastapi.testclient import TestClient

    from azure_component_service import Service
    from component_configuration import ComponentConfiguration as ServiceConfiguration

    identity = tmp_path / "identity.yaml"
    identity.write_text(
        json.dumps(
            {
                "revision": 1,
                "tenants": [{"tenant_id": tenant} for tenant in ("t1", "t2")],
                "identities": [
                    {
                        "credential_sha256": hashlib.sha256(name.encode()).hexdigest(),
                        "principal": {
                            "principal_id": name,
                            "home_scope": {
                                "tenant_id": tenant,
                                "application_id": "app",
                                "user_id": name,
                                "agent_id": "agent",
                            },
                            "permissions": [p.value for p in Permission],
                            "auth_epoch": 1,
                        },
                    }
                    for name, tenant in (("alice", "t1"), ("bob", "t2"))
                ],
            }
        ),
        encoding="utf-8",
    )
    service = Service(
        ServiceConfiguration(
            temporal={"deployment_id": "remember-pg-tests", "endpoint": "127.0.0.1:7233"},
            identity_file=identity,
            data_dir=tmp_path / "state",
            embedding_profile="injected",
        ),
        # Exercise the existing PG component through the HTTP composition root.
        # Deployable ServiceConfiguration still forbids direct PG deployment.
        postgres_dsn=dsns["state"],
    )
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    exporter = InMemorySpanExporter()
    service.tracing_provider.add_span_processor(SimpleSpanProcessor(exporter))
    client = TestClient(service.app())
    try:
        headers = {"Authorization": "Bearer alice"}
        assert client.get("/p3/auth/me", headers=headers).status_code == 200
        health = client.get("/p3/health", headers=headers)
        assert health.status_code == 200
        trace = health.headers["x-trace-id"]
        logs = client.get("/p3/logs/" + trace, headers=headers)
        assert logs.status_code == 200 and logs.json()["records"]
        foreign = client.get("/p3/logs/" + trace, headers={"Authorization": "Bearer bob"})
        assert foreign.status_code == 200 and not foreign.json()["records"]
        assert client.get("/p3/traces", headers=headers).json()["items"]
        service.tracing_provider.force_flush()
        assert any(
            format(span.context.trace_id, "032x") == trace for span in exporter.get_finished_spans()
        )
        assert service.runtime.foundation.telemetry.dropped == 0, (
            service.runtime.foundation.telemetry.last_error
        )
        with psycopg.connect(dsns["state"]) as db:
            assert (
                db.execute("SELECT count(*) FROM node_logs WHERE trace_id=%s", (trace,)).fetchone()[
                    0
                ]
                > 0
            )
        assert not (tmp_path / "state/p3.db").exists()
        assert not (tmp_path / "state/p3.logs.db").exists()
    finally:
        client.close()
        asyncio.run(service.close())


def test_missing_upstream_dsn_does_not_fall_back_to_sqlite(tmp_path, monkeypatch):
    from aether_agent_memory.runtime.flows.application import Service
    from aether_agent_memory.runtime.flows.config import ServiceConfiguration
    from azure_configuration_support import settings

    config = ServiceConfiguration.model_validate(settings(tmp_path))
    monkeypatch.delenv("TEST_P3_POSTGRES_DSN", raising=False)
    with pytest.raises(ValueError, match="TEST_P3_POSTGRES_DSN"):
        Service(config)
    assert not config.data_dir.exists()
