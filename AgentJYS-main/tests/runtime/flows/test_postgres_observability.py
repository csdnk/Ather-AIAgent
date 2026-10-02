"""Tenant, log and trace integration with the upstream PostgreSQL store only."""

import hashlib
import json
import os
from datetime import UTC, datetime, timedelta

import psycopg
import pytest
from psycopg import sql

from aether_agent_memory.runtime.contracts.models import Permission, Principal, Scope
from aether_agent_memory.runtime.flows.health import storage_probe
from aether_agent_memory.runtime.foundation.host import Foundation


@pytest.fixture
def dsns():
    dsn = os.environ.get("P3_TEST_STATE_DSN")
    if not dsn:
        if os.environ.get("P3_REQUIRE_POSTGRES") == "1":
            pytest.fail("P3_TEST_STATE_DSN is required; database tests must not skip")
        pytest.skip("P3_TEST_STATE_DSN requires a disposable test database")
    with psycopg.connect(dsn, autocommit=True) as db:
        name = db.execute("SELECT current_database()").fetchone()[0]
        assert name.startswith("p3_test_"), "Refusing to reset a non-test database"
        for (table,) in db.execute(
            "SELECT tablename FROM pg_tables WHERE schemaname='public'"
        ).fetchall():
            db.execute(sql.SQL("DROP TABLE {} CASCADE").format(sql.Identifier(table)))
    return {"state": dsn}


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


def test_upstream_service_keeps_auth_health_and_trace_http(tmp_path, dsns, monkeypatch):
    import asyncio

    from fastapi.testclient import TestClient

    from aether_agent_memory.runtime.flows.application import Service
    from aether_agent_memory.runtime.flows.config import ServiceConfiguration

    monkeypatch.setenv("AETHER_POSTGRES_DSN", dsns["state"])
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
            metadata_backend="postgresql",
            identity_file=identity,
            data_dir=tmp_path / "state",
            embedding_profile="lexical",
        ),
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
        assert service.runtime.foundation.telemetry.dropped == 0
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

    monkeypatch.delenv("AETHER_POSTGRES_DSN", raising=False)
    with pytest.raises(ValueError, match="PostgreSQL"):
        Service(
            ServiceConfiguration(
                temporal={"deployment_id": "missing-pg", "endpoint": "127.0.0.1:7233"},
                metadata_backend="postgresql",
                identity_file=tmp_path / "identity.yaml",
                data_dir=tmp_path / "state",
                embedding_profile="lexical",
            )
        )
    assert not list(tmp_path.rglob("*.db"))
