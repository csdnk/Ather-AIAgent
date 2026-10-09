"""A lost synchronous response must not select fresh work on retransmission."""

import asyncio

import pytest
from remember_helpers import app as app
from remember_helpers import context, drain, facts, save, source

from aether_agent_memory.remember.contracts.models import (
    CorrectionRequest,
    LifecycleRequest,
    RememberRequest,
    TextInput,
)
from aether_agent_memory.runtime.contracts.models import ErrorCode, ScopeSelector
from aether_agent_memory.runtime.foundation.common import FoundationError
from aether_agent_memory.runtime.foundation.postgres import (
    PostgresTransaction as PostgresTransaction,
)
from aether_agent_memory.runtime.foundation.requests import request_key


def test_empty_consolidation_replay_does_not_consume_later_input(app):
    ctx = context(app, operation="original-consolidation")
    selection = ScopeSelector(session_id="session_1")
    assert app.remember.consolidate(ctx, selection) == ()
    saved = asyncio.run(
        app.remember.save(
            context(app),
            RememberRequest(
                source=source(),
                selection=selection,
                trigger="observe",
                content=TextInput(kind="text", text="Later observation must wait for new consent."),
            ),
        )
    )
    assert app.remember.consolidate(ctx, selection) == ()
    with app.foundation.uow.transaction() as tx:
        assert tx.read("remember_pending", saved.memories[0].memory_id)["state"] == "pending"


def test_reprocess_replay_keeps_original_task_after_long_term_content_correction(app):
    saved = save(app, "Original content")
    drain(app)
    ref = facts(app, saved)[0]
    ctx = context(app, operation="original-reprocess")
    original = app.remember.reprocess(ctx, ref.memory_id)
    asyncio.run(
        app.remember.correct_async(
            context(app),
            ref.memory_id,
            CorrectionRequest(
                expected_version=1,
                content="Corrected content",
                reason="correction",
                source=source(),
            ),
        )
    )
    with app.foundation.uow.transaction() as tx:
        before = len(tx.rows("tasks"))
    assert app.remember.reprocess(ctx, ref.memory_id) == original
    with app.foundation.uow.transaction() as tx:
        assert len(tx.rows("tasks")) == before


def test_distill_replay_keeps_original_task_after_archive(app):
    saved = save(app, "On Tuesday the deployment failed.")
    drain(app)
    ref = facts(app, saved)[0]
    ctx = context(app, operation="original-distill")
    original = app.remember.distill(ctx, (ref,))
    app.remember.lifecycle(
        context(app),
        ref.memory_id,
        LifecycleRequest(expected_version=ref.version, target="archived", reason="archive"),
    )
    assert app.remember.distill(ctx, (ref,)) == original


def test_same_consolidation_operation_rejects_changed_scope(app):
    ctx = context(app, operation="bound-consolidation")
    app.remember.consolidate(ctx, ScopeSelector(session_id="session_1"))
    with pytest.raises(FoundationError) as error:
        app.remember.consolidate(ctx, ScopeSelector(session_id="session_2"))
    assert error.value.code == "IDEMPOTENCY_CONFLICT"


def test_receipt_write_failure_rolls_back_lifecycle_and_enqueued_tasks(app, monkeypatch):
    saved = save(app)
    mid = saved.memories[0].memory_id
    before = app.remember.get(context(app), mid)
    with app.foundation.uow.transaction() as tx:
        tasks_before = tx.rows("tasks")
    original = PostgresTransaction.put_if_revision

    def unavailable(tx, ref, value, expected):
        if ref.object_type == "mutation_receipt":
            raise FoundationError(ErrorCode.DEPENDENCY_UNAVAILABLE, "receipt write interrupted")
        return original(tx, ref, value, expected)

    monkeypatch.setattr(PostgresTransaction, "put_if_revision", unavailable)
    ctx = context(app, operation="atomic-archive")
    with pytest.raises(FoundationError):
        app.remember.lifecycle(
            ctx, mid, LifecycleRequest(expected_version=1, target="archived", reason="test")
        )
    assert app.remember.get(context(app), mid) == before
    assert (
        app.remember.mutations.lookup(ctx, ctx.operation_id, "remember.lifecycle").state
        == "unconfirmed"
    )
    with app.foundation.uow.transaction() as tx:
        assert tx.rows("tasks") == tasks_before


def test_activation_receipt_records_all_original_admitted_tasks(app):
    saved = save(app)
    mid = saved.memories[0].memory_id
    app.remember.lifecycle(
        context(app), mid, LifecycleRequest(expected_version=1, target="archived", reason="test")
    )
    ctx = context(app, operation="reactivate")
    with app.foundation.uow.transaction() as tx:
        before = {key for key, _ in tx.rows("tasks")}
    app.remember.lifecycle(
        ctx, mid, LifecycleRequest(expected_version=1, target="active", reason="test")
    )
    with app.foundation.uow.transaction() as tx:
        admitted = {key for key, _ in tx.rows("tasks")} - before
    receipt = app.remember.mutations.lookup(ctx, ctx.operation_id, "remember.lifecycle").receipt
    assert admitted and receipt and set(receipt.task_ids) == admitted


def test_legacy_response_is_not_backfilled_with_invented_identity_provenance(app):
    saved = save(app)
    mid = saved.memories[0].memory_id
    ctx = context(app, operation="legacy-archive")
    request = LifecycleRequest(expected_version=1, target="archived", reason="test")
    original = app.remember.lifecycle(ctx, mid, request)
    # Represent an upgraded store that has only the pre-receipt operation row.
    with app.foundation.uow.transaction() as tx:
        ref = app.remember.mutations.ref(ctx, ctx.operation_id, "remember.lifecycle")
        tx.remove_if_revision(ref, 1)
        app.remember.remember_result(tx, request_key(ctx, "lifecycle_" + mid), request, original)
    assert app.remember.lifecycle(ctx, mid, request) == original
    assert (
        app.remember.mutations.lookup(ctx, ctx.operation_id, "remember.lifecycle").state
        == "unconfirmed"
    )


def test_changed_target_and_corrupt_original_response_fail_closed(app):
    a, b = save(app, "first"), save(app, "second")
    ctx = context(app, operation="original-reprocess")
    app.remember.reprocess(ctx, a.memories[0].memory_id)
    with pytest.raises(FoundationError) as changed:
        app.remember.reprocess(ctx, b.memories[0].memory_id)
    assert changed.value.code == "IDEMPOTENCY_CONFLICT"
    with app.foundation.uow.transaction() as tx:
        ref = app.remember.mutations.ref(ctx, ctx.operation_id, "remember.reprocess")
        raw = tx.get(ref)
        tx.put_if_revision(ref, {**raw, "response": {"task_id": "different-task"}}, 1)
    with pytest.raises(FoundationError) as corrupt:
        app.remember.mutations.lookup(ctx, ctx.operation_id, "remember.reprocess")
    assert corrupt.value.code == "IDEMPOTENCY_CONFLICT"


def test_lifecycle_receipt_does_not_persist_a_second_authoritative_body(app):
    saved = save(app, "Body belongs in P2 only.")
    mid = saved.memories[0].memory_id
    ctx = context(app, operation="archive-body")
    request = LifecycleRequest(expected_version=1, target="archived", reason="test")
    original = app.remember.lifecycle(ctx, mid, request)
    with app.foundation.uow.transaction() as tx:
        raw = tx.get(app.remember.mutations.ref(ctx, ctx.operation_id, "remember.lifecycle"))
        assert "content" not in raw["response"]
        assert tx.read("remember_operations", request_key(ctx, "lifecycle_" + mid)) is None
    assert app.remember.lifecycle(ctx, mid, request) == original


def test_distill_rejects_oversized_input_before_target_authorization(app, monkeypatch):
    saved = save(app)
    ctx = context(app)
    refs = saved.memories * (app.remember.policy.consolidation_messages + 1)

    def unexpected_authorization(*args, **kwargs):
        pytest.fail("oversized distill input reached per-target authorization")

    monkeypatch.setattr(app.foundation.identity, "authorize", unexpected_authorization)
    with pytest.raises(FoundationError) as error:
        app.remember.distill(ctx, refs)
    assert error.value.code == "INVALID_ARGUMENT"
