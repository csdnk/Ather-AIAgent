from datetime import UTC, datetime
from uuid import uuid4

from aether_agent_memory.b2.compression import (
    CompressionArtifact,
    HybridMemoryCompressor,
    attach_compression_metadata,
)
from aether_agent_memory.b2.compression_store import CompressionArtifactStore
from aether_agent_memory.b2.events import MemoryEvent
from aether_agent_memory.b2.memory_consolidation import MemoryConsolidator
from aether_agent_memory.b2.text_classifier import classify_text
from aether_agent_memory.context.models import ContextPack, ContextRequest
from aether_agent_memory.core.enums import MemoryState, MemoryType, SignalType
from aether_agent_memory.core.memory import Memory
from aether_agent_memory.interfaces.managers import MemoryManager
from aether_agent_memory.interfaces.signal import SignalEmitter
from aether_agent_memory.memory.formation.models import (
    DefaultMemoryFormationPolicy,
    FormationAction,
    MemoryFormationPolicy,
)
from aether_agent_memory.runtime.ports import ContextPackBuilder
from aether_agent_memory.signal.models import MemorySignal


class MemoryService:
    """B2 application service for Agent lifecycle events and scoped context recall."""

    def __init__(
        self,
        *,
        working: MemoryManager,
        episodic: MemoryManager,
        semantic: MemoryManager,
        builder: ContextPackBuilder,
        emitter: SignalEmitter | None = None,
        compressor: HybridMemoryCompressor | None = None,
        compression_store: CompressionArtifactStore | None = None,
        consolidator: MemoryConsolidator | None = None,
        formation_policy: MemoryFormationPolicy | None = None,
        auto_compress_long_memory: bool = True,
    ) -> None:
        self._working = working
        self._episodic = episodic
        self._semantic = semantic
        self._builder = builder
        self._emitter = emitter
        self._compressor = compressor
        self._compression_store = compression_store
        self._consolidator = consolidator
        self._formation_policy = formation_policy or DefaultMemoryFormationPolicy()
        self._auto_compress_long_memory = auto_compress_long_memory

    async def close_store(self) -> None:
        """Close an optional network-backed store owned by the runtime."""
        stores = {
            getattr(manager, "_store", None)
            for manager in (self._working, self._episodic, self._semantic)
        }
        if self._compression_store is not None:
            stores.add(self._compression_store)
        for store in stores:
            close = getattr(store, "close", None)
            if close is not None:
                await close()

    async def compress_memory(
        self,
        memory: Memory,
        *,
        keywords: list[str] | None = None,
        metadata: dict[str, object] | None = None,
    ) -> tuple[Memory, CompressionArtifact]:
        """Persist a compressed Artifact and attach its reference to Memory.

        Foreground ingest uses this only for policy-selected long memories;
        asynchronous long-text workers use the same metadata contract.
        """
        if self._compressor is None or self._compression_store is None:
            raise RuntimeError("compression service is not configured")
        artifact = await self._compressor.compress_and_store(
            memory.content,
            source_memory_id=memory.id,
            source_id=memory.source_id,
            store=self._compression_store,
            keywords=keywords,
            metadata={**memory.metadata, **(metadata or {})},
        )
        updated = attach_compression_metadata(memory, artifact)
        await self._manager_for(memory.type).write(updated)
        return updated, artifact

    async def content_for_embedding(self, memory: Memory) -> str:
        """Return the persisted compressed representation when available."""
        if memory.compression_artifact_id is None or self._compression_store is None:
            return memory.content
        try:
            artifact = await self._compression_store.get(memory.compression_artifact_id)
        except Exception:
            return memory.content
        return artifact.compressed_text if artifact is not None else memory.content

    async def ingest(self, event: MemoryEvent, *, compress: bool | None = None) -> Memory:
        classification = classify_text(event.content)
        decision = self._formation_policy.decide(event)
        if (
            decision.action is not FormationAction.CREATE_MEMORY
            or decision.memory_type is None
        ):
            raise RuntimeError(f"memory formation did not create memory: {decision.action}")
        memory_type = decision.memory_type
        memory = Memory(
            type=memory_type,
            session_id=event.session_id,
            agent_id=event.agent_id,
            user_id=event.user_id,
            tenant_id=event.tenant_id,
            task_id=event.task_id,
            request_id=event.request_id,
            trace_id=event.trace_id,
            source_id=event.source_id,
            object_id=event.object_id,
            content=event.content,
            source=decision.source,
            importance=event.importance,
            tags=[event.event_type.value],
            metadata={
                **event.metadata,
                "event_type": event.event_type.value,
                "evidence_refs": list(event.evidence_refs),
                "category": classification.category,
                "keywords": classification.keywords,
                "classification_confidence": classification.confidence,
            },
        )
        manager = self._semantic if memory_type == MemoryType.SEMANTIC else self._working
        stored = await manager.write(memory)
        if memory_type == MemoryType.SEMANTIC and self._consolidator is not None:
            consolidation = await self._consolidator.consolidate(
                stored,
                manager=self._semantic,
            )
            stored = consolidation.canonical_memory
        if compress is None:
            requested = stored.metadata.get("compress")
            compress = (
                requested
                if isinstance(requested, bool)
                else self._auto_compress_long_memory
                and self._compressor is not None
                and self._compressor.should_compress(stored.content)
            )
        if compress and self._compressor is not None and self._compression_store is not None:
            stored, _ = await self.compress_memory(
                stored,
                keywords=classification.keywords,
                metadata={"compression_trigger": "ingest"},
            )
        await self._emit_best_effort(stored, SignalType.CREATION)
        return stored

    async def before_inference(self, request: ContextRequest) -> ContextPack:
        pack = await self._builder.build(request)
        for memory in pack.memories:
            memory.touch()
            await self._emit_best_effort(memory, SignalType.ACCESS, context_used=True)
        return pack

    def _manager_for(self, memory_type: MemoryType) -> MemoryManager:
        if memory_type == MemoryType.WORKING:
            return self._working
        if memory_type == MemoryType.EPISODIC:
            return self._episodic
        return self._semantic

    async def archive_session(
        self,
        *,
        session_id: str,
        agent_id: str,
        user_id: str | None = None,
        tenant_id: str | None = None,
        trace_id: str | None = None,
        compress: bool = False,
    ) -> list[Memory]:
        working_items = await self._working.query(
            session_id=session_id,
            agent_id=agent_id,
            user_id=user_id,
            tenant_id=tenant_id,
            state=MemoryState.ACTIVE,
        )
        archived: list[Memory] = []
        now = datetime.now(UTC)
        for item in working_items:
            episodic = item.model_copy(
                deep=True,
                update={
                    "id": uuid4().hex,
                    "type": MemoryType.EPISODIC,
                    "state": MemoryState.ACTIVE,
                    "created_at": now,
                    "updated_at": now,
                    "expires_at": None,
                    "trace_id": trace_id or item.trace_id,
                    "metadata": {**item.metadata, "archived_from": item.id},
                },
            )
            stored = await self._episodic.write(episodic)
            if compress:
                stored, _ = await self.compress_memory(stored)
            await self._working.update_state(item.id, MemoryState.ARCHIVED)
            await self._emit_best_effort(stored, SignalType.ARCHIVAL)
            archived.append(stored)
        return archived

    async def _emit(
        self,
        memory: Memory,
        signal_type: SignalType,
        *,
        context_used: bool = False,
    ) -> None:
        if self._emitter is None:
            return
        ttl_seconds = None
        if memory.expires_at is not None:
            ttl_seconds = max(
                int((memory.expires_at - datetime.now(UTC)).total_seconds()),
                0,
            )
        await self._emitter.emit(
            MemorySignal(
                memory_id=memory.id,
                memory_type=memory.type,
                session_id=memory.session_id,
                agent_id=memory.agent_id,
                user_id=memory.user_id,
                tenant_id=memory.tenant_id,
                signal_type=signal_type,
                heat=memory.importance,
                importance=memory.importance,
                use_count=memory.access_count,
                last_used_time=memory.last_accessed_at,
                ttl_seconds=ttl_seconds,
                state=memory.state,
                context_used_flag=context_used,
                source_id=memory.source_id,
                trace_id=memory.trace_id,
                metadata={"event_id": uuid4().hex},
            )
        )

    async def _emit_best_effort(
        self,
        memory: Memory,
        signal_type: SignalType,
        *,
        context_used: bool = False,
    ) -> None:
        """B3 signal failures must not roll back a successful B2 operation."""
        try:
            await self._emit(memory, signal_type, context_used=context_used)
        except Exception:
            return
