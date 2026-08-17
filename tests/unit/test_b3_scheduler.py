import pytest

from aether_agent_memory.b3 import (
    AccessStats,
    ActionType,
    HeuristicPolicy,
    HeuristicScheduler,
    MockExecutor,
    ResourceState,
    SchedulableObject,
    ScheduleRequest,
    SemanticSignals,
    TierState,
)
from aether_agent_memory.core.enums import StorageTier


def _hot_object(object_id: str = "hot") -> SchedulableObject:
    return SchedulableObject(
        object_id=object_id,
        object_type="memory",
        current_tier=StorageTier.L2_HDD,
        access=AccessStats(access_frequency=1.0, recency_score=1.0, hit_rate=1.0),
        semantic=SemanticSignals(
            semantic_relevance=1.0,
            importance=1.0,
            task_relevance=0.8,
        ),
        business_priority=1.0,
    )


@pytest.mark.unit
def test_policy_outputs_promote_demote_prefetch_and_pin() -> None:
    policy = HeuristicPolicy()
    cold = SchedulableObject(
        object_id="cold",
        object_type="document",
        current_tier=StorageTier.L1_NVME,
    )
    prefetch = SchedulableObject(
        object_id="future",
        object_type="memory",
        current_tier=StorageTier.L3_OBJECT,
        semantic=SemanticSignals(task_relevance=0.95),
    )
    pinned = _hot_object("pinned").model_copy(update={"pinned": True})
    request = ScheduleRequest(objects=[_hot_object(), cold, prefetch, pinned])

    actions = policy.decide(request)

    assert [action.action_type for action in actions] == [
        ActionType.PROMOTE,
        ActionType.DEMOTE,
        ActionType.PREFETCH,
        ActionType.PIN,
    ]
    assert actions[0].target_tier == StorageTier.L1_NVME
    assert actions[1].target_tier == StorageTier.L2_HDD
    assert all(action.policy_version == "heuristic-v1" for action in actions)
    assert all("frequency=" in action.reason for action in actions)
    assert actions[0].request_id == request.request_id
    assert actions[0].object_type == "memory"
    assert actions[0].score_frequency == 1.0


@pytest.mark.unit
def test_policy_keeps_object_when_hot_target_is_full() -> None:
    resources = ResourceState(
        tiers={
            StorageTier.L1_NVME: TierState(
                tier=StorageTier.L1_NVME,
                capacity_total=100,
                capacity_used=95,
            )
        }
    )
    action = HeuristicPolicy().decide(
        ScheduleRequest(objects=[_hot_object()], resource_state=resources)
    )[0]
    assert action.action_type == ActionType.KEEP


@pytest.mark.unit
def test_high_migration_cost_reduces_score() -> None:
    policy = HeuristicPolicy()
    obj = _hot_object()
    low_cost, _ = policy.score(obj, ResourceState(migration_cost_score=0.0))
    high_cost, _ = policy.score(obj, ResourceState(migration_cost_score=1.0))
    assert low_cost > high_cost


@pytest.mark.unit
async def test_scheduler_records_failure_and_keep_fallback() -> None:
    executor = MockExecutor(
        initial_tiers={"hot": StorageTier.L2_HDD},
        fail_object_ids={"hot"},
    )
    scheduler = HeuristicScheduler(executor=executor)
    result = await scheduler.run_once(ScheduleRequest(objects=[_hot_object()]))

    entry = result.entries[0]
    assert entry.feedback.execute_status == "failed"
    assert entry.fallback_action is not None
    assert entry.fallback_action.action_type == ActionType.KEEP
    assert entry.fallback_feedback is not None
    assert entry.fallback_feedback.execute_status == "success"
    assert len(scheduler.action_log.entries) == 1
    assert executor.current_tiers["hot"] == StorageTier.L2_HDD


@pytest.mark.unit
async def test_scheduler_retry_is_executor_idempotent() -> None:
    scheduler = HeuristicScheduler()
    request = ScheduleRequest(objects=[_hot_object()])

    first = await scheduler.run_once(request)
    second = await scheduler.run_once(request)

    assert first.actions[0].action_id == second.actions[0].action_id
    assert first.entries[0].feedback == second.entries[0].feedback


@pytest.mark.unit
async def test_scheduler_periodic_runner_is_configurable() -> None:
    scheduler = HeuristicScheduler()

    async def objects() -> list[SchedulableObject]:
        return [_hot_object()]

    async def resources() -> ResourceState:
        return ResourceState()

    results = await scheduler.run_periodic(
        objects,
        resources,
        interval_seconds=0,
        iterations=2,
    )
    assert len(results) == 2
    assert len(scheduler.action_log.entries) == 2
