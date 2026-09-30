import hashlib
import importlib
import importlib.util

import pytest
from test_ledger import foundation as foundation
from test_ledger import spec_for

from aether_agent_memory.runtime.contracts.models import EffectStatus, Lease, TaskState


def migration():
    name = "aether_agent_memory.runtime.temporal.migration"
    assert importlib.util.find_spec(name), "read-only migration inspection is missing"
    return importlib.import_module(name)


def target(app):
    path = app.uow.path.parent / "service.yaml"
    path.write_text(
        "temporal:\n  deployment_id: migration-test\n  endpoint: localhost:7233\n", encoding="utf-8"
    )
    return path


def old_task(app, *, unknown=False):
    ctx, ref, content, spec = spec_for(app)
    spec = spec.model_copy(update={"kind": "operate.evaluate"})
    app.tasks.register("operate.evaluate", "operate", object())
    with app.uow.transaction() as tx:
        tx.put_if_revision(ref, content, None)
        task = app.tasks.enqueue(tx, ctx, spec)
        if unknown:
            row, task = app.tasks.load(tx, task.task_id)
            app.tasks.change(
                tx,
                {**row, "original_operation_id": "original-action"},
                task,
                state=TaskState.RUNNING,
                attempt=2,
                query_attempt=1,
                lease=Lease(owner_id="old", token="old", until="2020-01-01T00:00:00.000Z"),
                effect_status=EffectStatus.UNKNOWN,
            )
    return task.task_id


def snapshot(path):
    # WAL is business data; SHM read-lock slots can change during a read-only transaction.
    return {
        p.name: None if p.name.endswith("-shm") else hashlib.sha256(p.read_bytes()).hexdigest()
        for p in path.parent.iterdir()
        if p.is_file()
    }


def test_migration_dry_run_is_read_only(foundation):
    target(foundation)
    old_task(foundation)
    before = snapshot(foundation.uow.path)
    report = migration().inspect_migration(foundation.uow.path)
    assert snapshot(foundation.uow.path) == before
    assert report.state_counts["tasks"]["pending"] == 1
    assert report.database_fingerprint and report.config_fingerprint


def test_stale_report_is_rejected(foundation):
    target(foundation)
    old_task(foundation)
    api = migration()
    report = api.inspect_migration(foundation.uow.path)
    with foundation.uow.transaction() as tx:
        tx.write("settings", "changed-after-inspect", True)
    with pytest.raises(ValueError, match="stale"):
        api.apply_migration(
            foundation.uow.path, report, "migration-test", legacy_service_stopped=True
        )


def test_unknown_old_task_enters_reconcile(foundation):
    target(foundation)
    key = old_task(foundation, unknown=True)
    api = migration()
    report = api.inspect_migration(foundation.uow.path)
    assert report.original_operations[key] == "original-action"
    # Missing original action evidence must block, never reset/re-submit it.
    assert any("ORIGINAL_ACTION_MISSING" in item for item in report.blockers)
    with pytest.raises(ValueError, match="blocked"):
        api.apply_migration(
            foundation.uow.path, report, "migration-test", legacy_service_stopped=True
        )
    from aether_agent_memory.operate.contracts.models import ActionIntent, ActionRecord
    from aether_agent_memory.runtime.foundation.common import now

    intent = ActionIntent.model_validate(
        {
            "action_id": "original-action",
            "decision": {
                "decision_id": "decision",
                "memory": {
                    "memory_id": "memory",
                    "version": 1,
                    "scope": foundation.identity.context("alice").principal.home_scope.model_dump(),
                },
                "outcome": "promote",
                "current_tier": "cold",
                "target_tier": "warm",
                "reason": "test",
                "policy_version": "v1",
                "storage_watermark": 1,
                "access_watermark": 1,
            },
            "representation_id": "original",
            "content_hash": "a" * 64,
            "provider_id": "cache",
            "provider_instance_id": "instance",
            "expected_epoch": 1,
            "provider_mode": "real",
            "created_at": now(),
        }
    )
    with foundation.uow.transaction() as tx:
        tx.write("operate_task_actions", key, intent.model_dump(mode="json"))
        tx.write(
            "operate_actions",
            intent.action_id,
            ActionRecord(
                intent=intent, state="unknown", revision=1, cleanup_state="unknown"
            ).model_dump(mode="json"),
        )
    report = api.inspect_migration(foundation.uow.path)
    result = api.apply_migration(
        foundation.uow.path, report, "migration-test", legacy_service_stopped=True
    )
    assert result.bindings[key].entry == "reconcile"
    with foundation.uow.transaction() as tx:
        current = foundation.tasks.load(tx, key)[1]
        assert current.attempt == 2 and current.query_attempt == 1
        assert current.effect_status == EffectStatus.UNKNOWN
        assert tx.read("operate_task_actions", key)["action_id"] == "original-action"


def test_repeat_apply_is_idempotent_and_keeps_original_id(foundation):
    target(foundation)
    key = old_task(foundation)
    api = migration()
    report = api.inspect_migration(foundation.uow.path)
    first = api.apply_migration(
        foundation.uow.path, report, "migration-test", legacy_service_stopped=True
    )
    second = api.apply_migration(
        foundation.uow.path, report, "migration-test", legacy_service_stopped=True
    )
    assert first == second
    assert first.bindings[key].job_id == key
    with foundation.uow.transaction() as tx:
        assert len(tx.rows("temporal_start_intents")) == 1
        assert tx.read("meta", "execution_backend")["backend"] == "temporal"


@pytest.mark.asyncio
async def test_rf_compatibility_entry_refuses_temporal_store(foundation):
    with foundation.uow.transaction() as tx:
        tx.write("meta", "execution_backend", {"backend": "temporal"})
    with pytest.raises(RuntimeError, match="Temporal"):
        await foundation.tasks.run_once("legacy", "engineering")


def test_migration_requires_stopped_old_service(foundation):
    target(foundation)
    api = migration()
    report = api.inspect_migration(foundation.uow.path)
    with pytest.raises(ValueError, match="stop"):
        api.apply_migration(foundation.uow.path, report, "migration-test")


def test_readonly_inspector_rejects_unregistered_kind(foundation):
    target(foundation)
    ctx, ref, content, spec = spec_for(foundation)
    with foundation.uow.transaction() as tx:
        tx.put_if_revision(ref, content, None)
        foundation.tasks.enqueue(tx, ctx, spec)
    report = migration().inspect_migration(foundation.uow.path)
    assert any("UNREGISTERED_KIND" in item for item in report.blockers)


def test_service_refuses_unmigrated_database_before_composition(foundation):
    api = migration()
    old_task(foundation)
    with pytest.raises(ValueError, match="migration"):
        api.check_service_backend(
            foundation.uow.path,
            api.TemporalConfiguration(deployment_id="new", endpoint="localhost:7233"),
        )


def test_unconfigured_http_cannot_start_rf_worker(tmp_path):
    from aether_agent_memory.remember.local import create_runtime
    from aether_agent_memory.runtime.flows.http import create_app

    runtime = create_runtime(tmp_path / "p3.db", tmp_path / "cache", embedding_profile="lexical")
    try:
        with pytest.raises(ValueError, match="Temporal"):
            create_app(runtime)
    finally:
        runtime.close()


def test_engineering_sample_requires_explicit_profile(tmp_path):
    from aether_agent_memory.runtime.foundation.host import Foundation

    app = Foundation(tmp_path / "default.db")
    try:
        assert "engineering.save" not in app.tasks.handlers
    finally:
        app.close()


def test_temporal_store_rejects_unbound_compatibility_admission(foundation):
    from aether_agent_memory.runtime.foundation.common import FoundationError

    with foundation.uow.transaction() as tx:
        tx.write("meta", "execution_backend", {"backend": "temporal"})
    with pytest.raises(FoundationError, match="Temporal"):
        foundation.sample.submit(foundation.identity.context("alice"), "old-client", "input")
    with foundation.uow.transaction() as tx:
        assert tx.rows("tasks") == [] and tx.rows("outbox") == []


def test_apply_failure_rolls_back_marker_and_every_intent(foundation, monkeypatch):
    target(foundation)
    old_task(foundation)
    api = migration()
    report = api.inspect_migration(foundation.uow.path)
    write = api.put

    def fault(db, table, key, value):
        write(db, table, key, value)
        if table == "temporal_migration_batches":
            raise RuntimeError("crash before atomic commit")

    monkeypatch.setattr(api, "put", fault)
    with pytest.raises(RuntimeError):
        api.apply_migration(
            foundation.uow.path, report, "migration-test", legacy_service_stopped=True
        )
    assert api.inspect_migration(foundation.uow.path) == report


def test_config_change_and_directory_owner_block_apply(foundation):
    path = target(foundation)
    api = migration()
    report = api.inspect_migration(foundation.uow.path)
    lock = api.DirectoryLock()
    lock.acquire(foundation.uow.path.parent)
    try:
        with pytest.raises(RuntimeError, match="owned"):
            api.apply_migration(
                foundation.uow.path, report, "migration-test", legacy_service_stopped=True
            )
    finally:
        lock.release()
    path.write_text(path.read_text() + "profile: local\n", encoding="utf-8")
    with pytest.raises(ValueError, match="stale"):
        api.apply_migration(
            foundation.uow.path, report, "migration-test", legacy_service_stopped=True
        )


def test_old_remember_task_without_original_model_binding_is_blocked(foundation):
    target(foundation)
    key = old_task(foundation)
    with foundation.uow.transaction() as tx:
        row = tx.read("tasks", key)
        row["class"], row["record"]["kind"] = "remember", "remember.extract"
        tx.write("tasks", key, row)
    assert any(
        "ADMISSION_MODEL_POLICY_BINDING_MISSING" in item
        for item in migration().inspect_migration(foundation.uow.path).blockers
    )


def test_unknown_legacy_phase_cannot_invent_new_execution(foundation):
    target(foundation)
    key = old_task(foundation, unknown=True)
    with foundation.uow.transaction() as tx:
        row = tx.read("tasks", key)
        row["class"], row["record"]["kind"] = "remember", "remember.save"
        tx.write("tasks", key, row)
    api = migration()
    report = api.inspect_migration(foundation.uow.path)
    assert any("LEGACY_UNKNOWN_PHASE_UNMAPPED" in item for item in report.blockers)
    with pytest.raises(ValueError, match="blocked"):
        api.apply_migration(
            foundation.uow.path, report, "migration-test", legacy_service_stopped=True
        )


@pytest.mark.asyncio
async def test_old_event_keeps_pair_id_and_inbox_effect_once(foundation, workflow_client):
    import asyncio

    from aether_agent_memory.remember.basic.service import Remember
    from aether_agent_memory.runtime.contracts.models import EventEnvelope, Flow, Permission
    from aether_agent_memory.runtime.foundation.common import fingerprint, now
    from aether_agent_memory.runtime.temporal.bridge import IntentBridge
    from aether_agent_memory.runtime.temporal.events import register_events
    from aether_agent_memory.runtime.temporal.gateway import TemporalGateway
    from aether_agent_memory.runtime.temporal.ledger import ExecutionLedger
    from aether_agent_memory.runtime.temporal.registry import StageRegistry
    from aether_agent_memory.runtime.temporal.worker import WorkerHost

    api = migration()
    target(foundation)
    ctx = foundation.identity.context("alice", timeout_seconds=120)
    _, ref, _, _ = spec_for(foundation)
    ref = ref.model_copy(update={"owner": Flow.REMEMBER, "object_type": "memory", "version": 1})
    payload = {
        "memory": {
            "scope": ref.scope.model_dump(mode="json"),
            "memory_id": ref.object_id,
            "version": 1,
        },
        "object_revision": 1,
        "change": "saved",
        "status": "active",
        "projection_state": "not_required",
        "content_hash": "a" * 64,
        "source_count": 1,
    }
    event = EventEnvelope(
        event_id="original-event",
        event_type="memory.changed",
        producer="remember",
        subject=ref,
        subject_revision=1,
        occurred_at=now(),
        request_id=ctx.request_id,
        trace_id=ctx.trace_id,
        initiator_id="alice",
        initiator_auth_epoch=1,
        payload=payload,
        payload_hash=fingerprint(payload),
    )
    foundation.events.register_type(
        "memory.changed", Remember.validate_event, permission=Permission.READ
    )

    def consume(tx, event):
        tx.write(
            "effect_counter", event.event_id, (tx.read("effect_counter", event.event_id) or 0) + 1
        )

    foundation.events.subscribe("memory.changed", "operate", consume)
    key = fingerprint(["operate", event.event_id])
    with foundation.uow.transaction() as tx:
        foundation.events.append(tx, ctx, event)
        foundation.events.consume(tx, "operate", event, consume)  # Lost old dispatcher ACK.
        row = tx.read("deliveries", key)
        tx.write("deliveries", key, {**row, "state": "sent", "attempt": 2})
    report = api.inspect_migration(foundation.uow.path)
    result = api.apply_migration(
        foundation.uow.path, report, "migration-test", legacy_service_stopped=True
    )
    assert result.bindings[key].entry == "reconcile"
    ledger = ExecutionLedger(foundation.tasks, api.deployment_configuration(report.temporal))
    registry = StageRegistry()
    admission = register_events(registry, ledger, foundation.events)
    foundation.tasks.on_admitted = lambda tx, task: ledger.bind_admitted(tx, task)
    with foundation.uow.transaction() as tx:
        assert admission.admit(tx, ctx, event, "operate") == result.bindings[key]
    workers = WorkerHost(workflow_client, ledger, registry)
    try:
        await workers.start()
        await IntentBridge(ledger, TemporalGateway(workflow_client, ledger)).flush()
        async with asyncio.timeout(60):
            await workflow_client.get_workflow_handle(
                f"p3/migration-test/runtime.event_delivery/{key}"
            ).result()
        with foundation.uow.transaction() as tx:
            assert tx.read("effect_counter", event.event_id) == 1
            assert tx.read("deliveries", key)["state"] == "acknowledged"
    finally:
        await workers.stop()


@pytest.mark.parametrize("fault", ["payload", "budget", "context"])
def test_invalid_legacy_event_blocks_backend_switch(foundation, fault):
    from aether_agent_memory.runtime.foundation.common import fingerprint

    target(foundation)
    ctx = foundation.identity.context("alice")
    foundation.sample.submit(ctx, "legacy-event", "input")
    with foundation.uow.transaction() as tx:
        event_id, original = tx.rows("outbox")[0]
        event = original["event"]
        event.update(event_type="memory.changed", producer="remember")
        ref = event["subject"]
        ref.update(owner="remember", object_type="memory", version=1)
        event["subject_revision"] = 1
        event["payload"] = {
            "memory": {"memory_id": ref["object_id"], "scope": ref["scope"], "version": 1},
            "object_revision": 1,
            "change": "saved",
            "status": "active",
            "projection_state": "not_required",
            "content_hash": "a" * 64,
            "source_count": 1,
        }
        if fault == "payload":
            event["payload"]["memory"]["memory_id"] = "unrelated"
        event["payload_hash"] = fingerprint(event["payload"])
        original["signature"] = fingerprint(event)
        if fault == "context":
            original["context"]["principal"]["principal_id"] = "someone-else"
        tx.write("outbox", event_id, original)
        key = fingerprint(["operate", event_id])
        tx.write(
            "deliveries",
            key,
            {
                "event_id": event_id,
                "consumer_id": "operate",
                "state": "sent",
                "attempt": 6 if fault == "budget" else 1,
                "revision": 1,
                "lease": None,
            },
        )
    report = migration().inspect_migration(foundation.uow.path)
    code = "LEGACY_EVENT_BUDGET_EXHAUSTED" if fault == "budget" else "EVENT_DOMAIN_INVALID"
    assert any(item == f"{key}:{code}" for item in report.blockers)
