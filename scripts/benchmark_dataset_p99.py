"""Run Redis + Milvus Semantic Memory latency on a real local dataset."""

from __future__ import annotations

import asyncio
import csv
import json
import os
import statistics
import time
from uuid import uuid4

from aether_agent_memory.b1.sidecar_client import SidecarEmbeddingClient
from aether_agent_memory.b2.milvus_store import MilvusMemoryStore
from aether_agent_memory.context.models import ContextRequest
from aether_agent_memory.core.enums import MemoryType
from aether_agent_memory.core.memory import Memory
from aether_agent_memory.persistence import RedisMemoryStore
from aether_agent_memory.semantic.manager import MockSemanticMemoryManager


def p99(values: list[float]) -> float:
    return sorted(values)[min(len(values) - 1, int(len(values) * 0.99))]


async def measure(operation) -> tuple[object, float]:
    started = time.perf_counter()
    result = await operation()
    return result, (time.perf_counter() - started) * 1000


def load_turns(path: str, limit: int) -> list[str]:
    with open(path, encoding="utf-8", newline="") as handle:
        row = next(csv.DictReader(handle))
    turns = json.loads(row["turns"])
    return list(turns["utterance"][:limit])


async def main() -> None:
    dataset = os.getenv("AETHER_DATASET", "/app/datasets/locomo.csv")
    turns = load_turns(dataset, int(os.getenv("AETHER_DATASET_TURNS", "20")))
    redis = RedisMemoryStore(
        os.getenv("AETHER_B2_REDIS_URL", "redis://redis:6379/0"),
        namespace=f"aether:b2:dataset:{uuid4().hex}",
    )
    embedder = SidecarEmbeddingClient(os.getenv("AETHER_B1_SIDECAR_URL", "http://b1-sidecar:18081"))
    await embedder.ensure_ready()
    milvus = MilvusMemoryStore(
        os.getenv("AETHER_B2_MILVUS_URI", "http://milvus:19530"),
        collection_name=os.getenv("AETHER_B2_MILVUS_COLLECTION", "b2_memory_chunks_v3"),
        dimension=int(embedder.dimension or 512),
    )
    manager = MockSemanticMemoryManager(embedder=embedder, store=redis, vector_store=milvus)
    warmup_memories = [
        Memory(
            type=MemoryType.SEMANTIC,
            session_id="locomo-warmup",
            agent_id="p99-agent",
            user_id="locomo-user",
            tenant_id="locomo-tenant",
            content=turn,
            tags=["locomo", "warmup"],
        )
        for turn in turns
    ]
    memories = [
        Memory(
            type=MemoryType.SEMANTIC,
            session_id="locomo-0",
            agent_id="p99-agent",
            user_id="locomo-user",
            tenant_id="locomo-tenant",
            content=turn,
            tags=["locomo"],
        )
        for turn in turns
    ]
    try:
        for memory in warmup_memories:
            await manager.write(memory)
        await asyncio.to_thread(milvus.flush)
        results = []
        for memory in memories:
            results.append(await measure(lambda memory=memory: manager.write(memory)))
        await asyncio.to_thread(milvus.flush)
        request = ContextRequest(
            session_id="locomo-0",
            agent_id="p99-agent",
            user_id="locomo-user",
            tenant_id="locomo-tenant",
            query="What music and artists were discussed?",
            max_candidates=5,
        )
        recalls = await asyncio.gather(
            *(measure(lambda: manager.recall(request)) for _ in range(len(turns)))
        )
        write_latencies = [latency for _, latency in results]
        recall_latencies = [latency for _, latency in recalls]
        hits = recalls[0][0]
        print(f"dataset={dataset} turns={len(turns)} dimension={embedder.dimension}")
        print(
            f"write_p50_ms={statistics.median(write_latencies):.3f} "
            f"write_p99_ms={p99(write_latencies):.3f}"
        )
        print(
            f"recall_p50_ms={statistics.median(recall_latencies):.3f} "
            f"recall_p99_ms={p99(recall_latencies):.3f}"
        )
        print(f"recall_hits={len(hits)} top_memory_ids={[hit.memory.id for hit in hits]}")
    finally:
        for memory in [*warmup_memories, *memories]:
            await redis.delete(memory.id)
            await asyncio.to_thread(milvus.delete_memory, memory.id)
        await redis.close()
        await embedder.close()


if __name__ == "__main__":
    asyncio.run(main())
