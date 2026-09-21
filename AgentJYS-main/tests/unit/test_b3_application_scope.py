from __future__ import annotations

import pytest

from aether_agent_memory.application.services import ScheduleMemoryUseCase
from aether_agent_memory.b3 import (
    AccessStats,
    HeuristicScheduler,
    SchedulableObject,
    ScheduleRequest,
    SemanticSignals,
)
from aether_agent_memory.core.enums import StorageTier
from aether_agent_memory.runtime.dependencies import RuntimeDependencies
from aether_agent_memory.runtime.errors import ScopeError
from aether_agent_memory.runtime.request_context import RequestContext


class _SchedulerAdapter:
    def __init__(self) -> None:
        self._scheduler = HeuristicScheduler(shadow_mode=True)

    async def schedule(
        self,
        request: ScheduleRequest,
        context: RequestContext,
    ):
        del context
        return await self._scheduler.run_once(request)


def _dependencies() -> RuntimeDependencies:
    return RuntimeDependencies(
        embedding=None,  # type: ignore[arg-type]
        memory_events=None,  # type: ignore[arg-type]
        context_builder=None,  # type: ignore[arg-type]
        scheduler=_SchedulerAdapter(),
    )


def _object(**scope: str) -> SchedulableObject:
    return SchedulableObject(
        object_id="memory-1",
        object_type="memory",
        current_tier=StorageTier.L2_HDD,
        access=AccessStats(access_frequency=1.0, recency_score=1.0),
        semantic=SemanticSignals(semantic_relevance=1.0, importance=1.0),
        **scope,
    )


def _context() -> RequestContext:
    return RequestContext.from_values(
        request_id="schedule-request",
        trace_id="schedule-trace",
        tenant_id="tenant-a",
        user_id="user-a",
        agent_id="agent-a",
    )


@pytest.mark.asyncio
async def test_schedule_inherits_scope_into_candidates_and_action_log() -> None:
    result = await ScheduleMemoryUseCase(_dependencies()).execute(
        ScheduleRequest(objects=[_object()]),
        _context(),
    )

    action = result.actions[0]
    assert (action.tenant_id, action.user_id, action.agent_id) == (
        "tenant-a",
        "user-a",
        "agent-a",
    )
    assert result.entries[0].action == action


@pytest.mark.asyncio
async def test_schedule_rejects_cross_tenant_candidate_before_decision() -> None:
    use_case = ScheduleMemoryUseCase(_dependencies())

    with pytest.raises(ScopeError, match="candidate scope"):
        await use_case.execute(
            ScheduleRequest(
                tenant_id="tenant-a",
                user_id="user-a",
                agent_id="agent-a",
                objects=[_object(tenant_id="tenant-b")],
            ),
            _context(),
        )


@pytest.mark.asyncio
async def test_legacy_unscoped_schedule_remains_compatible() -> None:
    result = await ScheduleMemoryUseCase(_dependencies()).execute(
        ScheduleRequest(objects=[_object()]),
        RequestContext.from_values(request_id="legacy", trace_id="legacy"),
    )

    assert result.actions
    assert result.actions[0].tenant_id is None
