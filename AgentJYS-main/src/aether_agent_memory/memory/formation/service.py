from __future__ import annotations

from aether_agent_memory.b2 import MemoryEvent
from aether_agent_memory.core.memory import Memory
from aether_agent_memory.memory.formation.models import FormationStatus, MemoryFormationResult
from aether_agent_memory.memory.models import ProjectionStatus
from aether_agent_memory.runtime.dtos import LongMemorySubmission
from aether_agent_memory.runtime.ports import MemoryEventPort, TaskQueuePort
from aether_agent_memory.runtime.request_context import RequestContext


class MemoryFormationService:
    """Formation facade over the current B2 synchronous and Celery paths."""

    def __init__(
        self,
        *,
        memory_events: MemoryEventPort,
        task_queue: TaskQueuePort | None = None,
    ) -> None:
        self._memory_events = memory_events
        self._task_queue = task_queue

    async def write_event(self, event: MemoryEvent, context: RequestContext) -> Memory:
        return await self._memory_events.write_memory(_with_context(event, context), context)

    async def submit_long_memory(
        self,
        *,
        text: str,
        tenant_id: str,
        user_id: str,
        agent_id: str,
        session_id: str,
        source_id: str,
        object_id: str | None,
        content_ref: str | None,
        context: RequestContext,
    ) -> tuple[LongMemorySubmission | None, MemoryFormationResult]:
        if self._task_queue is None:
            return (
                None,
                MemoryFormationResult(
                    status=FormationStatus.FORMATION_FAILED,
                    compression_status=ProjectionStatus.NOT_IMPLEMENTED,
                    embedding_status=ProjectionStatus.NOT_IMPLEMENTED,
                    projection_status=ProjectionStatus.NOT_IMPLEMENTED,
                    trace_id=context.trace_id,
                ),
            )
        submission = await self._task_queue.submit_long_memory(
            text=text,
            tenant_id=tenant_id,
            user_id=user_id,
            agent_id=agent_id,
            session_id=session_id,
            source_id=source_id,
            object_id=object_id,
            content_ref=content_ref,
            context=context,
        )
        return (
            submission,
            MemoryFormationResult(
                memory_id=submission.memory_id,
                task_id=submission.task_id,
                status=FormationStatus.FORMATION_PENDING,
                compression_status=ProjectionStatus.PENDING,
                embedding_status=ProjectionStatus.PENDING,
                projection_status=ProjectionStatus.PENDING,
                trace_id=submission.trace_id or context.trace_id,
            ),
        )


def _with_context(event: MemoryEvent, context: RequestContext) -> MemoryEvent:
    return event.model_copy(
        update={
            "request_id": event.request_id or context.request_id,
            "trace_id": event.trace_id or context.trace_id,
            "tenant_id": event.tenant_id or context.tenant_id,
            "user_id": event.user_id or context.user_id,
            "agent_id": event.agent_id or context.agent_id,
            "session_id": event.session_id or context.session_id,
            "task_id": event.task_id or context.task_id,
        }
    )
