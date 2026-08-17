"""Exercise the deployed B1 -> P2 vector/object path with deterministic embeddings."""

from __future__ import annotations

import asyncio
import os
from datetime import timedelta
from typing import Any

from aether_agent_memory.b1 import EmbeddingPipeline, EmbeddingRequest
from aether_agent_memory.b2 import MemoryEvent, MemoryEventType, MemoryService
from aether_agent_memory.b2.p2_bridge import embedding_records, p2_collection_for_scope
from aether_agent_memory.b3 import (
    AccessStats,
    HeuristicScheduler,
    SchedulableObject,
    ScheduleRequest,
    SemanticSignals,
)
from aether_agent_memory.context import ContextRequest, MockContextPackBuilder
from aether_agent_memory.core.enums import SourceType, StorageTier
from aether_agent_memory.episodic import MockEpisodicMemoryManager
from aether_agent_memory.mocks.embedding import MockEmbeddingClient
from aether_agent_memory.p2 import P2GrpcClient, P2StorageClient, P2VectorSink
from aether_agent_memory.semantic import MockSemanticMemoryManager
from aether_agent_memory.signal import MockSignalEmitter
from aether_agent_memory.working import MockWorkingMemoryManager


async def run() -> dict[str, Any]:
    endpoint = os.getenv("AETHER_P2_GRPC", "engine:50052")
    client = P2GrpcClient(endpoint, bucket="p3-smoke", collection="p3-smoke")
    storage = P2StorageClient(client)
    key = "documents/p3-smoke.txt"

    try:
        for attempt in range(20):
            try:
                reference = await storage.put(key, b"Aether P3 integration smoke test.")
                break
            except Exception:
                if attempt == 19:
                    raise
                await asyncio.sleep(1)

        pipeline = EmbeddingPipeline(
            embedder=MockEmbeddingClient(dim=32),
            sink=P2VectorSink(client),
            model_name="mock-sha256-32",
        )
        result = await pipeline.process(
            EmbeddingRequest(
                text="Aether P3 writes traceable semantic vectors into the P2 engine.",
                source_type=SourceType.DOCUMENT,
                source_id="p3-smoke",
                object_id=reference.object_key,
                tenant_id="integration",
            )
        )
        if result.status.value != "success":
            raise RuntimeError(f"embedding pipeline failed: {result.error_code}")
        vector_hits = await client.search_vectors(result.records[0].vector, top_k=1)
        if (
            not vector_hits
            or vector_hits[0].metadata.get("chunk_text") != result.records[0].chunk_text
        ):
            raise RuntimeError("P2 E1 search did not preserve B1 chunk metadata")
        bridge_records = embedding_records(
            {
                "task_id": "p3-smoke-task",
                "memory_id": "p3-smoke-memory",
                "tenant_id": "integration",
                "user_id": "p3-smoke-user",
                "agent_id": "p3-smoke-agent",
                "session_id": "p3-smoke-session",
                "content_ref": f"p2://p3-smoke/{reference.object_key}",
                "object_id": reference.object_key,
            },
            {
                "request_id": result.request_id,
                "trace_id": result.trace_id,
                "source_id": result.source_id,
                "records": [
                    {
                        "chunk_id": result.records[0].chunk_id,
                        "chunk_text": result.records[0].chunk_text,
                        "vector": result.records[0].vector,
                        "embedding_model": result.records[0].embedding_model,
                        "metadata": result.records[0].metadata,
                    }
                ],
            },
            category="fact",
            keywords=["aether"],
        )
        scoped_collection = p2_collection_for_scope(
            "p3-smoke",
            "integration",
            "p3-smoke-user",
            "p3-smoke-agent",
            len(bridge_records[0].vector),
        )
        await client.upsert_vectors(bridge_records, collection=scoped_collection)
        scoped_hits = await client.search_vectors(
            bridge_records[0].vector,
            top_k=1,
            collection=scoped_collection,
        )
        if not scoped_hits or scoped_hits[0].metadata.get("memory_id") != "p3-smoke-memory":
            raise RuntimeError("B2 scoped P2 E1 search did not preserve memory metadata")

        working = MockWorkingMemoryManager(default_ttl=timedelta(hours=1))
        episodic = MockEpisodicMemoryManager(embedder=MockEmbeddingClient(dim=32))
        semantic = MockSemanticMemoryManager(embedder=MockEmbeddingClient(dim=32))
        b2 = MemoryService(
            working=working,
            episodic=episodic,
            semantic=semantic,
            builder=MockContextPackBuilder(
                working=working,
                episodic=episodic,
                semantic=semantic,
            ),
            emitter=MockSignalEmitter(),
        )
        memory = await b2.ingest(
            MemoryEvent(
                event_type=MemoryEventType.RAG_RESULT,
                session_id="p3-smoke-session",
                agent_id="p3-smoke-agent",
                tenant_id="integration",
                source_id="p3-smoke",
                object_id=reference.object_key,
                source=SourceType.DOCUMENT,
                content="The P2 object was embedded and is available for semantic recall.",
                importance=1.0,
            )
        )
        context = await b2.before_inference(
            ContextRequest(
                session_id="p3-smoke-session",
                agent_id="p3-smoke-agent",
                tenant_id="integration",
                query="Which object is available for semantic recall?",
            )
        )

        schedule = await HeuristicScheduler().run_once(
            ScheduleRequest(
                objects=[
                    SchedulableObject(
                        object_id=reference.object_key,
                        object_type="document",
                        current_tier=StorageTier.L3_OBJECT,
                        access=AccessStats(access_frequency=1.0, recency_score=1.0, hit_rate=1.0),
                        semantic=SemanticSignals(semantic_relevance=1.0, importance=1.0),
                        business_priority=1.0,
                    )
                ]
            )
        )
        action = schedule.actions[0]
        feedback = schedule.entries[0].feedback
        summary = (
            f"P2 object={reference.object_key}; vectors={len(result.records)}; "
            f"search_hits={len(vector_hits)}; scoped_hits={len(scoped_hits)}; "
            f"B2 memories={len(context.memories)}; B3 action={action.action_type}"
        )
        return {
            "summary": summary,
            "request_id": action.request_id,
            "trace_id": action.trace_id,
            "events": [
                {
                    "step": 1,
                    "component": "P2-E2",
                    "title": "对象写入",
                    "status": "success",
                    "details": {
                        "对象键": reference.object_key,
                        "存储分段": reference.segment_id,
                        "存储层级": reference.tier.value,
                    },
                },
                {
                    "step": 2,
                    "component": "P3-B1 -> P2-E1",
                    "title": "文本切分、向量化与写入",
                    "status": result.status.value,
                    "details": {
                        "向量数量": len(result.records),
                        "向量维度": len(result.records[0].vector),
                        "模型": result.records[0].embedding_model,
                        "处理耗时毫秒": round(result.latency_ms, 3),
                        "请求 ID": result.request_id,
                        "追踪 ID": result.trace_id,
                    },
                },
                {
                    "step": 3,
                    "component": "P3-B2",
                    "title": "记忆写入与上下文包构建",
                    "status": "success",
                    "details": {
                        "记忆 ID": memory.id,
                        "上下文记忆数": len(context.memories),
                        "记忆引用": ", ".join(context.memory_refs) or "无",
                        "证据引用": ", ".join(context.evidence_refs) or "无",
                        "已用 Token": context.budget_info["used_tokens"],
                    },
                },
                {
                    "step": 4,
                    "component": "P3-B3",
                    "title": "语义分层调度评分",
                    "status": "success",
                    "details": {
                        "总评分": round(action.score, 3),
                        "访问频率评分": round(action.score_frequency, 3),
                        "语义价值评分": round(action.score_semantic, 3),
                        "时效性评分": round(action.score_decay, 3),
                        "迁移成本评分": round(action.score_cost, 3),
                        "策略版本": action.policy_version,
                    },
                },
                {
                    "step": 5,
                    "component": "执行反馈",
                    "title": "调度动作与回执",
                    "status": feedback.execute_status.value,
                    "details": {
                        "调度动作": action.action_type.value,
                        "源层级": action.source_tier.value,
                        "目标层级": action.target_tier.value if action.target_tier else "无",
                        "优先级": action.priority,
                        "预期效果": action.expected_effect,
                        "执行延迟毫秒": feedback.execute_latency_ms,
                        "动作 ID": action.action_id,
                    },
                },
            ],
        }
    finally:
        await client.close()


async def main() -> None:
    print((await run())["summary"])


if __name__ == "__main__":
    asyncio.run(main())
