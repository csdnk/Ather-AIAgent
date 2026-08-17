"""Replay normalized LoCoMo/LongMemEval/BEAM JSONL through B2 only.

No B1 HTTP embedding and no B3 scheduling are used. Pass absolute dataset
paths with --locomo and --longmemeval.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import time
from pathlib import Path
from uuid import uuid4

from aether_agent_memory.b2.milvus_store import MilvusMemoryStore
from aether_agent_memory.context.models import ContextRequest
from aether_agent_memory.core.enums import MemoryType
from aether_agent_memory.core.memory import Memory
from aether_agent_memory.episodic.manager import MockEpisodicMemoryManager
from aether_agent_memory.persistence import RedisMemoryStore
from aether_agent_memory.semantic.manager import MockSemanticMemoryManager
from aether_agent_memory.working.manager import MockWorkingMemoryManager


class ReplayEmbedder:
    dimension = 512

    async def embed_one(self, text: str) -> list[float]:
        vector = [0.0] * self.dimension
        vector[hash(text) % self.dimension] = 1.0
        return vector


def read_jsonl(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def p99(values: list[float]) -> float:
    return sorted(values)[min(len(values) - 1, int(len(values) * 0.99))]


async def timed(operation) -> float:
    started = time.perf_counter()
    await operation()
    return (time.perf_counter() - started) * 1000


async def replay_sample(sample: dict, working, episodic, semantic, stats: dict[str, list[float]], ids: list[str]) -> None:
    sample_id = str(sample["sample_id"])
    session_id = f"replay-{sample_id}"
    turns = sample.get("turns", [])
    session_turns: list[tuple[str, dict]] = []
    if turns:
        session_turns = [(sample_id, turn) for turn in turns]
    else:
        for session in sample.get("sessions", []):
            session_id_for_turns = str(session.get("session_id", sample_id))
            session_turns.extend((session_id_for_turns, turn) for turn in session.get("turns", []))
    for index, (source_session_id, turn) in enumerate(session_turns):
        content = turn.get("content") or turn.get("utterance") or ""
        if not content:
            continue
        memory = Memory(type=MemoryType.WORKING, session_id=session_id, agent_id="b2-replay", tenant_id="dataset", source_id=source_session_id, content=content, metadata={"sample_id": sample_id, "turn_index": index, "source_session_id": source_session_id})
        ids.append(memory.id)
        stats["working_write_ms"].append(await timed(lambda m=memory: working.write(m)))
        archived = memory.model_copy(update={"id": uuid4().hex, "type": MemoryType.EPISODIC})
        ids.append(archived.id)
        stats["episodic_write_ms"].append(await timed(lambda m=archived: episodic.write(m)))
    question = sample.get("question") or (turns[-1].get("content", "") if turns else "")
    if not question:
        return
    request = ContextRequest(session_id=session_id, agent_id="b2-replay", tenant_id="dataset", query=question, max_candidates=5)
    stats["query_count"] .append(1)
    started = time.perf_counter()
    await asyncio.gather(working.recall(request), episodic.recall(request), semantic.recall(request))
    stats["context_recall_ms"].append((time.perf_counter() - started) * 1000)
    semantic_memory = Memory(type=MemoryType.SEMANTIC, session_id=session_id, agent_id="b2-replay", user_id="dataset-user", tenant_id="dataset", content=question, metadata={"sample_id": sample_id})
    ids.append(semantic_memory.id)
    stats["semantic_write_ms"].append(await timed(lambda: semantic.write(semantic_memory)))


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--locomo", type=Path, required=True)
    parser.add_argument("--longmemeval", type=Path, required=True)
    parser.add_argument(
        "--beam",
        type=Path,
        help="Optional normalized BEAM JSONL produced by prepare_beam_dataset.py.",
    )
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    samples = read_jsonl(args.locomo) + read_jsonl(args.longmemeval)
    if args.beam is not None:
        samples.extend(read_jsonl(args.beam))
    namespace = f"aether:b2:replay:{uuid4().hex}"
    redis = RedisMemoryStore("redis://redis:6379/0", namespace=namespace)
    milvus = MilvusMemoryStore("http://milvus:19530", dimension=512)
    embedder = ReplayEmbedder()
    working = MockWorkingMemoryManager(store=redis)
    episodic = MockEpisodicMemoryManager(embedder=embedder, store=redis)
    semantic = MockSemanticMemoryManager(embedder=embedder, store=redis, vector_store=milvus)
    stats: dict[str, list[float]] = {name: [] for name in ("working_write_ms", "episodic_write_ms", "semantic_write_ms", "context_recall_ms", "query_count")}
    ids: list[str] = []
    try:
        for sample in samples:
            await replay_sample(sample, working, episodic, semantic, stats, ids)
        await asyncio.to_thread(milvus.flush)
        report = {
            "samples": len(samples),
            "working_writes": len(stats["working_write_ms"]),
            "episodic_writes": len(stats["episodic_write_ms"]),
            "semantic_writes": len(stats["semantic_write_ms"]),
            "queries": len(stats["query_count"]),
        }
        for name in ("working_write_ms", "episodic_write_ms", "semantic_write_ms", "context_recall_ms"):
            values = stats[name]
            report[f"{name}_p50_ms"] = round(statistics.median(values), 3)
            report[f"{name}_p99_ms"] = round(p99(values), 3)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    finally:
        if ids:
            for start in range(0, len(ids), 500):
                await redis._redis.delete(*[redis._key(memory_id) for memory_id in ids[start : start + 500]])
            # A full acceptance replay creates tens of thousands of IDs.  One
            # enormous Milvus boolean expression can destabilise the gRPC
            # request during cleanup, even though the replay and flush already
            # succeeded.  Delete in bounded batches, matching the Redis path.
            for start in range(0, len(ids), 500):
                await asyncio.to_thread(
                    milvus.delete_memories,
                    [memory_id for memory_id in ids[start : start + 500] if memory_id],
                )
        await redis.close()


if __name__ == "__main__":
    asyncio.run(main())
