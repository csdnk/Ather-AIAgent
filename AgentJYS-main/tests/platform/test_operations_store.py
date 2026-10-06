import json
import os
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql

from aether_platform.directory import Actor, Directory
from aether_platform.operations.models import Command
from aether_platform.operations.store import OpsStore


@pytest.fixture
def store():
    path = os.environ.get("AETHER_OPS_TEST_CONFIG")
    if not path:
        pytest.skip("requires dedicated test PostgreSQL")
    config = json.loads(Path(path).read_text())
    schema = "ops_test_" + uuid4().hex
    directory = Directory(config["database_dsn"], schema=schema)
    directory.migrate()
    store = OpsStore(directory)
    store.migrate()
    yield store
    with psycopg.connect(config["database_dsn"]) as conn:
        conn.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


def actor(uid="root", tenant=None, role="platform_admin"):
    return Actor(uid, "https://id", uid, tenant, role, 1)


def test_unknown_command_is_durable_and_cannot_be_reexecuted_or_rebound(store):
    command = Command(
        command_id="recover-123", resource="tasks", action="reconcile", target_id="job-1"
    )
    record, created = store.admit(actor(), command)
    assert created and record["status"] == "submitted"
    store.finish(command.command_id, "unknown", {"code": "CONNECTION_UNCONFIRMED"})
    fresh = OpsStore(store.directory)
    same, created = fresh.admit(actor(), command)
    assert not created and same["status"] == "unknown"
    with pytest.raises(ValueError):
        fresh.admit(actor(), command.model_copy(update={"target_id": "job-2"}))
    with pytest.raises(PermissionError):
        fresh.admit(actor("other"), command)


def test_records_are_tenant_scoped_and_version_checked(store):
    a, b = actor("a", "tenant-a", "tenant_admin"), actor("b", "tenant-b", "tenant_admin")
    created = store.save_record(a, "support", "case-1", {"summary": "error"}, None)
    assert created["version"] == 1
    assert store.records(b, "support", 50, 0)["total"] == 0
    with pytest.raises(PermissionError):
        store.save_record(b, "support", "case-1", {"summary": "overwrite"}, 1)
    with pytest.raises(ValueError):
        store.save_record(a, "support", "case-1", {"summary": "stale"}, 0)
    updated = store.save_record(a, "support", "case-1", {"summary": "resolved"}, 1)
    assert updated["version"] == 2


def test_alert_deduplication_acknowledgement_and_recovery(store):
    store.observe("queue-age", "tenant-a", 100, 30)
    store.observe("queue-age", "tenant-a", 120, 30)
    a = actor("a", "tenant-a", "tenant_admin")
    alerts = store.alerts(a)
    assert len(alerts) == 1 and alerts[0]["occurrences"] == 2
    alert_id = alerts[0]["id"]
    store.update_alert(a, alert_id, "acknowledge", "working")
    assert store.alerts(a)[0]["owner_id"] == "a"
    store.observe("queue-age", "tenant-a", 0, 30)
    assert store.alerts(a)[0]["state"] == "resolved"
    assert store.alerts(actor("b", "tenant-b", "tenant_admin")) == []


def test_request_and_usage_queries_preserve_tenant_scope_without_content(store):
    from aether_platform.chat import Conversations
    from aether_platform.operations.service import Operations

    directory = store.directory
    directory.seed(
        "ops",
        [{"id": t, "name": t, "enabled": True} for t in ("tenant-a", "tenant-b")],
        [
            {
                "id": u,
                "issuer": "https://id",
                "subject": u,
                "username": u,
                "display_name": u,
                "tenant_id": t,
                "role": "user",
                "enabled": True,
            }
            for u, t in (("u1", "tenant-a"), ("u2", "tenant-b"))
        ],
    )
    chats = Conversations(directory)
    chats.migrate()
    for uid in ("u1", "u2"):
        user = directory.authenticate("https://id", uid)
        conversation = chats.create(user)
        chats.begin(user, conversation["id"], "turn-" + uid, "private content " + uid)
    ops = Operations({}, directory)
    current = actor("a", "tenant-a", "tenant_admin")
    rows = ops.read(current, "token", "requests")
    assert rows["total"] == 1 and rows["items"][0]["user_id"] == "u1"
    assert "private content" not in str(rows)
    assert {r["tenant_id"] for r in ops.read(current, "token", "usage")["items"]} == {"tenant-a"}


def test_failed_external_command_is_not_resubmitted_on_same_key(store):
    from aether_platform.operations.service import Operations
    from aether_platform.p3 import P3Error

    ops = Operations({}, store.directory)
    calls = []

    def fail(*args, **kwargs):
        calls.append(args)
        raise P3Error("CONNECTION_UNCONFIRMED")

    ops.p3_call = fail
    cmd = Command(
        command_id="command-unknown",
        resource="tasks",
        action="reconcile",
        target_id="job-1",
        expected_version=1,
        parameters={"reason": "operator recovery"},
    )
    first = ops.command(actor(), "token", cmd)
    second = ops.command(actor(), "token", cmd)
    assert first["status"] == second["status"] == "unknown"
    assert len(calls) == 1
    ops.p3_call = lambda *args, **kwargs: {"operation_id": cmd.command_id, "state": "completed"}
    assert ops.command_status(actor(), "token", cmd.command_id)["items"][0]["status"] == "complete"
    assert len(calls) == 1


def test_original_command_lookup_does_not_disclose_other_tenant(store):
    cmd = Command(
        command_id="tenant-a-command", resource="support", action="save", target_id="case"
    )
    store.admit(actor("a", "tenant-a", "tenant_admin"), cmd)
    assert store.command_record(actor("b", "tenant-b", "tenant_admin"), cmd.command_id) is None


def test_local_change_and_command_receipt_rollback_together_on_crash(store, monkeypatch):
    from aether_platform.operations.service import Operations

    class ProcessExit(BaseException):
        pass

    ops = Operations({}, store.directory)
    original = ops.store.finish
    cmd = Command(
        command_id="atomic-local-1",
        resource="support",
        action="save",
        target_id="case-atomic",
        parameters={"summary": "incident"},
    )

    def crash(*args, **kwargs):
        raise ProcessExit()

    monkeypatch.setattr(ops.store, "finish", crash)
    with pytest.raises(ProcessExit):
        ops.command(actor(), "token", cmd)
    assert store.records(actor(), "support")["total"] == 0
    assert store.command_record(actor(), cmd.command_id) is None
    monkeypatch.setattr(ops.store, "finish", original)
    result = ops.command(actor(), "token", cmd)
    assert result["status"] == "complete"
    assert ops.command(actor(), "token", cmd)["status"] == "complete"
    assert store.records(actor(), "support")["items"][0]["version"] == 1


def test_backup_original_receipt_recovers_committed_result_without_execution(store, tmp_path):
    import hashlib

    from aether_platform.operations.backups import BackupExecutor
    from aether_platform.operations.service import Operations

    config = {"operations": {"backup_directory": str(tmp_path)}}
    ops = Operations(config, store.directory)
    cmd = Command(
        command_id="backup-recovery-1",
        resource="backups",
        action="create",
        target_id="original-backup",
    )
    store.admit(actor(), cmd)
    executor = BackupExecutor(config, store.directory)
    dump, receipt = executor.paths(cmd.target_id)
    dump.write_bytes(b"controlled archive bytes")
    result = {
        "id": cmd.target_id,
        "command_id": cmd.command_id,
        "status": "complete",
        "sha256": hashlib.sha256(dump.read_bytes()).hexdigest(),
    }
    executor.write_receipt(receipt, result)
    assert executor.completed("create", cmd.target_id, "another-command") is None
    record = ops.command_status(actor(), "token", cmd.command_id)["items"][0]
    assert record["status"] == "complete" and record["result"] == result
    assert ops.command_status(actor(), "token", cmd.command_id)["items"][0]["status"] == "complete"
    assert store.records(actor(), "backups")["items"][0]["version"] == 1


def test_configuration_timeout_reconciles_original_snapshot_without_reactivation(store):
    from aether_platform.operations.service import Operations
    from aether_platform.p3 import P3Error

    ops = Operations({}, store.directory)
    snapshot = {"version": "v1", "config_hash": "a" * 64, "activated_at": "2026-10-06"}
    command = Command(
        command_id="config-timeout-1",
        resource="configuration",
        action="activate",
        parameters={"snapshot": snapshot},
    )
    calls = []

    def remote(actor, token, method, path, **kwargs):
        calls.append(method)
        if method == "PUT":
            raise P3Error("CONNECTION_UNCONFIRMED")
        return snapshot

    ops.p3_call = remote
    assert ops.command(actor(), "token", command)["status"] == "unknown"
    assert (
        ops.command_status(actor(), "token", command.command_id)["items"][0]["status"] == "complete"
    )
    assert calls == ["PUT", "GET"]
