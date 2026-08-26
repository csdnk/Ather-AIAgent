"""B3 shadow-mode tests: decisions are recorded, execution is skipped."""

from __future__ import annotations

import pytest

from aether_agent_memory.b3.models import (
    AccessStats,
    ExecuteStatus,
    ResourceState,
    SchedulableObject,
    ScheduleAction,
    ScheduleRequest,
    SemanticSignals,
)
from aether_agent_memory.b3.scheduler import HeuristicScheduler
from aether_agent_memory.core.enums import StorageTier


class _NeverExecutor:
    async def execute(self, action: ScheduleAction):
        raise AssertionError("executor must not run in shadow mode")


def _request() -> ScheduleRequest:
    obj = SchedulableObject(
        object_id="obj-1",
        object_type="semantic_memory",
        current_tier=StorageTier.L3_OBJECT,
        size_bytes=100,
        access=AccessStats(
            access_frequency=0.6, recency_score=0.9, hit_rate=0.8, access_count=5
        ),
        semantic=SemanticSignals(
            semantic_relevance=0.9, importance=0.9, task_relevance=0.9
        ),
        business_priority=0.8,
    )
    return ScheduleRequest(objects=[obj], resource_state=ResourceState())


@pytest.mark.asyncio
async def test_shadow_mode_skips_executor_and_marks_entries() -> None:
    scheduler = HeuristicScheduler(executor=_NeverExecutor(), shadow_mode=True)
    result = await scheduler.run_once(_request())

    assert result.actions, "policy should still produce decisions in shadow mode"
    assert len(result.entries) == len(result.actions)
    for entry in result.entries:
        assert entry.feedback.execute_status == ExecuteStatus.SKIPPED
        assert entry.feedback.metadata["shadow_mode"] is True
        assert entry.feedback.metadata["route_mode"] == "shadow"
        assert entry.fallback_feedback is None


@pytest.mark.asyncio
async def test_non_shadow_mode_still_executes() -> None:
    calls: list[str] = []

    class _RecordingExecutor:
        async def execute(self, action: ScheduleAction):
            calls.append(action.action_id)
            from aether_agent_memory.b3.models import ExecutionFeedback

            return ExecutionFeedback(
                action_id=action.action_id,
                object_id=action.object_id,
                action_type=action.action_type,
                execute_status=ExecuteStatus.SUCCESS,
                execute_latency_ms=1.0,
                new_tier=action.target_tier,
                trace_id=action.trace_id,
            )

    scheduler = HeuristicScheduler(executor=_RecordingExecutor(), shadow_mode=False)
    result = await scheduler.run_once(_request())

    assert len(calls) == len(result.actions)
    for entry in result.entries:
        assert entry.feedback.execute_status == ExecuteStatus.SUCCESS
