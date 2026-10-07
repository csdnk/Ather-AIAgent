from copy import deepcopy
from types import SimpleNamespace

import pytest
from test_p3_admin_diagnostics import operator, runtime

from aether_agent_memory.runtime.flows.admin_diagnostics import AdminDiagnostics
from aether_agent_memory.runtime.foundation.common import FoundationError


def action(mode="real"):
    memory = {
        "memory_id": "memory1",
        "version": 1,
        "scope": {
            "tenant_id": "tenant1",
            "user_id": "user1",
            "application_id": "p3",
            "agent_id": "p3-agent",
        },
    }
    return {
        "intent": {
            "action_id": "action1",
            "representation_id": "original",
            "content_hash": "a" * 64,
            "provider_id": "cache",
            "provider_instance_id": "instance1",
            "expected_epoch": 1,
            "provider_mode": mode,
            "created_at": "2026-10-07T00:00:00.000Z",
            "decision": {
                "decision_id": "decision1",
                "memory": memory,
                "outcome": "promote",
                "current_tier": "warm",
                "target_tier": "hot",
                "reason": "heat=0.900000; desired=hot; hysteresis_v1",
                "policy_version": "continuous_heat_v1",
                "storage_watermark": 1,
                "access_watermark": 3,
            },
        },
        "state": "succeeded",
        "revision": 2,
        "cleanup_state": "completed",
        "feedback": {
            "action_id": "action1",
            "provider_instance_id": "instance1",
            "state": "succeeded",
            "observed_at": "2026-10-07T00:00:02.000Z",
            "observation": {
                "memory": memory,
                "representation_id": "original",
                "provider_instance_id": "instance1",
                "tier": "hot",
                "epoch": 2,
                "readable": True,
                "content_hash": "a" * 64,
                "observed_at": "2026-10-07T00:00:02.000Z",
            },
            "read_proof": {
                "action_id": "action1",
                "memory": memory,
                "provider_instance_id": "instance1",
                "content_hash": "a" * 64,
                "readable": True,
                "verified_at": "2026-10-07T00:00:02.000Z",
                "provider_mode": mode,
            },
        },
    }


@pytest.mark.asyncio
async def test_actions_are_scoped_deduplicated_paginated_and_do_not_query_temporal(tmp_path):
    identity, ctx, _ = operator(tmp_path)
    host = runtime(identity, tmp_path)
    admin = AdminDiagnostics(
        host,
        SimpleNamespace(
            ledger=SimpleNamespace(
                config=SimpleNamespace(namespace="isolated", deployment_id="one")
            )
        ),
    )
    assert hasattr(admin, "placements"), "tier actions need their own fast, scoped query"
    with identity.uow.transaction() as tx:
        for task, aid, deployment in [
            ("first", "action1", "one"),
            ("retry", "action1", "one"),
            ("second", "action2", "one"),
            ("foreign", "private", "other"),
        ]:
            tx.write(
                "temporal_bindings",
                task,
                {"namespace": "isolated", "job": {"deployment_id": deployment}},
            )
            tx.write("operate_task_actions", task, {"action_id": aid})
            row = action()
            if aid != "action1":
                row["intent"]["action_id"] = aid
                row["state"] = "unknown"
                row["feedback"] = None
            tx.write("operate_actions", aid, row)
        tx.write("operate_action_triggers", "action1", {"kind": "recall.access"})
    first = await admin.placements(ctx, limit=1)
    assert first["total"] == 2 and first["next_cursor"]
    second = await admin.placements(ctx, limit=1, cursor=first["next_cursor"])
    items = first["items"] + second["items"]
    assert {r["action_id"] for r in items} == {"action1", "action2"}
    confirmed = next(r for r in items if r["action_id"] == "action1")
    assert confirmed["result"] == "succeeded"
    assert confirmed["trigger"] == "recall.access"
    assert confirmed["decision_reason"] == "heat_policy"
    assert confirmed["heat"] == 0.9
    assert confirmed["duration_seconds"] == 2
    assert "private" not in str(first) + str(second)
    filtered = await admin.placements(ctx, status="unconfirmed")
    assert filtered["total"] == 2 and filtered["matching"] == 1
    assert filtered["items"][0]["action_id"] == "action2"


@pytest.mark.parametrize(
    "mode,damage,expected",
    [
        ("real", None, "succeeded"),
        ("simulated", None, "simulated"),
        ("real", "read_proof", "unconfirmed"),
        ("real", "wrong_tier", "unconfirmed"),
        ("real", "wrong_hash", "unconfirmed"),
        ("real", "wrong_instance", "unconfirmed"),
    ],
)
def test_success_requires_exact_action_target_and_read_evidence(mode, damage, expected):
    from aether_agent_memory.runtime.flows import dashboard

    assert hasattr(dashboard, "placement_item")
    row = deepcopy(action(mode))
    if damage == "read_proof":
        row["feedback"]["read_proof"] = None
    elif damage == "wrong_tier":
        row["feedback"]["observation"]["tier"] = "warm"
    elif damage == "wrong_hash":
        row["feedback"]["read_proof"]["content_hash"] = "b" * 64
    elif damage == "wrong_instance":
        row["feedback"]["provider_instance_id"] = "other"
    result = dashboard.placement_item("task1", row, None)
    assert result["result"] == expected
    assert result["trigger"] == "unrecorded"


def test_unknown_provider_messages_are_not_exposed():
    from aether_agent_memory.runtime.flows import dashboard

    assert hasattr(dashboard, "placement_item")
    row = action()
    row["state"] = "failed"
    row["reason"] = row["intent"]["decision"]["reason"] = "secret-provider-token"
    row["feedback"]["reason"] = "private-url"
    result = dashboard.placement_item("task", row, None)
    assert result["result"] == "failed"
    assert result["decision_reason"] == "unrecorded"
    assert "secret" not in str(result) and "private-url" not in str(result)


@pytest.mark.asyncio
async def test_tenant_cannot_query_global_actions(tmp_path):
    identity, ctx, _ = operator(tmp_path, role="aether_tenant_admin")
    admin = AdminDiagnostics(runtime(identity, tmp_path), None)
    assert hasattr(admin, "placements")
    with pytest.raises(FoundationError):
        await admin.placements(ctx)


def test_trigger_metadata_survives_idempotent_enqueue_without_changing_task_input(tmp_path):
    from aether_agent_memory.operate.basic.service import Operate
    from aether_agent_memory.remember.contracts.models import MemoryRef

    identity, ctx, _ = operator(tmp_path)
    service = SimpleNamespace(tasks=SimpleNamespace(enqueue=lambda *args: None))
    memory = MemoryRef.model_validate(action()["intent"]["decision"]["memory"])
    with identity.uow.transaction() as tx:
        task_id = Operate.enqueue(
            service,
            tx,
            ctx,
            memory,
            "event1",
            cleanup=False,
            permanent=False,
            trigger_kind="recall.access",
        )
        Operate.enqueue(
            service,
            tx,
            ctx,
            memory,
            "event1",
            cleanup=False,
            permanent=False,
            trigger_kind="periodic",
        )
        assert tx.read("operate_evaluation_triggers", task_id) == {"kind": "recall.access"}


def test_resuming_an_action_preserves_its_original_trigger(tmp_path):
    from aether_agent_memory.operate.basic.service import Operate
    from aether_agent_memory.operate.contracts.models import ActionIntent

    identity, ctx, _ = operator(tmp_path)
    service = SimpleNamespace(
        uow=identity.uow,
        tasks=SimpleNamespace(guard=lambda *args: None),
        save_action=lambda tx, ctx, record: tx.write(
            "operate_actions", record.intent.action_id, record.model_dump(mode="json")
        ),
    )
    intent = ActionIntent.model_validate(action()["intent"])
    with identity.uow.transaction() as tx:
        tx.write("operate_evaluation_triggers", "original", {"kind": "recall.access"})
        tx.write("operate_evaluation_triggers", "retry", {"kind": "periodic"})
    for task_id in ["original", "retry"]:
        Operate.reserve_evaluation(service, ctx, SimpleNamespace(task_id=task_id), "memory", intent)
    with identity.uow.transaction() as tx:
        assert tx.read("operate_action_triggers", "action1") == {"kind": "recall.access"}
        assert tx.read("operate_task_actions", "retry")["action_id"] == "action1"
