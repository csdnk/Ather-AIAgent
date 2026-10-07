"""Metadata observations execute set queries without accessing business transactions."""

import json
import sqlite3
from contextlib import contextmanager

import pytest


class MetadataDatabase:
    backend = "sqlite"

    def __init__(self, factory=None):
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = factory
        self.db.execute(
            "CREATE TABLE capability_records(namespace TEXT,tenant TEXT,key TEXT,"
            "value TEXT, PRIMARY KEY(namespace,tenant,key))"
        )
        self.reads = []

    @contextmanager
    def reader(self):
        yield self

    def execute(self, sql, parameters=()):
        cursor = self.db.execute(sql, parameters)
        rows = cursor.fetchall()
        self.reads.append(rows)

        class Result:
            def fetchall(self):
                return rows

        return Result()

    def put(self, table, key, value, tenant="system"):
        self.db.execute(
            "INSERT INTO capability_records VALUES(?,?,?,?)",
            ("p3_rf_" + table, tenant, key, json.dumps(value)),
        )

    def task(
        self,
        key,
        *,
        namespace="ours",
        deployment="deployment",
        kind="remember.compress",
        state="succeeded",
        tenant="system",
    ):
        self.put(
            "tasks",
            key,
            {"record": {"kind": kind, "state": state}, "secret": "BODY_SECRET"},
            tenant,
        )
        self.put(
            "temporal_bindings",
            key,
            {"namespace": namespace, "job": {"deployment_id": deployment}, "secret": "BODY_SECRET"},
            tenant,
        )

    def artifact(self, key, task, **changes):
        self.put(
            "remember_artifacts",
            key,
            {
                "task_id": task,
                "published": True,
                "quality": "passed",
                "original_bytes": 1000,
                "stored_bytes": 200,
                "content": "BODY_SECRET",
                **changes,
            },
        )


def observe(db, namespace="ours", deployment="deployment"):
    from aether_agent_memory.runtime.flows.performance_metadata import performance_metadata

    return performance_metadata(
        db, namespace=namespace, deployment_id=deployment, observed_at="2026-10-07T12:00:00.000Z"
    )


@pytest.fixture
def database():
    db = MetadataDatabase()
    yield db
    db.db.close()


@pytest.mark.parametrize(
    "factory",
    [
        None,
        sqlite3.Row,
        lambda cursor, row: dict(zip([c[0] for c in cursor.description], row, strict=True)),
    ],
)
def test_working_ids_use_memory_records_and_include_history_without_body(factory):
    db = MetadataDatabase(factory)
    try:
        for key, memory_id, kind, object_type in [
            ("history1", "wm", "working", "memory"),
            ("history2", "wm", "working", "memory"),
            ("other", "semantic", "semantic", "memory"),
            ("not_memory", "fake", "working", "source"),
        ]:
            db.put(
                "records",
                key,
                {
                    "ref": {"object_type": object_type},
                    "value": {
                        "ref": {"memory_id": memory_id},
                        "kind": kind,
                        "content": "BODY_SECRET",
                    },
                },
            )
        ids, metrics = observe(db)
        assert ids == {"wm"}
        assert metrics["compression"]["reason"] == "not_triggered"
        assert metrics["placement"]["total"] == 0
        assert len(db.reads) == 3
        assert "BODY_SECRET" not in str(db.reads)
    finally:
        db.db.close()


def test_set_queries_isolate_namespace_deployment_and_system_records(database):
    database.task("ours")
    database.task("wrong_namespace", namespace="other")
    database.task("wrong_deployment", deployment="other")
    database.task("foreign_tenant", tenant="not-system")
    database.task("not_compress", kind="remember.project", state="running")
    for task in ("ours", "wrong_namespace", "wrong_deployment", "foreign_tenant", "unbound"):
        database.artifact("artifact_" + task, task)
    ids, metrics = observe(database)
    compression = metrics["compression"]
    assert ids == set()
    assert compression["task_total"] == 1
    assert compression["samples"] == compression["artifacts_total"] == 1
    assert compression["ratio"] == 5
    assert "BODY_SECRET" not in str(database.reads)
    assert "BODY_SECRET" not in json.dumps(metrics)
    assert len(database.reads) == 3


def test_total_byte_ratio_and_pending_rejections_preserve_dashboard_contract(database):
    database.task("a")
    database.task("b")
    database.task("later", state="running")
    database.artifact("a1", "a")
    database.artifact("a2", "a", original_bytes=3000, stored_bytes=3000)
    database.artifact("b1", "b", published=False, quality="failed", stored_bytes=0)
    _, metrics = observe(database)
    result = metrics["compression"]
    assert result["ratio"] == 4000 / 3200
    assert result["samples"] == 2
    assert result["task_total"] == 3
    assert result["tasks_by_state"] == {"succeeded": 2, "running": 1}
    assert result["unpublished_artifacts"] == 1
    assert result["quality_not_passed_artifacts"] == 1
    assert result["invalid_byte_artifacts"] == 1


def test_parameters_cannot_widen_deployment_scope(database):
    database.task("ours")
    database.artifact("a", "ours")
    _, metrics = observe(database, namespace="ours' OR 1=1 --")
    assert metrics["compression"]["ratio"] is None
    assert metrics["compression"]["task_total"] == 0


def test_unpublished_boolean_is_not_coerced_from_number(database):
    database.task("ours", state="running")
    database.artifact("a", "ours", published=1)
    _, metrics = observe(database)
    assert metrics["compression"]["samples"] == 0
    assert metrics["compression"]["reason"] == "pending"


def test_malformed_scalar_fields_cannot_transfer_nested_content(database):
    database.task("ours", state="BODY_SECRET")
    database.artifact(
        "a",
        "ours",
        published={"content": "BODY_SECRET"},
        quality={"content": "BODY_SECRET"},
        original_bytes={"content": "BODY_SECRET"},
    )
    database.put(
        "records",
        "malformed",
        {
            "ref": {"object_type": "memory"},
            "value": {"kind": "working", "ref": {"memory_id": {"content": "BODY_SECRET"}}},
        },
    )
    ids, metrics = observe(database)
    assert ids == set()
    assert "BODY_SECRET" not in str(database.reads)
    assert "BODY_SECRET" not in json.dumps(metrics)
