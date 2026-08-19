from __future__ import annotations

from typing import Protocol

from aether_agent_memory.context import ContextRequest
from aether_agent_memory.core.memory import RecalledMemory
from aether_agent_memory.interfaces.managers import MemoryManager
from aether_agent_memory.memory.retrieval.models import RecallCandidate
from aether_agent_memory.runtime.request_context import RequestContext


class RecallSource(Protocol):
    @property
    def name(self) -> str: ...

    async def recall(
        self,
        request: ContextRequest,
        context: RequestContext,
    ) -> list[RecallCandidate]: ...


class MemoryManagerRecallSource:
    def __init__(self, name: str, manager: MemoryManager) -> None:
        self._name = name
        self._manager = manager

    @property
    def name(self) -> str:
        return self._name

    async def recall(
        self,
        request: ContextRequest,
        context: RequestContext,
    ) -> list[RecallCandidate]:
        recalled = await self._manager.recall(request)
        return [_candidate_from_recalled(item, self.name, context) for item in recalled]


class WorkingRecallSource(MemoryManagerRecallSource):
    def __init__(self, manager: MemoryManager) -> None:
        super().__init__("working", manager)


class LongTermRecallSource(MemoryManagerRecallSource):
    def __init__(self, manager: MemoryManager) -> None:
        super().__init__("long_term", manager)


def _candidate_from_recalled(
    item: RecalledMemory,
    source: str,
    context: RequestContext,
) -> RecallCandidate:
    memory = item.memory
    content_ref = memory.metadata.get("content_ref")
    return RecallCandidate(
        memory_id=memory.id,
        content=memory.content,
        content_ref=str(content_ref) if content_ref is not None else None,
        source=source,
        score=item.score,
        memory_type=memory.type,
        created_at=memory.created_at,
        last_access=memory.last_accessed_at,
        trace_metadata={
            "request_id": context.request_id,
            "trace_id": context.trace_id,
            "tenant_id": memory.tenant_id,
            "user_id": memory.user_id,
            "agent_id": memory.agent_id,
        },
    )
