from __future__ import annotations

import asyncio
import copy
import json
import sqlite3
import subprocess
import sys
import time
from contextlib import suppress
from hashlib import sha256
from pathlib import Path

import pytest

from aether_agent_memory.runtime.contracts.models import (
    AuthorizationGrant,
    EffectStatus,
    ErrorCode,
    EventEnvelope,
    Flow,
    PageRequest,
    Permission,
    Principal,
    RecordRef,
    RecoveryAction,
    RecoveryDecision,
    RecoveryRequest,
    RunResult,
    Scope,
    ScopeSelector,
    TaskSpec,
    TaskState,
)
from aether_agent_memory.runtime.foundation.common import FoundationError, fingerprint, later
from aether_agent_memory.runtime.foundation.host import Foundation


class Clock:
    value = "2026-09-19T00:00:00.000Z"

    def __call__(self):
        return self.value

    def advance(self, seconds=2):
        self.value = later(self.value, seconds)


def principal(name="alice", tenant="tenant_a", epoch=1, permissions=None):
    return Principal(
        principal_id=name,
        home_scope=Scope(
            tenant_id=tenant, application_id="app", user_id=name, agent_id="agent_" + name
        ),
        permissions=tuple(Permission) if permissions is None else permissions,
        auth_epoch=epoch,
    )


def provision(app, identities=None, grants=()):
    people = identities or [
        principal(),
        principal("bob"),
        principal("carol", "tenant_b"),
        principal("david", "tenant_b"),
    ]
    app.identity.provision(
        [(sha256(p.principal_id.encode()).hexdigest(), p) for p in people], grants
    )


@pytest.fixture
def app(tmp_path):
    instance = Foundation(tmp_path / "foundation.db")
    provision(instance)
    clock = Clock()
    instance.identity.clock = instance.tasks.clock = instance.events.clock = (
        instance.diagnostics.clock
    ) = clock
    instance.tasks.lease_seconds = instance.events.lease_seconds = 1
    instance.test_clock = clock
    yield instance
    instance.close()


def submit(app, key="request_1", name="alice"):
    return app.sample.submit(app.identity.context(name), key, "我喜欢简洁的解释")


def fetch(app, task_id, name="alice"):
    return app.diagnostics.task(app.identity.context(name), task_id)


def run(app):
    return asyncio.run(app.tasks.run_once("test_worker", "engineering"))


def test_normal_atomic_flow_and_trace(app):
    task = submit(app)
    with app.uow.transaction() as tx:
        assert tx.get(task.input_ref)
        assert tx.read("tasks", task.task_id)
        assert tx.read("outbox", task.task_id)
    assert run(app)
    assert app.events.dispatch_once("dispatcher")
    current = fetch(app, task.task_id)
    assert current.state == TaskState.SUCCEEDED
    evidence = app.diagnostics.task_evidence(app.identity.context("alice"), task.task_id)
    assert evidence["attempts"][0]["finished_at"]
    assert evidence["deliveries"][0]["state"] == "acknowledged"
    assert {r["stage"] for r in evidence["stages"]} >= {
        "task.enqueued",
        "task.claimed",
        "task.committed",
    }
    with app.uow.transaction() as tx:
        trace_id = tx.read("tasks", task.task_id)["context"]["trace_id"]
    trace = app.diagnostics.trace(app.identity.context("alice"), trace_id, limit=500)
    nodes = {r["node"] for r in trace["records"]}
    assert {"engineering.sample.submit", "engineering.sample.run"} <= nodes


def test_rollback_includes_fact_task_and_event(app, monkeypatch):
    original = app.events.append

    def crash(tx, ctx, event):
        original(tx, ctx, event)
        raise RuntimeError("fault after outbox write")

    monkeypatch.setattr(app.events, "append", crash)
    with pytest.raises(RuntimeError):
        submit(app)
    with app.uow.transaction() as tx:
        assert not tx.rows("records")
        assert not tx.rows("tasks")
        assert not tx.rows("outbox")


def test_request_idempotency_and_content_conflict(app):
    first = submit(app)
    assert submit(app).task_id == first.task_id
    with pytest.raises(FoundationError) as failure:
        app.sample.submit(app.identity.context("alice"), "request_1", "different")
    assert failure.value.code == ErrorCode.IDEMPOTENCY_CONFLICT


@pytest.mark.parametrize("caller", ["bob", "carol", "david"])
def test_cross_user_and_cross_tenant_queries_rejected(app, caller):
    task = submit(app)
    with pytest.raises(FoundationError):
        fetch(app, task.task_id, caller)
    with pytest.raises(FoundationError):
        app.diagnostics.task_evidence(app.identity.context(caller), task.task_id)
    ctx = app.identity.context(caller)
    with pytest.raises(FoundationError) as failure, app.uow.transaction() as tx:
        app.tasks.request_recovery(
            tx,
            ctx,
            RecoveryRequest(
                operation_id="unauthorized",
                task_id=task.task_id,
                expected_revision=1,
                reason="test",
            ),
        )
    assert failure.value.code == ErrorCode.FORBIDDEN


def test_identity_forgery_scope_selector_and_revocation(app):
    ctx = app.identity.context("alice")
    task = submit(app)
    with pytest.raises(FoundationError):
        app.identity.context("invalid")
    with pytest.raises(FoundationError):
        app.identity.context("alice", selector=ScopeSelector(user_id="bob"))
    forged = ctx.model_copy(update={"principal": principal("alice", "tenant_b")})
    with pytest.raises(FoundationError):
        app.diagnostics.task(forged, task.task_id)
    provision(app, [principal(epoch=2), principal("bob")])
    with pytest.raises(FoundationError):
        app.diagnostics.task(ctx, task.task_id)
    assert run(app)
    assert fetch(app, task.task_id).state == TaskState.ATTENTION
    with pytest.raises(ValueError):
        provision(app)  # Old epoch must not resurrect revoked identities.


def test_same_tenant_grant_is_explicit_and_revocable(app):
    task = submit(app)
    grant = AuthorizationGrant(
        grant_id="share",
        grantee_id="bob",
        grantee_tenant_id="tenant_a",
        resource=task.subject,
        permissions=(Permission.DIAGNOSE,),
        revision=1,
    )
    provision(app, grants=[grant])
    assert fetch(app, task.task_id, "bob").task_id == task.task_id
    provision(app, grants=[])
    with pytest.raises(FoundationError):
        fetch(app, task.task_id, "bob")


def test_cas_tombstone_scope_paging_and_poisoned_transaction(app):
    task = submit(app)
    with app.uow.transaction() as tx:
        revision = tx.revision(task.input_ref)
        tx.remove_if_revision(task.input_ref, revision)
    with pytest.raises(FoundationError), app.uow.transaction() as tx:
        tx.put_if_revision(task.input_ref, {"new": True}, None)
    with app.uow.transaction() as tx:
        tx.put_if_revision(task.input_ref, {"new": True}, 2)
        other = task.input_ref.model_copy(update={"object_id": "other"})
        tx.put_if_revision(other, {"new": True}, None)
        page = tx.scan_page("runtime", task.subject.scope, PageRequest(limit=1))
        assert len(page.records) == 1 and page.next_cursor
        second = tx.scan_page(
            "runtime", task.subject.scope, PageRequest(limit=1, cursor=page.next_cursor)
        )
        assert second.records[0] != page.records[0]
        with pytest.raises(FoundationError):
            tx.scan_page(
                "runtime", principal("bob").home_scope, PageRequest(cursor=page.next_cursor)
            )
    with pytest.raises(FoundationError), app.uow.transaction() as tx:
        tx.write("test", "should_rollback", {})
        with suppress(FoundationError):
            tx.put_if_revision(task.input_ref, {}, 1)
    with app.uow.transaction() as tx:
        assert tx.read("test", "should_rollback") is None


def test_nested_transaction_is_rejected(app):
    with app.uow.transaction(), pytest.raises(FoundationError), app.uow.transaction():
        pass


def test_expired_worker_fenced_and_recovered(app):
    task = submit(app)
    old = app.tasks.claim("old_worker", "engineering", app.test_clock())
    app.test_clock.advance()
    assert app.tasks.sweep() == 1
    assert run(app)  # Recovery proves no effect, does not rerun immediately.
    assert fetch(app, task.task_id).state == TaskState.RETRY_WAIT
    app.test_clock.advance()
    assert run(app)
    assert fetch(app, task.task_id).state == TaskState.SUCCEEDED
    with pytest.raises(FoundationError):
        asyncio.run(app.sample.run(app.identity.context("alice"), old))
    evidence = app.diagnostics.task_evidence(app.identity.context("alice"), task.task_id)
    assert evidence["recovery"][0]["decision"]["action"] == "resume"


def test_lease_expiring_during_commit_rolls_back(app):
    task = submit(app)
    claimed = app.tasks.claim("worker", "engineering", app.test_clock())
    result = RecordRef(
        owner=Flow.RUNTIME,
        object_type="engineering_result",
        object_id=task.task_id,
        scope=task.subject.scope,
    )
    ctx = app.identity.context("alice")
    with pytest.raises(FoundationError), app.uow.transaction() as tx:
        tx.put_if_revision(result, {"ok": True}, None)
        app.tasks.complete(tx, ctx, claimed, result)
        app.test_clock.advance()
    with app.uow.transaction() as tx:
        assert tx.get(result) is None
    assert fetch(app, task.task_id).state == TaskState.RUNNING


def test_renew_keeps_token_and_fences_old_revision(app):
    submit(app)
    task = app.tasks.claim("worker", "engineering", app.test_clock())
    app.test_clock.advance(0.5)
    with app.uow.transaction() as tx:
        updated = app.tasks.renew(tx, task.task_id, task.lease, task.revision)
    assert updated.lease.token == task.lease.token
    with pytest.raises(FoundationError), app.uow.transaction() as tx:
        app.tasks.renew(tx, task.task_id, task.lease, task.revision)
    app.test_clock.advance(0.6)
    # Original handler remains valid after heartbeat revision advance.
    asyncio.run(app.sample.run(app.identity.context("alice"), task))
    assert fetch(app, task.task_id).state == TaskState.SUCCEEDED


def test_outbox_pause_and_lost_ack_do_not_duplicate_effect(app):
    task = submit(app)
    with app.uow.transaction() as tx:
        assert tx.rows("deliveries")[0][1]["state"] == "pending"
    assert app.events.dispatch_once("dispatcher", lose_ack=True)
    app.test_clock.advance()
    assert app.events.dispatch_once("dispatcher")
    with app.uow.transaction() as tx:
        receipt = RecordRef(
            owner=Flow.RUNTIME,
            object_type="engineering_receipt",
            object_id=task.task_id,
            scope=task.subject.scope,
        )
        assert tx.get(receipt)["applications"] == 1
        assert tx.rows("deliveries")[0][1]["state"] == "acknowledged"
        assert len(tx.rows("inbox")) == 1


def test_consumer_effect_and_inbox_rollback_together(app):
    task = submit(app)
    original = app.sample.consume

    def fail(tx, event):
        original(tx, event)
        raise RuntimeError("consumer failed")

    app.events.consumers[(app.sample.EVENT, "engineering_receipt")] = fail
    app.events.dispatch_once("dispatcher")
    with app.uow.transaction() as tx:
        assert not tx.rows("inbox")
        receipt = RecordRef(
            owner=Flow.RUNTIME,
            object_type="engineering_receipt",
            object_id=task.task_id,
            scope=task.subject.scope,
        )
        assert tx.get(receipt) is None
    app.events.consumers[(app.sample.EVENT, "engineering_receipt")] = original
    app.test_clock.advance()
    app.events.dispatch_once("dispatcher")
    with app.uow.transaction() as tx:
        assert tx.get(receipt)["applications"] == 1


def test_mutated_event_is_rejected(app):
    task = submit(app)
    with app.uow.transaction() as tx:
        event = EventEnvelope.model_validate(tx.read("outbox", task.task_id)["event"])
    bad = event.model_copy(update={"payload": {"input_id": "other", "mode": "engineering_only"}})
    with pytest.raises(ValueError):
        app.events.validate(bad)
    data = copy.deepcopy(event.model_dump(mode="json"))
    data["payload"]["input_id"] = "other"
    data["payload_hash"] = fingerprint(data["payload"])
    with pytest.raises(ValueError):
        app.events.validate(EventEnvelope.model_validate(data))


def test_no_fake_success_or_unbounded_retry(app):
    task = submit(app)

    async def fake(ctx, record):
        return RunResult(
            outcome="committed",
            effect_status=EffectStatus.CONFIRMED,
            result_ref=record.input_ref,
            reason="fake success",
        )

    app.sample.run = fake
    run(app)
    assert fetch(app, task.task_id).state == TaskState.RECOVERY_WAIT
    app.test_clock.advance()
    run(app)
    app.test_clock.advance()

    async def no_effect(ctx, record):
        return RunResult(
            outcome="retryable_no_effect",
            effect_status=EffectStatus.NO_EFFECT,
            reason="bounded failure",
        )

    app.sample.run = no_effect
    for _ in range(6):
        run(app)
        app.test_clock.advance()
    assert fetch(app, task.task_id).state == TaskState.FAILED
    assert fetch(app, task.task_id).attempt == 3


def test_unknown_queries_original_action_without_resubmitting(app):
    task = submit(app)
    calls = []

    async def uncertain(ctx, record):
        calls.append(("submit", record.task_id))
        return RunResult(
            outcome="uncertain",
            effect_status=EffectStatus.UNKNOWN,
            operation_id=record.task_id,
            reason="response lost",
        )

    async def query(ctx, record):
        calls.append(("query", record.task_id))
        return RecoveryDecision(
            action=RecoveryAction.QUERY_ONLY,
            effect_status=EffectStatus.UNKNOWN,
            reason="executor history unavailable",
            original_operation_id=record.task_id,
            evidence=(record.input_ref,),
        )

    app.sample.run, app.sample.recover = uncertain, query
    for _ in range(10):
        run(app)
        app.test_clock.advance()
    current = fetch(app, task.task_id)
    assert current.state == TaskState.ATTENTION and current.effect_status == EffectStatus.UNKNOWN
    assert [kind for kind, _ in calls].count("submit") == 1
    assert [kind for kind, _ in calls].count("query") == 6
    assert {operation for _, operation in calls} == {task.task_id}


def test_manual_recovery_authorization_revision_audit_and_result(app):
    task = submit(app)
    claimed = app.tasks.claim("worker", "engineering", app.test_clock())
    request = RecoveryRequest(
        operation_id="manual_1",
        task_id=task.task_id,
        expected_revision=claimed.revision,
        reason="operator investigated stopped worker",
    )
    ctx = app.identity.context("alice")
    with pytest.raises(FoundationError) as failure, app.uow.transaction() as tx:
        app.tasks.request_recovery(tx, ctx, request)
    assert failure.value.code == ErrorCode.TASK_STILL_RUNNING
    app.test_clock.advance()
    with app.uow.transaction() as tx:
        operation = app.tasks.request_recovery(tx, ctx, request)
    with app.uow.transaction() as tx:
        assert app.tasks.request_recovery(tx, ctx, request) == operation
    run(app)
    app.test_clock.advance()
    run(app)
    assert app.diagnostics.operation(app.identity.context("alice"), "manual_1").state == "completed"
    evidence = app.diagnostics.task_evidence(app.identity.context("alice"), task.task_id)
    assert {r["phase"] for r in evidence["maintenance"]} == {"accepted", "completed"}


def test_pending_deadline_never_runs_and_class_scope_limits(app):
    task = submit(app)
    app.test_clock.advance(61)
    assert not run(app)
    assert fetch(app, task.task_id).state == TaskState.FAILED
    first = submit(app, "new_1")
    second = submit(app, "new_2")
    assert (
        app.tasks.claim("worker_1", "engineering", app.test_clock()).task_id == first.task_id
        or fetch(app, second.task_id).state == TaskState.RUNNING
    )
    assert app.tasks.claim("worker_2", "engineering", app.test_clock()) is None


def test_health_does_not_claim_business_ready(app):
    health = app.diagnostics.health(app.identity.context("alice"))
    assert all(capability.state == "unknown" for capability in health.capabilities)


def test_duplicate_consume_and_second_consumer_have_independent_inboxes(app):
    app.events.subscribe(app.sample.EVENT, "observer", lambda tx, event: None)
    task = submit(app)
    with app.uow.transaction() as tx:
        event = EventEnvelope.model_validate(tx.read("outbox", task.task_id)["event"])
        assert app.events.consume(tx, "engineering_receipt", event, app.sample.consume)
        assert not app.events.consume(tx, "engineering_receipt", event, app.sample.consume)
        assert app.events.consume(tx, "observer", event, lambda tx, event: None)
        assert len(tx.rows("inbox")) == 2


def test_tenant_backpressure_does_not_block_other_tenant(app):
    app.tasks.pending_limit_per_tenant = 1
    submit(app)
    with pytest.raises(FoundationError) as failure:
        submit(app, "bob_work", "bob")
    assert failure.value.code == ErrorCode.CAPACITY_EXCEEDED
    other = submit(app, "carol_work", "carol")
    app.tasks.class_limits["engineering"] = 2
    one = app.tasks.claim("first", "engineering", app.test_clock())
    two = app.tasks.claim("second", "engineering", app.test_clock())
    assert one.subject.scope.tenant_id != two.subject.scope.tenant_id
    assert other.task_id in {one.task_id, two.task_id}


def test_background_task_does_not_take_online_class_slot(app):
    background = submit(app)
    app.tasks.register("engineering.online", "recall", app.sample)
    spec = TaskSpec(**{name: getattr(background, name) for name in TaskSpec.model_fields})
    spec = spec.model_copy(
        update={"task_id": "online", "kind": "engineering.online", "idempotency_key": "online"}
    )
    ctx = app.identity.context("alice")
    with app.uow.transaction() as tx:
        app.tasks.enqueue(tx, ctx, spec)
    assert app.tasks.claim("background_worker", "engineering", app.test_clock())
    assert app.tasks.claim("online_worker", "recall", app.test_clock()).task_id == "online"


def test_query_cannot_switch_original_operation_id(app):
    task = submit(app)

    async def uncertain(ctx, record):
        return RunResult(
            outcome="uncertain",
            effect_status=EffectStatus.UNKNOWN,
            operation_id="original_action",
            reason="lost response",
        )

    async def wrong_query(ctx, record):
        return RecoveryDecision(
            action=RecoveryAction.QUERY_ONLY,
            effect_status=EffectStatus.UNKNOWN,
            original_operation_id="new_action",
            reason="wrong action",
            evidence=(record.input_ref,),
        )

    app.sample.run, app.sample.recover = uncertain, wrong_query
    run(app)
    app.test_clock.advance()
    run(app)
    assert fetch(app, task.task_id).error_code == ErrorCode.CONTRACT_VIOLATION


def test_external_simulated_effect_survives_lost_response_and_converges(app, tmp_path):
    executor = sqlite3.connect(tmp_path / "independent-simulator.db")
    executor.execute("CREATE TABLE effects (action_id TEXT PRIMARY KEY, effect_count INTEGER)")
    task = submit(app)
    submit_calls = []

    async def lose_response(ctx, record):
        submit_calls.append(record.task_id)
        executor.execute("INSERT INTO effects VALUES (?, 1)", (record.task_id,))
        executor.commit()
        return RunResult(
            outcome="uncertain",
            effect_status=EffectStatus.UNKNOWN,
            operation_id=record.task_id,
            reason="simulated apply then drop response",
        )

    async def reconcile(ctx, record):
        observed = executor.execute(
            "SELECT effect_count FROM effects WHERE action_id=?", (record.task_id,)
        ).fetchone()
        assert observed == (1,)
        proof = RecordRef(
            owner=Flow.RUNTIME,
            object_type="simulated_proof",
            object_id=record.task_id,
            scope=record.subject.scope,
        )
        with app.uow.transaction() as tx:
            app.tasks.guard(tx, record)
            tx.put_if_revision(
                proof,
                {"action_id": record.task_id, "mode": "simulated", "effect_count": observed[0]},
                None,
            )
            app.tasks.complete(tx, ctx, record, proof)
        return RecoveryDecision(
            action=RecoveryAction.QUERY_ONLY,
            effect_status=EffectStatus.CONFIRMED,
            original_operation_id=record.task_id,
            reason="original simulated effect verified",
            evidence=(proof,),
        )

    app.sample.run, app.sample.recover = lose_response, reconcile
    try:
        run(app)
        assert fetch(app, task.task_id).effect_status == EffectStatus.UNKNOWN
        app.test_clock.advance()
        run(app)
        assert fetch(app, task.task_id).state == TaskState.SUCCEEDED
        assert submit_calls == [task.task_id]
        evidence = app.diagnostics.task_evidence(app.identity.context("alice"), task.task_id)
        assert evidence["recovery"][0]["decision"]["effect_status"] == "confirmed"
    finally:
        executor.close()


def test_long_async_handler_renews_lease(tmp_path):
    app = Foundation(tmp_path / "heartbeat.db")
    provision(app)
    app.tasks.lease_seconds = 0.3
    task = submit(app)
    original = app.sample.run

    async def slow(ctx, record):
        await asyncio.sleep(0.7)
        return await original(ctx, record)

    app.sample.run = slow
    try:
        run(app)
        result = fetch(app, task.task_id)
        assert result.state == TaskState.SUCCEEDED
        assert result.revision >= 4
    finally:
        app.close()


def test_real_deadline_cancels_inflight_work_without_success(tmp_path):
    app = Foundation(tmp_path / "deadline.db")
    provision(app)
    task = app.sample.submit(
        app.identity.context("alice", timeout_seconds=0.12), "deadline", "test"
    )

    async def slow(ctx, record):
        await asyncio.sleep(2)
        raise AssertionError("must be cancelled before this point")

    app.sample.run = slow
    try:
        run(app)
        app.tasks.sweep()
        assert fetch(app, task.task_id).state == TaskState.RECOVERY_WAIT
    finally:
        app.close()


def test_cli_configure_submit_worker_trace(tmp_path):
    import os

    person = principal()
    config = tmp_path / "identities.json"
    config.write_text(
        json.dumps(
            {
                "identities": [
                    {
                        "credential_sha256": sha256(b"alice").hexdigest(),
                        "principal": person.model_dump(mode="json"),
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    content = tmp_path / "input.txt"
    content.write_text("工程验证", encoding="utf-8")
    env = {
        **os.environ,
        "PYTHONPATH": str(Path(__file__).resolve().parents[3] / "src"),
        "P3_API_KEY": "alice",
        "PYTHONIOENCODING": "utf-8",
    }
    command = [
        sys.executable,
        "-m",
        "aether_agent_memory.runtime.foundation",
        "--db",
        str(tmp_path / "cli.db"),
    ]

    def invoke(*args):
        completed = subprocess.run(
            [*command, *args], env=env, capture_output=True, text=True, encoding="utf-8", timeout=10
        )
        assert completed.returncode == 0, completed.stdout + completed.stderr
        return json.loads(completed.stdout)

    invoke("configure", "--identities", str(config))
    task = invoke("submit", "--key", "cli_case", "--text-file", str(content))
    invoke("worker", "--once")
    evidence = invoke("trace", "--task-id", task["task_id"])
    assert evidence["task"]["state"] == "succeeded"
    assert evidence["deliveries"][0]["state"] == "acknowledged"


def wait_marker(path, process, expected=None):
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if path.exists() and (expected is None or path.read_text() == expected):
            return
        if process.poll() is not None:
            raise AssertionError(process.communicate())
        time.sleep(0.03)
    raise AssertionError("child did not reach fault point")


@pytest.mark.parametrize("fault", ["after_claim", "before_commit", "after_commit"])
def test_real_process_kill_and_restart(tmp_path, fault):
    database = tmp_path / "process.db"
    app = Foundation(database)
    provision(app)
    task = submit(app)
    app.close()
    marker = tmp_path / "marker"
    child = subprocess.Popen(
        [
            sys.executable,
            str(Path(__file__).with_name("process_worker.py")),
            str(database),
            fault,
            str(marker),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        wait_marker(marker, child, "committed" if fault == "after_commit" else None)
        child.kill()
        child.communicate(timeout=5)
    finally:
        if child.poll() is None:
            child.kill()
            child.communicate(timeout=5)
    time.sleep(0.7)
    restarted = Foundation(database)
    try:
        with restarted.uow.transaction() as tx:
            assert tx.read("fault", "uncommitted") is None
        if fault != "after_commit":
            assert run(restarted)
            time.sleep(1.05)
            assert run(restarted)
        assert fetch(restarted, task.task_id).state == TaskState.SUCCEEDED
        with restarted.uow.transaction() as tx:
            results = [
                r for _, r in tx.rows("records") if r["ref"]["object_type"] == "engineering_result"
            ]
            assert len(results) == 1
            assert results[0]["revision"] == 1
    finally:
        restarted.close()


def test_two_processes_cannot_claim_same_task(tmp_path):
    database = tmp_path / "race.db"
    app = Foundation(database)
    provision(app)
    task = submit(app)
    app.close()
    children = []
    for index in range(2):
        marker = tmp_path / f"claim{index}.json"
        child = subprocess.Popen(
            [
                sys.executable,
                str(Path(__file__).with_name("process_worker.py")),
                str(database),
                "claim",
                str(marker),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        children.append((child, marker))
    results = []
    try:
        for child, marker in children:
            output = child.communicate(timeout=10)
            assert child.returncode == 0, output
            results.append(json.loads(marker.read_text()))
        assert results.count(task.task_id) == 1
        assert results.count(None) == 1
    finally:
        for child, _ in children:
            if child.poll() is None:
                child.kill()
                child.communicate(timeout=5)
