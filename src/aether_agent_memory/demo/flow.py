"""A traceable B1 -> P2 -> B2 -> B3 demo flow used by demos and smoke tests."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
from uuid import uuid4

from aether_agent_memory.b1 import EmbeddingPipeline, EmbeddingRequest, InMemoryVectorSink, TextChunker
from aether_agent_memory.b2 import MemoryEvent, MemoryEventType, MemoryService
from aether_agent_memory.b3 import (
    AccessStats,
    HeuristicScheduler,
    SchedulableObject,
    ScheduleRunResult,
    ScheduleRequest,
    SemanticSignals,
)
from aether_agent_memory.context import ContextPack, ContextRequest, MockContextPackBuilder
from aether_agent_memory.core.enums import SourceType, StorageTier
from aether_agent_memory.core.memory import P2Ref
from aether_agent_memory.episodic import MockEpisodicMemoryManager
from aether_agent_memory.mocks.embedding import MockEmbeddingClient
from aether_agent_memory.mocks.storage import MockStorageClient
from aether_agent_memory.semantic import MockSemanticMemoryManager
from aether_agent_memory.signal import MockSignalEmitter
from aether_agent_memory.working import MockWorkingMemoryManager


@dataclass(frozen=True)
class FlowEvent:
    sequence: int
    node: str
    action: str
    status: str
    detail: str
    trace_id: str


@dataclass
class FlowReport:
    trace_id: str
    events: list[FlowEvent] = field(default_factory=list)
    p2_ref: P2Ref | None = None
    embedding_count: int = 0
    context_memory_count: int = 0
    schedule: ScheduleRunResult | None = None


class UnifiedDemoRunner:
    """Runs the integration path with real P3 logic and a deterministic local P2 adapter.

    The P2 adapter deliberately uses the existing in-memory test implementations so
    this flow can be run without Cargo, Docker, or a gRPC listener. Production
    deployment replaces this adapter with ``P2GrpcClient``.
    """

    def __init__(self) -> None:
        self.events: list[FlowEvent] = []
        self.trace_id = ""

    def _emit(self, node: str, action: str, status: str, detail: str) -> None:
        self.events.append(
            FlowEvent(
                sequence=len(self.events) + 1,
                node=node,
                action=action,
                status=status,
                detail=detail,
                trace_id=self.trace_id,
            )
        )

    async def run(self) -> FlowReport:
        self.events = []
        self.trace_id = uuid4().hex
        report = FlowReport(trace_id=self.trace_id, events=self.events)

        embedder = MockEmbeddingClient(dim=32)
        storage = MockStorageClient(default_tier=StorageTier.L3_OBJECT)
        vector_sink = InMemoryVectorSink()
        working = MockWorkingMemoryManager(default_ttl=timedelta(hours=1))
        episodic = MockEpisodicMemoryManager(embedder=embedder)
        semantic = MockSemanticMemoryManager(embedder=embedder)
        signals = MockSignalEmitter()
        memory = MemoryService(
            working=working,
            episodic=episodic,
            semantic=semantic,
            builder=MockContextPackBuilder(
                working=working,
                episodic=episodic,
                semantic=semantic,
            ),
            emitter=signals,
        )
        pipeline = EmbeddingPipeline(
            embedder=embedder,
            sink=vector_sink,
            chunker=TextChunker(max_chars=80, overlap_chars=10),
            model_name="mock-sha256-32",
        )

        object_key = "documents/unified-demo.md"
        document = "Aether integrates semantic memory, object storage, vectors, and tier scheduling."
        self._emit("P4 Gateway", "receive document request", "success", "tenant=demo; object=unified-demo")

        report.p2_ref = await storage.put(object_key, document.encode("utf-8"))
        self._emit(
            "P2 E2 Object",
            "persist raw object",
            "success",
            f"key={object_key}; tier={report.p2_ref.tier.value}; bytes={len(document.encode('utf-8'))}",
        )

        embedding = await pipeline.process(
            EmbeddingRequest(
                text=document,
                source_type=SourceType.DOCUMENT,
                request_id="demo-request",
                trace_id=self.trace_id,
                tenant_id="demo",
                source_id="unified-demo",
                object_id=object_key,
            )
        )
        if embedding.status.value != "success":
            self._emit("P3 B1", "chunk and embed", "failed", embedding.error_message or "unknown error")
            raise RuntimeError(embedding.error_message or "B1 embedding failed")
        report.embedding_count = len(embedding.records)
        self._emit(
            "P3 B1",
            "chunk and embed",
            "success",
            f"chunks={report.embedding_count}; dimension={len(embedding.records[0].vector)}",
        )
        self._emit(
            "P2 E1 Vector",
            "upsert semantic vectors",
            "success",
            f"records={len(vector_sink.records)}; collection=demo-local",
        )

        stored = await memory.ingest(
            MemoryEvent(
                event_type=MemoryEventType.RAG_RESULT,
                session_id="demo-session",
                agent_id="demo-agent",
                tenant_id="demo",
                source_id="unified-demo",
                object_id=object_key,
                trace_id=self.trace_id,
                source=SourceType.DOCUMENT,
                content="The unified document is available as a semantically indexed source.",
                importance=1.0,
                evidence_refs=[object_key],
            )
        )
        stored.p2_ref = report.p2_ref
        self._emit(
            "P3 B2",
            "ingest memory event",
            "success",
            f"memory_id={stored.id[:8]}; type={stored.type.value}; signals={len(signals.signals)}",
        )

        context: ContextPack = await memory.before_inference(
            ContextRequest(
                session_id="demo-session",
                agent_id="demo-agent",
                tenant_id="demo",
                query="What does the unified project provide?",
                trace_id=self.trace_id,
            )
        )
        report.context_memory_count = len(context.memories)
        self._emit(
            "P3 B2",
            "build Context Pack",
            "success",
            f"memories={report.context_memory_count}; token_budget={context.budget_info['budget_tokens']}",
        )

        archived = await memory.archive_session(
            session_id="demo-session",
            agent_id="demo-agent",
            tenant_id="demo",
            trace_id=self.trace_id,
        )
        if not archived:
            raise RuntimeError("B2 archive produced no episodic memory")
        archived[0].p2_ref = report.p2_ref
        self._emit(
            "P3 B2",
            "archive session memory",
            "success",
            f"episodic_memory={archived[0].id[:8]}; signals={len(signals.signals)}",
        )

        scheduler = HeuristicScheduler()
        report.schedule = await scheduler.run_once(
            ScheduleRequest(
                trace_id=self.trace_id,
                objects=[
                    SchedulableObject(
                        object_id=object_key,
                        object_type="episodic_memory",
                        current_tier=report.p2_ref.tier,
                        tenant_id="demo",
                        access=AccessStats(access_frequency=1.0, recency_score=1.0, hit_rate=1.0),
                        semantic=SemanticSignals(
                            semantic_relevance=1.0,
                            importance=1.0,
                            task_relevance=1.0,
                        ),
                        business_priority=1.0,
                    )
                ],
            )
        )
        entry = report.schedule.entries[0]
        self._emit(
            "P3 B3",
            "score and recommend tier action",
            "success",
            f"action={entry.action.action_type.value}; target={entry.action.target_tier}; score={entry.action.score:.3f}",
        )
        self._emit(
            "P1/P2 Executor",
            "apply simulated scheduling action",
            entry.feedback.execute_status.value,
            f"new_tier={entry.feedback.new_tier}; latency_ms={entry.feedback.execute_latency_ms:.1f}",
        )
        self._emit("Demo Runner", "complete unified flow", "success", f"nodes={len(self.events)}")
        return report
