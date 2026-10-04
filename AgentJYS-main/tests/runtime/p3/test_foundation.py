from __future__ import annotations

import copy
import time
from contextlib import suppress
from hashlib import sha256

import pytest

from aether_agent_memory.runtime.contracts.models import (
    AuthorizationGrant,
    ErrorCode,
    EventEnvelope,
    PageRequest,
    Permission,
    Principal,
    RecoveryRequest,
    Scope,
    ScopeSelector,
)
from aether_agent_memory.runtime.foundation.common import FoundationError, fingerprint, later
from azure_test_runtime import Foundation


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
    instance = Foundation(tmp_path / "foundation.db", engineering_profile=True)
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
    with pytest.raises(FoundationError), app.uow.transaction() as tx:
        app.identity.revalidate(tx, ctx)
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


def wait_marker(path, process, expected=None):
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if path.exists() and (expected is None or path.read_text() == expected):
            return
        if process.poll() is not None:
            raise AssertionError(process.communicate())
        time.sleep(0.03)
    raise AssertionError("child did not reach fault point")
