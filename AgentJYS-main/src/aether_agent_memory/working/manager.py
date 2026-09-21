from datetime import UTC, datetime

from aether_agent_memory.context.models import ContextRequest
from aether_agent_memory.core.enums import MemoryState, MemoryType
from aether_agent_memory.core.memory import RecalledMemory
from aether_agent_memory.mocks._base import BaseMockMemoryManager


class MockWorkingMemoryManager(BaseMockMemoryManager):
    async def recall(self, request: ContextRequest) -> list[RecalledMemory]:
        now = datetime.now(UTC)
        candidates = [
            m
            for m in await self._scoped(request, include_session=True)
            if m.type == MemoryType.WORKING
            and m.state == MemoryState.ACTIVE
            and m.session_id == request.session_id
            and self._matches_scope(m, request)
        ]
        scored: list[tuple[float, RecalledMemory]] = []
        for m in candidates:
            # ``updated_at`` also changes for lifecycle metadata updates.  For
            # working-memory recency, an actual access is the strongest signal;
            # otherwise use the original creation time.
            recency_at = m.last_accessed_at or m.created_at
            age = now - recency_at
            age_seconds = max(age.total_seconds(), 0.0)
            recency = 1.0 / (1.0 + age_seconds / 60.0)
            score = m.importance * recency
            scored.append((score, RecalledMemory(memory=m, score=score)))
        scored.sort(key=lambda pair: pair[0], reverse=True)
        limit = request.max_candidates
        return [pair[1] for pair in scored[:limit]]
