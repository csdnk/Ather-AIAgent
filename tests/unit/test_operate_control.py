"""Representation control contracts, including ambiguous remote execution."""

from datetime import UTC, datetime, timedelta

import pytest

from aether_agent_memory.adapters.operate_journal import SQLiteActionJournal
from aether_agent_memory.adapters.storage_control_simulator import StorageControlSimulator
from aether_agent_memory.core.enums import MemoryType
from aether_agent_memory.core.memory import Memory
from aether_agent_memory.core.scope import Scope
from aether_agent_memory.memory.retrieval.models import AccessTrace
from aether_agent_memory.operate.controller import OperateController
from aether_agent_memory.operate.models import (
    ActionState,
    ActionType,
    ActuationTarget,
    ExecutionMode,
    PlacementObservation,
    RepresentationKind,
    RepresentationRef,
    RepresentationValueState,
    ResourceBudget,
    TargetRole,
    TierAction,
)
from aether_agent_memory.operate.policy import ActionPlanner, PlacementPolicy, ValuePolicy

SCOPE = Scope(tenant_id="t", user_id="u", agent_id="a", session_id="s")
ORIGIN = ActuationTarget(
    provider="object", namespace="memories", tier="L3", role=TargetRole.AUTHORITATIVE
)
CACHE = ActuationTarget(
    provider="redis", namespace="memory-cache", tier="L0", role=TargetRole.CACHE
)


def ref(kind=RepresentationKind.CANONICAL, revision=1):
    return RepresentationRef(
        memory_id="m",
        representation_id=f"m:{kind.value}",
        representation_kind=kind,
        revision=revision,
    )


def action(request_id="request", mode=ExecutionMode.LIVE):
    return TierAction(
        action_id=request_id,
        representation=ref(),
        scope=SCOPE,
        action_type=ActionType.PROMOTE,
        target=CACHE,
        size_bytes=20,
        expected_epoch=1,
        execution_mode=mode,
        policy_version="placement-v1",
    )


def simulator():
    sim = StorageControlSimulator()
    sim.register(ref(), SCOPE, [ORIGIN], size_bytes=20)
    sim.set_capacity(CACHE, 100)
    return sim


def test_heat_is_derived_per_representation_and_ignores_foreign_scope():
    memory = Memory(
        id="m",
        type=MemoryType.SEMANTIC,
        tenant_id="t",
        user_id="u",
        agent_id="a",
        session_id="s",
        content="fact",
        importance=1,
    )
    traces = [
        AccessTrace(
            trace_id=str(i),
            request_id=str(i),
            memory_id="m",
            source="vector",
            tenant_id="t",
            user_id="u",
            agent_id="a",
            session_id="s",
            score=1,
            metadata={"representation_kind": "vector"},
        )
        for i in range(10)
    ]
    value = ValuePolicy()
    canonical = value.evaluate(ref(), memory, [], traces)
    vector = value.evaluate(ref(RepresentationKind.VECTOR), memory, [], traces)
    assert canonical.heat != memory.importance
    assert vector.heat > canonical.heat
    foreign = [t.model_copy(update={"tenant_id": "other"}) for t in traces]
    assert (
        value.evaluate(ref(RepresentationKind.VECTOR), memory, [], foreign).heat == canonical.heat
    )


def test_placement_keeps_authoritative_copy_and_adds_cache_independent_of_memory_type():
    observed = PlacementObservation(representation=ref(), scope=SCOPE, targets=[ORIGIN], epoch=1)
    value = RepresentationValueState(representation=ref(), heat=0.95, importance=0.7)
    plan = PlacementPolicy(cache_target=CACHE).plan(
        ref(),
        value,
        observed,
        ResourceBudget(capacity_bytes={CACHE.key: 100}),
        size_bytes=20,
    )
    assert plan.targets == [ORIGIN, CACHE]
    actions = ActionPlanner().plan(plan, observed, request_id="r", size_bytes=20)
    assert len(actions) == 1 and actions[0].action_type == ActionType.PROMOTE
    assert actions[0].target.role == TargetRole.CACHE
    cached = observed.model_copy(update={"targets": [ORIGIN, CACHE]})
    cold = value.model_copy(update={"heat": 0.05})
    cold_plan = PlacementPolicy(cache_target=CACHE).plan(
        ref(), cold, cached, ResourceBudget(), size_bytes=20
    )
    assert cold_plan.targets == [ORIGIN]
    assert (
        ActionPlanner().plan(cold_plan, cached, request_id="r2", size_bytes=20)[0].action_type
        == ActionType.DEMOTE
    )


@pytest.mark.parametrize(
    "fault", ["timeout_after_success", "feedback_lost", "accepted_then_failed", "running"]
)
async def test_remote_outcome_is_reconciled_without_resubmit(tmp_path, fault):
    sim = simulator()
    sim.faults["request"] = fault
    path = tmp_path / "journal.db"
    controller = OperateController(sim, SQLiteActionJournal(path))
    first = await controller.execute(action())
    assert first.state in {ActionState.UNKNOWN, ActionState.SUBMITTED}
    assert sim.submission_count == 1
    # A new controller instance simulates a P3 restart, not a simulator reset.
    restarted = OperateController(sim, SQLiteActionJournal(path))
    if fault == "running":
        sim.finish("request")
    second = await restarted.execute(action())
    expected = ActionState.FAILED if fault == "accepted_then_failed" else ActionState.SUCCEEDED
    assert second.state == expected
    assert sim.submission_count == 1
    observed = await sim.observe(ref(), SCOPE)
    assert (CACHE in observed.targets) == (expected == ActionState.SUCCEEDED)


async def test_unknown_blocks_new_action_id_for_same_target(tmp_path):
    sim = simulator()
    sim.faults["request"] = "running"
    controller = OperateController(sim, SQLiteActionJournal(tmp_path / "journal.db"))
    await controller.execute(action())
    second = await controller.execute(action("new-request"))
    assert second.action_id == "request" and second.state == ActionState.SUBMITTED
    assert sim.submission_count == 1


async def test_shadow_is_generated_and_does_not_submit(tmp_path):
    sim = simulator()
    controller = OperateController(sim, SQLiteActionJournal(tmp_path / "journal.db"))
    result = await controller.execute(action(mode=ExecutionMode.SHADOW))
    assert result.state == ActionState.GENERATED
    assert sim.submission_count == 0
    assert (await sim.observe(ref(), SCOPE)).targets == [ORIGIN]


async def test_restart_releases_unsubmitted_live_intent_without_submitting_shadow(tmp_path):
    sim = simulator()
    path = tmp_path / "journal.db"
    original = SQLiteActionJournal(path)
    await original.reserve(action())
    await original.reserve(action("shadow", mode=ExecutionMode.SHADOW))
    restarted = OperateController(sim, SQLiteActionJournal(path))
    pending = await restarted.journal.pending(SCOPE)
    assert [item.action_id for item in pending] == ["request"]
    recovered = await restarted.reconcile(pending[0])
    assert recovered.state == ActionState.FAILED
    assert sim.submission_count == 0
    assert (await restarted.journal.get("shadow")).state == ActionState.GENERATED
    assert (await restarted.execute(action("replanned"))).state == ActionState.SUCCEEDED
    assert sim.submission_count == 1


async def test_duplicate_action_id_is_idempotent_and_conflicts_are_rejected(tmp_path):
    sim = simulator()
    controller = OperateController(sim, SQLiteActionJournal(tmp_path / "journal.db"))
    await controller.execute(action())
    await controller.execute(action())
    assert sim.submission_count == 1
    with pytest.raises(ValueError, match="action_id"):
        await controller.execute(action().model_copy(update={"size_bytes": 30}))


@pytest.mark.parametrize("reason", ["capacity", "stale", "revision"])
async def test_simulator_guards_capacity_stale_epoch_and_revision(tmp_path, reason):
    sim = simulator()
    candidate = action()
    if reason == "capacity":
        sim.set_capacity(CACHE, 1)
    elif reason == "stale":
        sim.set_placements(ref(), SCOPE, [ORIGIN, CACHE])
    else:
        sim.register(ref(revision=2), SCOPE, [ORIGIN], size_bytes=20)
    result = await OperateController(sim, SQLiteActionJournal(tmp_path / "journal.db")).execute(
        candidate
    )
    assert result.state == ActionState.FAILED
    assert result.error


def test_stale_observation_does_not_generate_physical_actions():
    observed = PlacementObservation(
        representation=ref(),
        scope=SCOPE,
        targets=[ORIGIN],
        epoch=1,
        observed_at=datetime.now(UTC) - timedelta(hours=1),
    )
    value = RepresentationValueState(representation=ref(), heat=1, importance=1)
    plan = PlacementPolicy(cache_target=CACHE).plan(
        ref(), value, observed, ResourceBudget(capacity_bytes={CACHE.key: 100}), size_bytes=20
    )
    assert plan.targets == observed.targets
    assert plan.blocked_reason == "stale_observation"


async def test_false_success_with_divergent_placement_remains_unknown(tmp_path):
    sim = simulator()
    sim.faults["request"] = "divergent"
    controller = OperateController(sim, SQLiteActionJournal(tmp_path / "journal.db"))
    result = await controller.execute(action())
    assert result.state == ActionState.UNKNOWN
    assert CACHE not in (await sim.observe(ref(), SCOPE)).targets
    await controller.execute(action())
    assert sim.submission_count == 1


async def test_stale_feedback_cannot_assert_success(tmp_path):
    from aether_agent_memory.operate.models import ActionFeedback

    sim = simulator()
    stale = PlacementObservation(
        representation=ref(),
        scope=SCOPE,
        targets=[ORIGIN, CACHE],
        epoch=0,
        observed_at=datetime.now(UTC) - timedelta(hours=1),
    )
    sim.outcomes["request"] = ActionFeedback(
        action_id="request", state=ActionState.SUCCEEDED, observed=stale
    )
    candidate = action()
    assert not OperateController._satisfied(candidate, stale)


def test_value_ignores_other_task_and_representation_identity():
    memory = Memory(
        id="m",
        type=MemoryType.SEMANTIC,
        tenant_id="t",
        user_id="u",
        agent_id="a",
        session_id="s",
        task_id="task",
        content="x",
    )
    baseline = ValuePolicy().evaluate(ref(), memory, [], [])
    wrong_task = AccessTrace(
        memory_id="m",
        trace_id="trace",
        request_id="request",
        tenant_id="t",
        user_id="u",
        agent_id="a",
        session_id="s",
        task_id="other",
        source="context",
    )
    wrong_rep = wrong_task.model_copy(
        update={"task_id": "task", "metadata": {"representation_id": "another-representation"}}
    )
    value = ValuePolicy().evaluate(ref(), memory, [], [wrong_task, wrong_rep])
    assert value.heat == baseline.heat


async def test_concurrent_controllers_submit_same_intent_once(tmp_path):
    import asyncio

    sim = simulator()
    path = tmp_path / "journal.db"
    controllers = [OperateController(sim, SQLiteActionJournal(path)) for _ in range(2)]
    await asyncio.gather(*(controller.execute(action()) for controller in controllers))
    assert sim.submission_count == 1
    assert (await controllers[0].journal.get("request")).state == ActionState.SUCCEEDED


@pytest.mark.parametrize(
    "budget",
    [
        ResourceBudget(max_actions=0),
        ResourceBudget(max_migration_bytes=0),
        ResourceBudget(network_available=False),
    ],
)
def test_resource_budgets_prevent_new_cache_placement(budget):
    budget.capacity_bytes[CACHE.key] = 100
    observed = PlacementObservation(representation=ref(), scope=SCOPE, targets=[ORIGIN], epoch=1)
    value = RepresentationValueState(representation=ref(), heat=0.9, importance=0.7)
    plan = PlacementPolicy(cache_target=CACHE).plan(ref(), value, observed, budget, size_bytes=20)
    assert plan.targets == [ORIGIN]
    assert plan.blocked_reason is not None


async def test_http_control_timeout_after_success_queries_instead_of_resubmitting(tmp_path):
    import httpx

    from aether_agent_memory.adapters.storage_control import HttpStorageControl
    from aether_agent_memory.operate.models import TierAction

    sim = simulator()
    calls = []

    async def transport(request):
        import json

        calls.append(request.url.path)
        received = TierAction.model_validate(json.loads(request.content))
        if request.url.path == "/actions":
            await sim.submit(received)
            raise httpx.ReadTimeout("lost response", request=request)
        response = await sim.query(received)
        return httpx.Response(200, json=response.model_dump(mode="json"))

    provider = HttpStorageControl("http://control.test")
    await provider.client.aclose()
    provider.client = httpx.AsyncClient(
        base_url="http://control.test", transport=httpx.MockTransport(transport)
    )
    controller = OperateController(provider, SQLiteActionJournal(tmp_path / "journal.db"))
    assert (await controller.execute(action())).state == ActionState.UNKNOWN
    assert (await controller.execute(action())).state == ActionState.SUCCEEDED
    assert calls == ["/actions", "/actions/query"]
    await provider.close()


def test_old_accesses_cool_down_instead_of_permanently_pinning_heat():
    memory = Memory(
        id="m",
        type=MemoryType.SEMANTIC,
        tenant_id="t",
        user_id="u",
        agent_id="a",
        session_id="s",
        content="x",
    )
    old = [
        AccessTrace(
            trace_id=str(i),
            request_id=str(i),
            memory_id="m",
            source="context",
            tenant_id="t",
            user_id="u",
            agent_id="a",
            session_id="s",
            score=1,
            timestamp=datetime.now(UTC) - timedelta(days=30),
        )
        for i in range(100)
    ]
    assert ValuePolicy().evaluate(ref(), memory, [], old).heat < 0.25
