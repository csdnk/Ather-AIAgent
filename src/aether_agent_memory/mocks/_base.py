import math
from datetime import UTC, datetime, timedelta

from aether_agent_memory.context.models import ContextRequest
from aether_agent_memory.core.enums import MemoryState
from aether_agent_memory.core.exceptions import MemoryNotFoundError
from aether_agent_memory.core.memory import Memory, RecalledMemory
from aether_agent_memory.interfaces.memory_store import MemoryStore
from aether_agent_memory.lifecycle.decay import is_ttl_expired
from aether_agent_memory.lifecycle.state import transition
from aether_agent_memory.persistence.memory_store import InMemoryMemoryStore


def cosine_similarity(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


class BaseMockMemoryManager:
    def __init__(
        self,
        default_ttl: timedelta | None = None,
        store: MemoryStore | None = None,
    ) -> None:
        self._store = store or InMemoryMemoryStore()
        self._default_ttl = default_ttl

    async def write(self, memory: Memory) -> Memory:
        if memory.expires_at is None and self._default_ttl is not None:
            memory.expires_at = datetime.now(UTC) + self._default_ttl
        await self._store.upsert(memory)
        return memory

    async def get(self, memory_id: str) -> Memory | None:
        m = await self._store.get(memory_id)
        if m is not None:
            m.touch()
            await self._store.upsert(m)
        return m

    async def query(
        self,
        *,
        session_id: str | None = None,
        agent_id: str | None = None,
        user_id: str | None = None,
        tenant_id: str | None = None,
        state: MemoryState | None = None,
        tags: list[str] | None = None,
        limit: int = 100,
    ) -> list[Memory]:
        results: list[Memory] = []
        for m in await self._store.list():
            if session_id is not None and m.session_id != session_id:
                continue
            if agent_id is not None and m.agent_id != agent_id:
                continue
            if user_id is not None and m.user_id != user_id:
                continue
            if tenant_id is not None and m.tenant_id != tenant_id:
                continue
            if state is not None and m.state != state:
                continue
            if tags is not None and not set(tags) & set(m.tags):
                continue
            results.append(m)
        return results[:limit]

    async def update_state(self, memory_id: str, new_state: MemoryState) -> Memory:
        m = await self._store.get(memory_id)
        if m is None:
            raise MemoryNotFoundError(memory_id)
        transition(m.state, new_state)
        m.state = new_state
        m.updated_at = datetime.now(UTC)
        await self._store.upsert(m)
        return m

    async def delete(self, memory_id: str) -> bool:
        return await self._store.delete(memory_id)

    async def expire_stale(self, now: datetime | None = None) -> int:
        current = now or datetime.now(UTC)
        count = 0
        for m in await self._store.list():
            if m.state == MemoryState.ACTIVE and is_ttl_expired(m, now=current):
                m.state = MemoryState.EXPIRED
                m.updated_at = current
                await self._store.upsert(m)
                count += 1
        return count

    async def _all(self) -> list[Memory]:
        return await self._store.list()

    async def _scoped(
        self, request: ContextRequest, *, include_session: bool = False
    ) -> list[Memory]:
        """Use a store-level scope lookup when available, with a legacy fallback."""
        scoped = getattr(self._store, "list_scoped", None)
        if scoped is None:
            return await self._all()
        return await scoped(
            tenant_id=request.tenant_id,
            user_id=request.user_id,
            agent_id=request.agent_id,
            session_id=request.session_id if include_session else None,
        )

    async def recall(self, request: ContextRequest) -> list[RecalledMemory]:
        raise NotImplementedError

    @staticmethod
    def _matches_scope(memory: Memory, request: ContextRequest) -> bool:
        if memory.agent_id != request.agent_id:
            return False
        if request.user_id is not None and memory.user_id != request.user_id:
            return False
        return request.tenant_id is None or memory.tenant_id == request.tenant_id
