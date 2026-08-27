"""Application use cases for the P3 MemoryRuntime facade."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime
from uuid import uuid4

from aether_agent_memory.b1 import EmbeddingRequest, EmbeddingResult
from aether_agent_memory.b2 import MemoryEvent
from aether_agent_memory.b3 import ScheduleRequest, ScheduleRunResult
from aether_agent_memory.context import ContextPack, ContextRequest
from aether_agent_memory.core.enums import MemoryType, SourceType
from aether_agent_memory.core.memory import Memory
from aether_agent_memory.memory.formation import MemoryFormationService
from aether_agent_memory.memory.retrieval.models import AccessTrace
from aether_agent_memory.memory.retrieval.service import MemoryRetrievalService
from aether_agent_memory.memory.retrieval.sources import P2E1RecallSource
from aether_agent_memory.persistence.idempotency import ClaimOutcome, payload_hash
from aether_agent_memory.runtime.dependencies import RuntimeDependencies
from aether_agent_memory.runtime.dtos import (
    LongMemorySubmission,
    MemorySearchHit,
    MemorySearchResult,
    TaskStatusRecord,
)
from aether_agent_memory.runtime.errors import ConflictError, DependencyUnavailableError
from aether_agent_memory.runtime.request_context import RequestContext
from aether_agent_memory.runtime.status import RuntimeComponent


class EmbedTextUseCase:
    def __init__(self, dependencies: RuntimeDependencies) -> None:
        self._dependencies = dependencies

    async def execute(
        self,
        request: EmbeddingRequest,
        context: RequestContext,
    ) -> EmbeddingResult:
        return await self._dependencies.embedding.embed(request, context)


class WriteMemoryUseCase:
    def __init__(
        self,
        *,
        formation: MemoryFormationService,
        dependencies: RuntimeDependencies,
    ) -> None:
        self._formation = formation
        self._dependencies = dependencies

    async def execute(self, event: MemoryEvent, context: RequestContext) -> Memory:
        outcome, cached = await _claim_idempotency(
            self._dependencies,
            context,
            op_type="memory_event",
            request_hash=payload_hash(event.model_dump(mode="json")),
        )
        if outcome == ClaimOutcome.CACHED.value and cached is not None:
            return Memory.model_validate(cached)
        try:
            memory = await self._formation.write_event(event, context)
        except Exception:
            await _fail_idempotency(self._dependencies, context, op_type="memory_event")
            raise
        await _complete_idempotency(
            self._dependencies,
            context,
            op_type="memory_event",
            response=memory.model_dump(mode="json"),
        )
        return memory


class IngestLongMemoryUseCase:
    def __init__(
        self,
        *,
        formation: MemoryFormationService,
        dependencies: RuntimeDependencies,
    ) -> None:
        self._formation = formation
        self._dependencies = dependencies

    async def execute(
        self,
        *,
        text: str,
        tenant_id: str,
        user_id: str,
        agent_id: str,
        session_id: str,
        source_id: str,
        object_id: str | None,
        content_ref: str | None,
        context: RequestContext,
    ) -> LongMemorySubmission:
        outcome, cached = await _claim_idempotency(
            self._dependencies,
            context,
            op_type="long_text",
            request_hash=payload_hash({"text": text, "source_id": source_id}),
        )
        if outcome == ClaimOutcome.CACHED.value and cached is not None:
            return LongMemorySubmission.from_mapping(cached)
        try:
            submission = await self._submit_uncached(
                text=text,
                tenant_id=tenant_id,
                user_id=user_id,
                agent_id=agent_id,
                session_id=session_id,
                source_id=source_id,
                object_id=object_id,
                content_ref=content_ref,
                context=context,
            )
        except Exception:
            await _fail_idempotency(self._dependencies, context, op_type="long_text")
            raise
        await _complete_idempotency(
            self._dependencies,
            context,
            op_type="long_text",
            response=submission.to_response_dict(),
        )
        return submission

    async def _submit_uncached(
        self,
        *,
        text: str,
        tenant_id: str,
        user_id: str,
        agent_id: str,
        session_id: str,
        source_id: str,
        object_id: str | None,
        content_ref: str | None,
        context: RequestContext,
    ) -> LongMemorySubmission:
        object_ref = None
        resolved_object_id = object_id
        resolved_content_ref = content_ref
        needs_object_write = resolved_object_id is None or resolved_content_ref is None
        if needs_object_write and self._dependencies.object_store:
            object_key = resolved_object_id or f"b2/long-text/{uuid4().hex}.txt"
            object_ref = await self._dependencies.object_store.put_text(
                text=text,
                object_key=object_key,
                context=context,
            )
            resolved_object_id = object_ref.object_key
            resolved_content_ref = object_ref.content_ref
        submission, formation = await self._formation.submit_long_memory(
            text=text,
            tenant_id=tenant_id,
            user_id=user_id,
            agent_id=agent_id,
            session_id=session_id,
            source_id=source_id,
            object_id=resolved_object_id,
            content_ref=resolved_content_ref,
            context=context,
        )
        if submission is None:
            raise DependencyUnavailableError(
                "long-memory task queue is not configured",
                component=RuntimeComponent.CELERY.value,
                trace_id=context.trace_id,
            )
        update: dict[str, object] = {"formation": formation.model_dump(mode="json")}
        if object_ref is not None:
            update.update(
                {
                    "provider": object_ref.provider,
                    "namespace": object_ref.namespace,
                    "object_key": object_ref.object_key,
                    "content_ref": object_ref.content_ref,
                    "provider_metadata": object_ref.metadata,
                }
            )
        return submission.model_copy(update=update)


class GetTaskStatusUseCase:
    def __init__(self, dependencies: RuntimeDependencies) -> None:
        self._dependencies = dependencies

    async def execute(
        self,
        task_id: str,
        context: RequestContext,
    ) -> TaskStatusRecord | None:
        if self._dependencies.task_status is None:
            raise DependencyUnavailableError(
                "task status store is not configured",
                component=RuntimeComponent.REDIS.value,
                trace_id=context.trace_id,
            )
        return await self._dependencies.task_status.get_task(task_id, context)


class SearchMemoryUseCase:
    def __init__(self, dependencies: RuntimeDependencies) -> None:
        self._dependencies = dependencies

    async def execute(
        self,
        *,
        query: str,
        tenant_id: str,
        user_id: str,
        agent_id: str,
        limit: int,
        context: RequestContext,
        task_id: str | None = None,
    ) -> MemorySearchResult:
        if self._dependencies.vector_search is None:
            raise DependencyUnavailableError(
                "vector search is not configured",
                component=RuntimeComponent.P2.value,
                trace_id=context.trace_id,
            )
        result = await self._dependencies.vector_search.search_memory(
            query=query,
            tenant_id=tenant_id,
            user_id=user_id,
            agent_id=agent_id,
            limit=limit,
            context=context,
            task_id=task_id,
        )
        for item in result.items:
            if item.memory_id is not None:
                await record_access(
                    self._dependencies,
                    AccessTrace(
                        trace_id=context.trace_id,
                        request_id=context.request_id,
                        memory_id=item.memory_id,
                        source=result.backend,
                        hit=True,
                        score=item.score,
                    ),
                )
        return result


class BuildContextUseCase:
    def __init__(self, dependencies: RuntimeDependencies) -> None:
        self._dependencies = dependencies

    async def execute(
        self,
        request: ContextRequest,
        context: RequestContext,
    ) -> ContextPack:
        if self._dependencies.retrieval is None:
            pack = await self._dependencies.context_builder.build_context(
                request, context
            )
        else:
            pack = _empty_context_pack(request, context)
            pack = await self._dependencies.retrieval.augment_context(
                pack, request, context
            )
        for memory_id, score in pack.recall_scores.items():
            await record_access(
                self._dependencies,
                AccessTrace(
                    trace_id=context.trace_id,
                    request_id=context.request_id,
                    memory_id=memory_id,
                    source="context",
                    hit=True,
                    score=score,
                ),
            )
        return pack


class ScheduleMemoryUseCase:
    def __init__(self, dependencies: RuntimeDependencies) -> None:
        self._dependencies = dependencies

    async def execute(
        self,
        request: ScheduleRequest,
        context: RequestContext,
    ) -> ScheduleRunResult:
        if self._dependencies.scheduler is None:
            raise DependencyUnavailableError(
                "scheduler is not configured",
                component=RuntimeComponent.B3.value,
                trace_id=context.trace_id,
            )
        result = await self._dependencies.scheduler.schedule(request, context)
        await persist_action_log(self._dependencies, result)
        return result


async def append_long_documents_to_context(
    pack: ContextPack,
    request: ContextRequest,
    context: RequestContext,
    *,
    vector_search: object | None,
) -> None:
    if vector_search is None or not request.query:
        return
    tenant = context.tenant_id or request.tenant_id
    user = context.user_id or request.user_id
    agent = context.agent_id or request.agent_id
    if not tenant or not user or not agent:
        return
    scoped_context = replace(
        context,
        tenant_id=tenant,
        user_id=user,
        agent_id=agent,
    )
    retrieval = MemoryRetrievalService([P2E1RecallSource(vector_search)])
    await retrieval.augment_context(pack, request, scoped_context)


def trim_context_to_budget(pack: ContextPack) -> None:
    if pack.total_tokens <= pack.budget_tokens:
        return
    ranked = sorted(
        pack.memories,
        key=lambda memory: pack.recall_scores.get(memory.id, 0.0),
        reverse=True,
    )
    selected: list[Memory] = []
    total = 0
    for memory in ranked:
        tokens = _estimate_tokens(memory.content)
        if total + tokens > pack.budget_tokens:
            continue
        selected.append(memory)
        total += tokens
    pack.memories = selected
    pack.total_tokens = total
    pack.recall_scores = {
        memory.id: pack.recall_scores.get(memory.id, 0.0) for memory in selected
    }
    pack.memory_refs = [memory.id for memory in selected]
    pack.assembled_text = "\n".join(
        f"[{index + 1}] ({memory.type.value}) {memory.content}"
        for index, memory in enumerate(selected)
    )


def _empty_context_pack(request: ContextRequest, context: RequestContext) -> ContextPack:
    scoped_request = request.model_copy(
        update={
            "request_id": request.request_id or context.request_id,
            "trace_id": request.trace_id or context.trace_id,
            "tenant_id": request.tenant_id or context.tenant_id,
            "user_id": request.user_id or context.user_id,
            "agent_id": request.agent_id or context.agent_id,
            "session_id": request.session_id or context.session_id,
            "task_id": request.task_id or context.task_id,
        }
    )
    return ContextPack(
        request=scoped_request,
        memories=[],
        total_tokens=0,
        budget_tokens=scoped_request.max_tokens,
        recall_scores={},
        assembled_text="",
        built_at=datetime.now(UTC),
        trace_id=scoped_request.trace_id,
        budget_info={
            "used_tokens": 0,
            "budget_tokens": scoped_request.max_tokens,
            "remaining_tokens": scoped_request.max_tokens,
        },
    )


async def record_access(dependencies: RuntimeDependencies, trace: AccessTrace) -> None:
    if dependencies.access_trace is None:
        return
    try:
        await dependencies.access_trace.record(trace)
    except Exception:
        return


async def persist_action_log(
    dependencies: RuntimeDependencies,
    result: ScheduleRunResult,
) -> None:
    store = dependencies.action_log_store
    if store is None:
        return
    try:
        await asyncio.to_thread(store.append_entries, result.entries)
    except Exception:
        return


async def _claim_idempotency(
    dependencies: RuntimeDependencies,
    context: RequestContext,
    *,
    op_type: str,
    request_hash: str,
) -> tuple[str, dict[str, object] | None]:
    store = dependencies.idempotency_store
    key = context.idempotency_key
    if store is None or not key:
        return ClaimOutcome.CLAIMED.value, None
    outcome, cached = await asyncio.to_thread(
        store.claim,
        key=key,
        tenant_id=context.tenant_id,
        op_type=op_type,
        payload_hash=request_hash,
    )
    if outcome == ClaimOutcome.CONFLICT.value:
        raise ConflictError(f"idempotency key is processing or payload differs: {key}")
    return outcome, cached


async def _complete_idempotency(
    dependencies: RuntimeDependencies,
    context: RequestContext,
    *,
    op_type: str,
    response: dict[str, object],
) -> None:
    store = dependencies.idempotency_store
    key = context.idempotency_key
    if store is None or not key:
        return
    await asyncio.to_thread(
        store.complete,
        key=key,
        tenant_id=context.tenant_id,
        op_type=op_type,
        response=response,
    )


async def _fail_idempotency(
    dependencies: RuntimeDependencies,
    context: RequestContext,
    *,
    op_type: str,
) -> None:
    store = dependencies.idempotency_store
    key = context.idempotency_key
    if store is None or not key:
        return
    await asyncio.to_thread(
        store.fail,
        key=key,
        tenant_id=context.tenant_id,
        op_type=op_type,
    )


def _estimate_tokens(text: str) -> int:
    return max(len(text) // 4, 1)


def _long_document_memory(item: MemorySearchHit, context: RequestContext) -> Memory:
    return Memory(
        id=item.memory_id or item.chunk_id or uuid4().hex,
        type=MemoryType.SEMANTIC,
        session_id=context.session_id or "",
        agent_id=context.agent_id or "",
        user_id=context.user_id,
        tenant_id=context.tenant_id,
        task_id=item.task_id,
        request_id=context.request_id,
        trace_id=item.trace_id or context.trace_id,
        source_id=item.content_ref,
        content=item.text,
        source=SourceType.DOCUMENT,
        importance=1.0,
        embedding_status="succeeded",
        vector_projection_status="succeeded",
        metadata={
            "source": "long_document",
            "chunk_id": item.chunk_id,
            "content_ref": item.content_ref,
            "category": item.category,
            "keywords": item.keywords,
            "embedding_status": "succeeded",
            "vector_projection_status": "succeeded",
        },
    )
