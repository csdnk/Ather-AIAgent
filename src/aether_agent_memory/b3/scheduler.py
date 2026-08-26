import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from uuid import NAMESPACE_URL, uuid5

from aether_agent_memory.b3.executor import ActionExecutor, MockExecutor
from aether_agent_memory.b3.heuristic import HeuristicPolicy
from aether_agent_memory.b3.models import (
    ActionLogEntry,
    ActionType,
    ExecuteStatus,
    ExecutionFeedback,
    ResourceState,
    SchedulableObject,
    ScheduleAction,
    ScheduleRequest,
    ScheduleRunResult,
)


class ActionLog:
    def __init__(self) -> None:
        self._entries: list[ActionLogEntry] = []

    def append(self, entry: ActionLogEntry) -> None:
        self._entries.append(entry)

    @property
    def entries(self) -> list[ActionLogEntry]:
        return list(self._entries)


class HeuristicScheduler:
    def __init__(
        self,
        *,
        policy: HeuristicPolicy | None = None,
        executor: ActionExecutor | None = None,
        action_log: ActionLog | None = None,
        shadow_mode: bool = False,
    ) -> None:
        self.policy = policy or HeuristicPolicy()
        self.executor = executor or MockExecutor()
        self.action_log = action_log or ActionLog()
        self.shadow_mode = shadow_mode

    async def run_once(self, request: ScheduleRequest) -> ScheduleRunResult:
        started_at = datetime.now(UTC)
        actions = self.policy.decide(request)
        entries: list[ActionLogEntry] = []
        for action in actions:
            if self.shadow_mode:
                feedback = self._shadow_feedback(action)
                fallback_action = None
                fallback_feedback = None
            else:
                feedback = await self.executor.execute(action)
                fallback_action = None
                fallback_feedback = None
                if feedback.execute_status == ExecuteStatus.FAILED:
                    fallback_action = self._fallback_keep(action)
                    fallback_feedback = await self.executor.execute(fallback_action)
            entry = ActionLogEntry(
                action=action,
                feedback=feedback,
                fallback_action=fallback_action,
                fallback_feedback=fallback_feedback,
            )
            self.action_log.append(entry)
            entries.append(entry)
        return ScheduleRunResult(
            request_id=request.request_id,
            trace_id=request.trace_id,
            actions=actions,
            entries=entries,
            started_at=started_at,
            completed_at=datetime.now(UTC),
        )

    async def run_periodic(
        self,
        object_source: Callable[[], Awaitable[list[SchedulableObject]]],
        resource_source: Callable[[], Awaitable[ResourceState]],
        *,
        interval_seconds: float = 30.0,
        iterations: int | None = None,
    ) -> list[ScheduleRunResult]:
        results: list[ScheduleRunResult] = []
        completed = 0
        while iterations is None or completed < iterations:
            objects, resources = await asyncio.gather(object_source(), resource_source())
            results.append(
                await self.run_once(ScheduleRequest(objects=objects, resource_state=resources))
            )
            completed += 1
            if iterations is None or completed < iterations:
                await asyncio.sleep(interval_seconds)
        return results

    def _shadow_feedback(self, action: ScheduleAction) -> ExecutionFeedback:
        """Decision-only feedback: the action is recorded but not executed."""
        return ExecutionFeedback(
            action_id=action.action_id,
            object_id=action.object_id,
            action_type=action.action_type,
            execute_status=ExecuteStatus.SKIPPED,
            execute_latency_ms=0.0,
            new_tier=action.source_tier,
            error_code="SHADOW_MODE",
            failure_reason="decision only; execution deferred in shadow mode",
            trace_id=action.trace_id,
            metadata={"shadow_mode": True, "route_mode": "shadow"},
        )

    def _fallback_keep(self, failed: ScheduleAction) -> ScheduleAction:
        return ScheduleAction(
            action_id=uuid5(NAMESPACE_URL, f"{failed.action_id}:fallback-keep").hex,
            request_id=failed.request_id,
            action_type=ActionType.KEEP,
            object_id=failed.object_id,
            object_type=failed.object_type,
            source_tier=failed.source_tier,
            target_tier=failed.source_tier,
            priority=100,
            reason=f"fallback after failed action {failed.action_id}",
            trace_id=failed.trace_id,
            policy_version=self.policy.config.policy_version,
            expected_effect="preserve current state after executor failure",
            callback_required=False,
            score=failed.score,
            score_frequency=failed.score_frequency,
            score_semantic=failed.score_semantic,
            score_decay=failed.score_decay,
            score_cost=failed.score_cost,
            metadata=dict(failed.metadata),
        )
