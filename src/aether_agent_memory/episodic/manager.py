from datetime import UTC, datetime, timedelta

from aether_agent_memory.context.models import ContextRequest
from aether_agent_memory.core.enums import MemoryState, MemoryType
from aether_agent_memory.core.memory import Memory, RecalledMemory
from aether_agent_memory.interfaces.embedding import EmbeddingClient
from aether_agent_memory.interfaces.memory_store import MemoryStore
from aether_agent_memory.lifecycle.decay import ebb_decay_weight
from aether_agent_memory.mocks._base import BaseMockMemoryManager, cosine_similarity


class MockEpisodicMemoryManager(BaseMockMemoryManager):
    def __init__(
        self,
        embedder: EmbeddingClient,
        default_ttl: timedelta | None = None,
        half_life_hours: float = 168.0,
        store: MemoryStore | None = None,
    ) -> None:
        super().__init__(default_ttl=default_ttl, store=store)
        self._embedder = embedder
        self._half_life_hours = half_life_hours

    async def write(self, memory: Memory) -> Memory:
        """Persist only the fact; the shared projection pipeline embeds it later."""
        return await super().write(memory)

    async def recall(self, request: ContextRequest) -> list[RecalledMemory]:
        now = datetime.now(UTC)
        query_vec = await self._embedder.embed_one(request.query)
        candidates = [
            m
            for m in await self._scoped(request)
            if m.type == MemoryType.EPISODIC
            and m.state in (MemoryState.ACTIVE, MemoryState.ARCHIVED)
            and self._matches_scope(m, request)
        ]
        scored: list[tuple[float, RecalledMemory]] = []
        for m in candidates:
            sim = cosine_similarity(query_vec, m.embedding or [])
            decay = ebb_decay_weight(m, now=now, half_life_hours=self._half_life_hours)
            score = sim * decay
            scored.append((score, RecalledMemory(memory=m, score=score)))
        scored.sort(key=lambda pair: pair[0], reverse=True)
        limit = request.max_candidates
        return [pair[1] for pair in scored[:limit]]
