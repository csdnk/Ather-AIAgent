from datetime import UTC, datetime
from uuid import uuid4

from aether_agent_memory.b2.events import MemoryEvent, MemoryEventType
from aether_agent_memory.b2.text_classifier import classify_text
from aether_agent_memory.context.builder import MockContextPackBuilder
from aether_agent_memory.context.models import ContextPack, ContextRequest
from aether_agent_memory.core.enums import MemoryState, MemoryType, SignalType, SourceType
from aether_agent_memory.core.memory import Memory
from aether_agent_memory.interfaces.managers import MemoryManager
from aether_agent_memory.interfaces.signal import SignalEmitter
from aether_agent_memory.signal.models import MemorySignal


class MemoryService:
    """B2 application service for Agent lifecycle events and scoped context recall."""

    def __init__(
        self,
        *,
        working: MemoryManager,
        episodic: MemoryManager,
        semantic: MemoryManager,
        builder: MockContextPackBuilder,
        emitter: SignalEmitter | None = None,
    ) -> None:
        self._working = working
        self._episodic = episodic
        self._semantic = semantic
        self._builder = builder
        self._emitter = emitter

    async def close_store(self) -> None:
        """Close an optional network-backed store owned by the runtime."""
        stores = {getattr(manager, "_store", None) for manager in (self._working, self._episodic, self._semantic)}
        for store in stores:
            close = getattr(store, "close", None)
            if close is not None:
                await close()

    async def ingest(self, event: MemoryEvent) -> Memory:
        classification = classify_text(event.content)
        memory_type = (
            MemoryType.SEMANTIC
            if event.event_type == MemoryEventType.USER_MEMORY
            else MemoryType.WORKING
        )
        source = self._source_for(event)
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
            source=source,
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
        await self._emit_best_effort(stored, SignalType.CREATION)
        return stored

    async def before_inference(self, request: ContextRequest) -> ContextPack:
        pack = await self._builder.build(request)
        for memory in pack.memories:
            memory.touch()
            try:
                await self._manager_for(memory.type).write(memory)
            except Exception as exc:
                pack.complete = False
                pack.status = "degraded"
                pack.degradation_reasons[f"feedback:{memory.type.value}"] = f"{type(exc).__name__}: {exc}"
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
            await self._working.update_state(item.id, MemoryState.ARCHIVED)
            await self._emit_best_effort(stored, SignalType.ARCHIVAL)
            archived.append(stored)
        return archived

    @staticmethod
    def _source_for(event: MemoryEvent) -> SourceType:
        if event.event_type == MemoryEventType.RAG_RESULT:
            return SourceType.RAG
        if event.event_type == MemoryEventType.TOOL_RESULT:
            return SourceType.TOOL
        return event.source

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
