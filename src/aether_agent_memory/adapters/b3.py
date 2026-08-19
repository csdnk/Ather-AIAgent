from __future__ import annotations

from typing import Any

from aether_agent_memory.b3 import ScheduleRequest, ScheduleRunResult
from aether_agent_memory.runtime.request_context import RequestContext
from aether_agent_memory.runtime.status import ComponentHealth, ComponentStatus, RuntimeComponent


class LegacySchedulerAdapter:
    def __init__(self, legacy_runtime: Any) -> None:
        self._legacy_runtime = legacy_runtime

    async def schedule(
        self,
        request: ScheduleRequest,
        context: RequestContext,
    ) -> ScheduleRunResult:
        scoped_request = request.model_copy(
            update={
                "request_id": request.request_id or context.request_id,
                "trace_id": request.trace_id or context.trace_id,
            }
        )
        result = await self._legacy_runtime.schedule_once(scoped_request)
        if isinstance(result, ScheduleRunResult):
            return result
        return ScheduleRunResult.model_validate(result)

    async def health(self) -> ComponentHealth:
        executor_name = getattr(self._legacy_runtime, "executor_name", "unknown")
        return ComponentHealth(
            component=RuntimeComponent.B3,
            status=ComponentStatus.HEALTHY,
            detail=f"Scheduler ready with {executor_name}",
            critical=False,
        )
