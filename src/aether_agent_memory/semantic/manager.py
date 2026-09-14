from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import TYPE_CHECKING

from aether_agent_memory.context.models import ContextRequest
from aether_agent_memory.core.enums import MemoryState, MemoryType
from aether_agent_memory.core.memory import Memory, RecalledMemory
from aether_agent_memory.interfaces.embedding import EmbeddingClient
from aether_agent_memory.interfaces.memory_store import MemoryStore
from aether_agent_memory.mocks._base import BaseMockMemoryManager, cosine_similarity

if TYPE_CHECKING:
    from aether_agent_memory.b2.milvus_store import MilvusMemoryStore


class MockSemanticMemoryManager(BaseMockMemoryManager):
    def __init__(
        self,
        embedder: EmbeddingClient,
        default_ttl: timedelta | None = None,
        store: MemoryStore | None = None,
        vector_store: MilvusMemoryStore | None = None,
    ) -> None:
        super().__init__(default_ttl=default_ttl, store=store)
        self._embedder = embedder
        self._vector_store = vector_store

    async def write(self, memory: Memory) -> Memory:
        """Persist the fact; success does not imply that projections are ready.

        New facts retain pending projection state. The application enqueues
        derived work after persistence; direct writes are recoverable through
        MemoryProjectionReconciler and the same Projection Worker.
        """
        return await super().write(memory)

    async def recall(self, request: ContextRequest) -> list[RecalledMemory]:
        query_vec = await self._embedder.embed_one(request.query)
        if self._vector_store is not None:
            try:
                hits = await asyncio.to_thread(
                    self._vector_store.search,
                    vector=query_vec,
                    tenant_id=request.tenant_id or "",
                    user_id=request.user_id or "",
                    agent_id=request.agent_id,
                    limit=request.max_candidates,
                )
                recalled: list[RecalledMemory] = []
                for hit in hits:
                    memory = await self._store.get(hit.memory_id)
                    if (
                        memory is not None
                        and memory.type == MemoryType.SEMANTIC
                        and memory.state == MemoryState.ACTIVE
                    ):
                        recalled.append(RecalledMemory(memory=memory, score=hit.score))
                return recalled[: request.max_candidates]
            except Exception:
                # Continue with the Redis primary records when Milvus is unavailable.
                pass
        candidates = [
            m
            for m in await self._scoped(request)
            if m.type == MemoryType.SEMANTIC
            and m.state == MemoryState.ACTIVE
            and self._matches_scope(m, request)
        ]
        scored: list[tuple[float, RecalledMemory]] = []
        for m in candidates:
            score = cosine_similarity(query_vec, m.embedding or [])
            scored.append((score, RecalledMemory(memory=m, score=score)))
        scored.sort(key=lambda pair: pair[0], reverse=True)
        limit = request.max_candidates
        return [pair[1] for pair in scored[:limit]]
