from __future__ import annotations

import argparse
import asyncio
import json
from datetime import timedelta

from aether_agent_memory import (
    AccessStats,
    ContextRequest,
    EmbeddingPipeline,
    EmbeddingRequest,
    FastEmbedClient,
    HeuristicScheduler,
    InMemoryVectorSink,
    MemoryEvent,
    MemoryEventType,
    MemoryService,
    MockContextPackBuilder,
    MockEmbeddingClient,
    MockEpisodicMemoryManager,
    MockSemanticMemoryManager,
    MockSignalEmitter,
    MockWorkingMemoryManager,
    SchedulableObject,
    ScheduleRequest,
    SemanticSignals,
    Settings,
    SourceType,
    StorageTier,
    TextChunker,
)


async def run(*, real_embedding: bool) -> dict[str, object]:
    settings = Settings()
    embedder = (
        FastEmbedClient(model_name=settings.b1_model_name)
        if real_embedding
        else MockEmbeddingClient(dim=32)
    )
    model_name = settings.b1_model_name if real_embedding else "mock-shake256-32"

    vector_sink = InMemoryVectorSink()
    b1 = EmbeddingPipeline(
        embedder=embedder,
        sink=vector_sink,
        chunker=TextChunker(max_chars=80, overlap_chars=10),
        model_name=model_name,
    )
    b1_result = await b1.process(
        EmbeddingRequest(
            text="汛期手册规定：一小时雨量达到 50mm 时进入三级响应，并提前通知西城片区。",
            source_type=SourceType.DOCUMENT,
            source_id="flood-manual-v3",
            object_id="manual-object-1",
            tenant_id="tenant-demo",
        )
    )
    if not b1_result.records:
        raise RuntimeError(b1_result.error_message or "B1 pipeline failed")

    working = MockWorkingMemoryManager(default_ttl=timedelta(hours=1))
    episodic = MockEpisodicMemoryManager(embedder=embedder)
    semantic = MockSemanticMemoryManager(embedder=embedder)
    emitter = MockSignalEmitter()
    builder = MockContextPackBuilder(working=working, episodic=episodic, semantic=semantic)
    b2 = MemoryService(
        working=working,
        episodic=episodic,
        semantic=semantic,
        builder=builder,
        emitter=emitter,
    )

    await b2.ingest(
        MemoryEvent(
            event_type=MemoryEventType.USER_MEMORY,
            session_id="session-1",
            agent_id="agent-1",
            user_id="user-1",
            tenant_id="tenant-demo",
            source_id="flood-manual-v3",
            content="一小时雨量达到 50mm 时进入三级响应。",
            source=SourceType.DOCUMENT,
            importance=1.0,
        )
    )
    await b2.ingest(
        MemoryEvent(
            event_type=MemoryEventType.TOOL_RESULT,
            session_id="session-1",
            agent_id="agent-1",
            user_id="user-1",
            tenant_id="tenant-demo",
            task_id="rainfall-response",
            source_id="weather-tool",
            content="西城站最近一小时雨量为 58mm。",
            source=SourceType.TOOL,
            importance=0.9,
        )
    )
    context = await b2.before_inference(
        ContextRequest(
            session_id="session-1",
            agent_id="agent-1",
            user_id="user-1",
            tenant_id="tenant-demo",
            task_id="rainfall-response",
            query="西城站应该进入什么响应级别？",
            max_tokens=256,
        )
    )
    archived = await b2.archive_session(
        session_id="session-1",
        agent_id="agent-1",
        user_id="user-1",
        tenant_id="tenant-demo",
    )

    scheduler = HeuristicScheduler()
    schedule_result = await scheduler.run_once(
        ScheduleRequest(
            objects=[
                SchedulableObject(
                    object_id=archived[0].id,
                    object_type="episodic_memory",
                    current_tier=StorageTier.L2_HDD,
                    tenant_id="tenant-demo",
                    access=AccessStats(
                        access_frequency=0.95,
                        recency_score=1.0,
                        hit_rate=0.9,
                    ),
                    semantic=SemanticSignals(
                        semantic_relevance=0.9,
                        importance=0.9,
                        task_relevance=0.85,
                    ),
                    business_priority=0.9,
                )
            ]
        )
    )
    entry = schedule_result.entries[0]
    return {
        "embedding": {
            "provider": model_name,
            "chunks": len(b1_result.records),
            "dimension": len(b1_result.records[0].vector),
            "latency_ms": round(b1_result.latency_ms, 3),
        },
        "context_pack": {
            "memories": len(context.memories),
            "memory_refs": context.memory_refs,
            "evidence_refs": context.evidence_refs,
            "budget_info": context.budget_info,
        },
        "archive": {"episodic_memories": len(archived)},
        "schedule": {
            "policy_version": entry.action.policy_version,
            "score": round(entry.action.score, 3),
            "action": entry.action.action_type.value,
            "target_tier": entry.action.target_tier.value if entry.action.target_tier else None,
            "execute_status": entry.feedback.execute_status.value,
            "action_log_entries": len(scheduler.action_log.entries),
        },
        "signals_emitted": len(emitter.signals),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the P3 B1→B2→B3 closed-loop smoke demo")
    parser.add_argument(
        "--real-embedding",
        action="store_true",
        help="Use the optional FastEmbed CPU ONNX provider instead of deterministic MockEmbedding",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    print(
        json.dumps(
            asyncio.run(run(real_embedding=args.real_embedding)), ensure_ascii=False, indent=2
        )
    )
