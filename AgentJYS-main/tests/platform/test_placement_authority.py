"""Placement outlives login, but never current authority or its one-memory scope."""

import pytest
from test_ruoyi_p3 import setup

from aether_agent_memory.runtime.contracts.models import EventEnvelope, Flow, Permission, RecordRef
from aether_agent_memory.runtime.foundation.common import FoundationError, fingerprint


def scheduled(tmp_path, admin=False, expired_before_enrollment=False):
    from aether_agent_memory.runtime.foundation.placement_authority import placement_context

    adapter, identity, state, token = setup(tmp_path)
    p = adapter.authenticate(identity, "token")
    original = identity.context_for_principal(p)
    adapter.bind_context(original, "token")
    actor_context = original
    if admin:
        from test_p3_admin_diagnostics import operator
        from test_p3_admin_memory_commands import GRANTS

        from aether_agent_memory.runtime.foundation.admin_execution import bind_admin_context

        identity, actor_context, state = operator(tmp_path, grants=list(GRANTS))
        adapter = identity.ruoyi_revalidate.__self__
        remote = adapter.verifier.remote

        def directory(method, path, **kwargs):
            if str(kwargs.get("data", {}).get("user_id")) == "17":
                return {
                    "user_id": "17",
                    "tenant_id": "2",
                    "user_enabled": True,
                    "tenant_enabled": True,
                    "role_codes": ["aether_user"],
                    "permissions": [],
                }
            return remote(method, path, **kwargs)

        adapter.verifier.remote = directory
        original = bind_admin_context(identity, actor_context, "old-tenant", "old-user", "recall")
        p = original.principal
    memory = {"scope": p.home_scope.model_dump(mode="json"), "memory_id": "one", "version": 1}
    payload = {"memory": memory, "stage": "read", "outcome": "succeeded"}
    event = EventEnvelope(
        event_id="access",
        event_type="recall.access",
        producer=Flow.RECALL,
        subject=RecordRef(
            owner=Flow.REMEMBER, object_type="memory", object_id="one", scope=p.home_scope
        ),
        subject_revision=1,
        occurred_at=identity.clock(),
        request_id=original.request_id,
        trace_id=original.trace_id,
        initiator_id=p.principal_id,
        initiator_auth_epoch=p.auth_epoch,
        payload=payload,
        payload_hash=fingerprint(payload),
    )
    key = fingerprint([memory["scope"], "one"])
    with identity.uow.transaction() as tx:
        if expired_before_enrollment:
            grant = tx.read("ruoyi_request_grants", actor_context.request_id)
            tx.write("ruoyi_request_grants", actor_context.request_id, {**grant, "expires": 1})
        tx.write(
            "operate_views",
            key,
            {"memory": memory, "scheduler_event": event.model_dump(mode="json")},
        )
        tx.write("outbox", event.event_id, {"event": event.model_dump(mode="json")})
        ctx = placement_context(identity, tx, key, "evaluation-one")
    return adapter, identity, state, original, ctx, memory, key


def test_placement_survives_login_expiry_but_original_request_does_not(tmp_path):
    _, identity, _, original, ctx, _, _ = scheduled(tmp_path)
    with identity.uow.transaction() as tx:
        grant = tx.read("ruoyi_request_grants", original.request_id)
        tx.write("ruoyi_request_grants", original.request_id, {**grant, "expires": 1})
    with identity.uow.transaction() as tx:
        identity.revalidate(tx, ctx)
    with pytest.raises(FoundationError), identity.uow.transaction() as tx:
        identity.revalidate(tx, original)


@pytest.mark.parametrize(
    "change",
    [{"user_enabled": False}, {"tenant_enabled": False}, {"role_codes": []}, {"permissions": []}],
)
def test_current_authority_revocation_stops_placement(tmp_path, change):
    _, identity, state, _, ctx, _, _ = scheduled(tmp_path)
    state.update(change)
    with pytest.raises(FoundationError), identity.uow.transaction() as tx:
        identity.revalidate(tx, ctx)


def test_placement_cannot_read_other_memory_or_write_delete_correct(tmp_path):
    _, identity, _, _, ctx, memory, _ = scheduled(tmp_path)
    ref = RecordRef(
        owner=Flow.REMEMBER, object_type="memory", object_id="one", scope=ctx.principal.home_scope
    )
    with identity.uow.transaction() as tx:
        assert identity.permits(tx, ctx, Permission.READ, ref)
        assert not identity.permits(
            tx, ctx, Permission.READ, ref.model_copy(update={"object_id": "two"})
        )
        assert not identity.permits(tx, ctx, Permission.READ, ref.model_copy(update={"version": 2}))
        for p in (Permission.WRITE, Permission.DELETE, Permission.CORRECT):
            assert not identity.permits(tx, ctx, p, ref)


@pytest.mark.parametrize(
    "field,value", [("cleanup", True), ("cleanup_completed", True), ("memory", None)]
)
def test_retired_or_changed_memory_stops_binding(tmp_path, field, value):
    _, identity, _, _, ctx, _, key = scheduled(tmp_path)
    with identity.uow.transaction() as tx:
        view = tx.read("operate_views", key)
        view[field] = value
        tx.write("operate_views", key, view)
    with pytest.raises(FoundationError), identity.uow.transaction() as tx:
        identity.revalidate(tx, ctx)


def test_existing_admin_due_is_adopted_with_current_authority_not_renewed_token(tmp_path):
    _, identity, state, original, ctx, _, _ = scheduled(
        tmp_path, admin=True, expired_before_enrollment=True
    )
    with identity.uow.transaction() as tx:
        identity.revalidate(tx, ctx)
        binding = tx.read("operate_scheduler_bindings", ctx.request_id)
        assert binding["source_event_id"] == "access"
        assert binding["admin_action"] == "recall"
    with pytest.raises(FoundationError), identity.uow.transaction() as tx:
        identity.revalidate(tx, original)
    state["permissions"].remove("aether:recall:execute")
    with pytest.raises(FoundationError), identity.uow.transaction() as tx:
        identity.revalidate(tx, ctx)


@pytest.mark.parametrize(
    "change", ["epoch", "policy", "scope", "version", "forged_context", "unknown_request"]
)
def test_binding_fences_cannot_be_reused(tmp_path, change):
    _, identity, _, _, ctx, _, key = scheduled(tmp_path)
    with identity.uow.transaction() as tx:
        row = tx.read("identities", ctx.principal.principal_id)
        if change == "epoch":
            row["principal"]["auth_epoch"] += 1
        elif change == "policy":
            row["ruoyi_policy"] = "changed"
        elif change == "scope":
            row["principal"]["home_scope"]["tenant_id"] = "foreign"
        elif change == "version":
            view = tx.read("operate_views", key)
            view["memory"]["version"] += 1
            tx.write("operate_views", key, view)
        elif change == "forged_context":
            ctx = ctx.model_copy(
                update={
                    "principal": ctx.principal.model_copy(
                        update={"permissions": (Permission.READ, Permission.WRITE)}
                    )
                }
            )
        else:
            ctx = ctx.model_copy(update={"request_id": "not-bound"})
        tx.write("identities", ctx.principal.principal_id, row)
    with pytest.raises(FoundationError), identity.uow.transaction() as tx:
        identity.revalidate(tx, ctx)


def test_directory_outage_remains_retryable(tmp_path):
    from aether_agent_memory.runtime.contracts.models import ErrorCode
    from aether_platform.auth.ruoyi import RuoyiUnavailableError

    adapter, identity, _, _, ctx, _, _ = scheduled(tmp_path)

    def unavailable(*args, **kwargs):
        raise RuoyiUnavailableError("unavailable")

    adapter.verifier.remote = unavailable
    with pytest.raises(FoundationError) as failure, identity.uow.transaction() as tx:
        identity.revalidate(tx, ctx)
    assert failure.value.code == ErrorCode.DEPENDENCY_UNAVAILABLE


def test_only_this_evaluation_and_its_real_action_are_permitted(tmp_path):
    _, identity, _, _, ctx, memory, _ = scheduled(tmp_path)
    ref = RecordRef(
        owner=Flow.OPERATE,
        object_type="evaluation",
        object_id="evaluation-one",
        scope=ctx.principal.home_scope,
    )
    decision = {"memory": memory, "current_tier": "hot", "target_tier": "cold"}
    action_id = fingerprint([memory, "hot", "cold", 1, "evaluation-one"])
    with identity.uow.transaction() as tx:
        assert identity.permits(tx, ctx, Permission.READ, ref)
        assert not identity.permits(
            tx, ctx, Permission.READ, ref.model_copy(update={"object_id": "other"})
        )
        action = ref.model_copy(update={"object_type": "action", "object_id": action_id})
        assert not identity.permits(tx, ctx, Permission.READ, action)
        tx.write(
            "operate_actions", action_id, {"intent": {"decision": decision, "expected_epoch": 1}}
        )
        assert identity.permits(tx, ctx, Permission.READ, action)


@pytest.mark.parametrize("kind,cleanup", [("recall.execute", False), ("operate.evaluate", True)])
def test_binding_cannot_enqueue_another_flow_or_cleanup(tmp_path, kind, cleanup):
    from types import SimpleNamespace
    from aether_agent_memory.runtime.foundation.tasks import Tasks

    _, identity, _, _, ctx, memory, _ = scheduled(tmp_path)
    ref = RecordRef(
        owner=Flow.OPERATE,
        object_type="evaluation",
        object_id="evaluation-one",
        scope=ctx.principal.home_scope,
    )
    tasks = Tasks(identity.uow, identity)
    with identity.uow.transaction() as tx:
        tx.put_if_revision(ref, {"memory": memory, "cleanup": cleanup, "permanent": False}, None)
    spec = SimpleNamespace(kind=kind, task_id="evaluation-one", subject=ref, input_ref=ref)
    with pytest.raises(FoundationError), identity.uow.transaction() as tx:
        tasks.enqueue(tx, ctx, spec)


def test_recovery_can_only_adopt_exact_durable_pending_intent(tmp_path):
    _, identity, _, _, ctx, memory, key = scheduled(tmp_path)
    ref = RecordRef(
        owner=Flow.OPERATE,
        object_type="action",
        object_id="original-action",
        scope=ctx.principal.home_scope,
    )
    intent = {
        "action_id": "original-action",
        "decision": {"memory": memory, "current_tier": "hot", "target_tier": "cold"},
        "expected_epoch": 1,
    }
    with identity.uow.transaction() as tx:
        tx.write("operate_actions", "original-action", {"intent": intent})
        tx.write("operate_pending", key, "original-action")
        assert not identity.permits(tx, ctx, Permission.READ, ref)
        tx.write("operate_task_actions", "evaluation-one", intent)
        assert identity.permits(tx, ctx, Permission.READ, ref)
        tx.write("operate_task_actions", "evaluation-one", {**intent, "expected_epoch": 2})
        assert not identity.permits(tx, ctx, Permission.READ, ref)
