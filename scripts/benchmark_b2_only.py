"""B2-only benchmark: excludes B1 HTTP inference and B3 scheduling."""

from __future__ import annotations

import asyncio
import csv
import json
import os
import statistics
import time
from uuid import uuid4

from aether_agent_memory.b2.milvus_store import MilvusMemoryStore
from aether_agent_memory.context.models import ContextRequest
from aether_agent_memory.core.enums import MemoryType
from aether_agent_memory.core.memory import Memory
from aether_agent_memory.episodic.manager import MockEpisodicMemoryManager
from aether_agent_memory.persistence import RedisMemoryStore
from aether_agent_memory.semantic.manager import MockSemanticMemoryManager
from aether_agent_memory.working.manager import MockWorkingMemoryManager


class FixedEmbedder:
    dimension = 512

    async def embed_one(self, text: str) -> list[float]:
        vector = [0.0] * self.dimension
        vector[hash(text) % self.dimension] = 1.0
        return vector


def p99(values: list[float]) -> float:
    return sorted(values)[min(len(values) - 1, int(len(values) * 0.99))]


async def timed(operation) -> float:
    started = time.perf_counter()
    await operation()
    return (time.perf_counter() - started) * 1000


def load_turns(path: str, limit: int) -> list[str]:
    with open(path, encoding="utf-8", newline="") as handle:
        row = next(csv.DictReader(handle))
    return json.loads(row["turns"])["utterance"][:limit]


async def main() -> None:
    turns = load_turns(os.getenv("AETHER_DATASET", "/app/datasets/locomo.csv"), int(os.getenv("AETHER_DATASET_TURNS", "5")))
    namespace = f"aether:b2:only:{uuid4().hex}"
    redis = RedisMemoryStore(os.getenv("AETHER_B2_REDIS_URL", "redis://redis:6379/0"), namespace=namespace)
    embedder = FixedEmbedder()
    milvus = MilvusMemoryStore(os.getenv("AETHER_B2_MILVUS_URI", "http://milvus:19530"), dimension=512)
    working = MockWorkingMemoryManager(store=redis)
    episodic = MockEpisodicMemoryManager(embedder=embedder, store=redis)
    semantic = MockSemanticMemoryManager(embedder=embedder, store=redis, vector_store=milvus)
    working_memories = [Memory(type=MemoryType.WORKING, session_id="b2", agent_id="agent", tenant_id="t", content=t) for t in turns]
    episodic_memories = [Memory(type=MemoryType.EPISODIC, session_id="b2", agent_id="agent", tenant_id="t", content=t) for t in turns]
    semantic_memories = [Memory(type=MemoryType.SEMANTIC, session_id="b2", agent_id="agent", user_id="u", tenant_id="t", content=t) for t in turns]
    try:
        # Warm Redis and Milvus before collecting measurements.
        await working.write(working_memories[0])
        await episodic.write(episodic_memories[0])
        await semantic.write(semantic_memories[0])
        await asyncio.to_thread(milvus.flush)
        for memory in working_memories[1:]:
            await working.write(memory)
        for memory in episodic_memories[1:]:
            await episodic.write(memory)
        semantic_write = [await timed(lambda memory=m: semantic.write(memory)) for m in semantic_memories[1:]]
        await asyncio.to_thread(milvus.flush)
        request = ContextRequest(session_id="b2", agent_id="agent", tenant_id="t", user_id="u", query=turns[-1], max_candidates=5)
        working_read = [await timed(lambda m=m: working.get(m.id)) for m in working_memories]
        episodic_read = [await timed(lambda m=m: episodic.get(m.id)) for m in episodic_memories]
        semantic_recall = [await timed(lambda: semantic.recall(request)) for _ in range(len(semantic_memories))]
        working_recall = [await timed(lambda: working.recall(request)) for _ in range(len(working_memories))]
        print(f"dataset_turns={len(turns)} b2_only=true")
        for name, values in (("working_get", working_read), ("working_recall", working_recall), ("episodic_get", episodic_read), ("semantic_write", semantic_write), ("semantic_recall", semantic_recall)):
            print(f"{name}_p50_ms={statistics.median(values):.3f} {name}_p99_ms={p99(values):.3f}")
        print(f"semantic_hits={len((await semantic.recall(request)))}")
    finally:
        for memory in [*working_memories, *episodic_memories, *semantic_memories]:
            await redis.delete(memory.id)
        await asyncio.to_thread(
            milvus.delete_memories,
            [memory.id for memory in semantic_memories],
        )
        await redis.close()


if __name__ == "__main__":
    asyncio.run(main())
