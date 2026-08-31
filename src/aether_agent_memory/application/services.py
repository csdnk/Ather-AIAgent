"""Application use cases for the P3 MemoryRuntime facade."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime
from uuid import uuid4

from aether_agent_memory.application.scope import require_agent_scope, require_session_scope
from aether_agent_memory.b1 import EmbeddingRequest, EmbeddingResult
from aether_agent_memory.b2 import MemoryEvent
from aether_agent_memory.b3 import ScheduleRequest, ScheduleRunResult
from aether_agent_memory.context import ContextPack, ContextRequest
from aether_agent_memory.context_store.access import (
    item_is_visible_to_scope,
    uri_is_visible_to_scope,
)
from aether_agent_memory.context_store.mapping import scope_uri
from aether_agent_memory.context_store.models import (
    ContextItem,
    ContextItemKind,
    ContextLayer,
    ContextSearchQuery,
    ContextSearchResult,
    RetrievalTrace,
)
from aether_agent_memory.context_store.reindex import ReindexReport
from aether_agent_memory.context_store.uri import AetherUri
from aether_agent_memory.core.enums import MemoryType, SourceType
from aether_agent_memory.core.memory import Memory
from aether_agent_memory.core.scope import Scope
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
from aether_agent_memory.runtime.errors import (
    ConflictError,
    DependencyUnavailableError,
    ScopeError,
)
from aether_agent_memory.runtime.ids import is_compact_id
from aether_agent_memory.runtime.ports import VectorSearchPort
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
        require_session_scope(
            context,
            expected=Scope(
                tenant_id=event.tenant_id,
                user_id=event.user_id,
                agent_id=event.agent_id,
                session_id=event.session_id,
                task_id=event.task_id,
            ),
            operation="memory write",
        )
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
        await enqueue_projection_work(self._dependencies, memory)
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
        require_session_scope(
            context,
            expected=Scope(
                tenant_id=tenant_id,
                user_id=user_id,
                agent_id=agent_id,
                session_id=session_id,
            ),
            operation="long-memory ingestion",
        )
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
        # Northbound v1 exposes no separate scope fields on task reads. The
        # server-issued 128-bit task id is therefore the capability boundary;
        # reject malformed/low-entropy ids before they reach the Redis adapter.
        if not is_compact_id(task_id):
            return None
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
        require_agent_scope(
            context,
            expected=Scope(
                tenant_id=tenant_id,
                user_id=user_id,
                agent_id=agent_id,
                task_id=task_id,
            ),
            operation="memory search",
        )
        if task_id != context.task_id:
            raise ScopeError(
                "memory search task scope does not match request context",
                trace_id=context.trace_id,
            )
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
        await record_access_many(
            self._dependencies,
            [
                AccessTrace(
                    trace_id=context.trace_id,
                    request_id=context.request_id,
                    memory_id=item.memory_id,
                    source=result.backend,
                    hit=True,
                    score=item.score,
                )
                for item in result.items
                if item.memory_id is not None
            ],
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
        require_session_scope(
            context,
            expected=Scope(
                tenant_id=request.tenant_id,
                user_id=request.user_id,
                agent_id=request.agent_id,
                session_id=request.session_id,
                task_id=request.task_id,
            ),
            operation="context build",
        )
        if self._dependencies.retrieval is None:
            pack = await self._dependencies.context_builder.build_context(
                request, context
            )
        else:
            pack = _empty_context_pack(request, context)
            pack = await self._dependencies.retrieval.augment_context(
                pack, request, context
            )
        await record_access_many(
            self._dependencies,
            [
                AccessTrace(
                    trace_id=context.trace_id,
                    request_id=context.request_id,
                    memory_id=memory_id,
                    source="context",
                    hit=True,
                    score=score,
                )
                for memory_id, score in pack.recall_scores.items()
            ],
        )
        return pack


class GetRetrievalTraceUseCase:
    def __init__(self, dependencies: RuntimeDependencies) -> None:
        self._dependencies = dependencies

    async def execute(
        self,
        trace_id: str,
        context: RequestContext,
    ) -> RetrievalTrace | None:
        store = self._dependencies.retrieval_trace_store
        if store is None:
            raise DependencyUnavailableError(
                "retrieval trace store is not configured",
                component=RuntimeComponent.P3.value,
                trace_id=context.trace_id,
            )
        try:
            trace = await store.get(trace_id)
        except Exception as exc:
            raise DependencyUnavailableError(
                "retrieval trace store is unavailable",
                component=RuntimeComponent.P3.value,
                trace_id=context.trace_id,
            ) from exc
        if trace is None:
            return None
        _enforce_trace_scope(trace, context)
        return trace


class GetContextItemUseCase:
    def __init__(self, dependencies: RuntimeDependencies) -> None:
        self._dependencies = dependencies

    async def execute(
        self,
        uri: AetherUri,
        context: RequestContext,
    ) -> ContextItem | None:
        catalog = self._dependencies.context_catalog
        if catalog is None:
            raise DependencyUnavailableError(
                "context catalog is not configured",
                component=RuntimeComponent.P3.value,
                trace_id=context.trace_id,
            )
        _require_context_scope(context)
        _enforce_uri_scope(uri, context)
        item = await catalog.get(uri)
        if item is None:
            return None
        _enforce_item_scope(item, context)
        return item


class ListContextChildrenUseCase:
    def __init__(self, dependencies: RuntimeDependencies) -> None:
        self._dependencies = dependencies

    async def execute(
        self,
        parent: AetherUri,
        context: RequestContext,
        *,
        kind: ContextItemKind | None = None,
    ) -> list[ContextItem]:
        catalog = self._dependencies.context_catalog
        if catalog is None:
            raise DependencyUnavailableError(
                "context catalog is not configured",
                component=RuntimeComponent.P3.value,
                trace_id=context.trace_id,
            )
        _require_context_scope(context)
        _enforce_uri_scope(parent, context)
        items = await catalog.list_children(parent, kind=kind)
        for item in items:
            _enforce_item_scope(item, context)
        return items


class SearchContextUseCase:
    def __init__(self, dependencies: RuntimeDependencies) -> None:
        self._dependencies = dependencies

    async def execute(
        self,
        query: ContextSearchQuery,
        context: RequestContext,
    ) -> ContextSearchResult:
        search = self._dependencies.context_search
        if search is None:
            raise DependencyUnavailableError(
                "hierarchical context search is not configured",
                component=RuntimeComponent.P3.value,
                trace_id=context.trace_id,
            )
        _require_context_scope(context)
        root_uri = query.root_uri or scope_uri(context.scope)
        _enforce_uri_scope(root_uri, context)
        resolved = query.model_copy(
            update={
                "scope": context.scope,
                "root_uri": root_uri,
                "trace_id": context.trace_id,
            }
        )
        result = await search.search(resolved)
        for hit in result.hits:
            _enforce_uri_scope(hit.uri, context)
        return result


class ReindexContextUseCase:
    """Rebuild derived Context indexes from the authoritative catalog/content ports."""

    def __init__(self, dependencies: RuntimeDependencies) -> None:
        self._dependencies = dependencies

    async def execute(
        self,
        context: RequestContext,
        *,
        root_uri: AetherUri | None = None,
        layers: tuple[ContextLayer, ...] = (
            ContextLayer.ABSTRACT,
            ContextLayer.OVERVIEW,
        ),
        cursor: str | None = None,
    ) -> ReindexReport:
        reindex = self._dependencies.context_reindex
        if reindex is None:
            raise DependencyUnavailableError(
                "context reindex is not configured",
                component=RuntimeComponent.P3.value,
                trace_id=context.trace_id,
            )
        _require_context_scope(context)
        resolved_root = root_uri or scope_uri(context.scope)
        _enforce_uri_scope(resolved_root, context)
        return await reindex.rebuild(resolved_root, layers=layers, cursor=cursor)


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
        scoped_request = _authorize_schedule_request(request, context)
        result = await self._dependencies.scheduler.schedule(scoped_request, context)
        await persist_action_log(self._dependencies, result)
        return result


def _authorize_schedule_request(
    request: ScheduleRequest,
    context: RequestContext,
) -> ScheduleRequest:
    """Bind scoped B3 candidates before decisions enter the durable action log.

    Unscoped requests remain supported for the legacy internal scheduler path. Once
    either the request, a candidate, or its application context carries scope, the
    request must resolve to one complete agent scope and every explicit candidate
    field must agree with it.
    """
    fields = ("tenant_id", "user_id", "agent_id")
    request_values = tuple(getattr(request, field) for field in fields)
    candidate_has_scope = any(
        getattr(obj, field) is not None for obj in request.objects for field in fields
    )
    context_has_scope = any(getattr(context, field) is not None for field in fields)
    if not any(request_values) and not candidate_has_scope and not context_has_scope:
        return request

    if any(request_values) and not all(request_values):
        raise ScopeError(
            "schedule memory requires complete tenant/user/agent scope",
            trace_id=context.trace_id,
        )
    expected = (
        Scope(
            tenant_id=request.tenant_id,
            user_id=request.user_id,
            agent_id=request.agent_id,
        )
        if all(request_values)
        else None
    )
    scope = require_agent_scope(
        context,
        expected=expected,
        operation="schedule memory",
    )

    scoped_objects = []
    for obj in request.objects:
        for field in fields:
            value = getattr(obj, field)
            if value is not None and value != getattr(scope, field):
                raise ScopeError(
                    "schedule candidate scope does not match request context",
                    trace_id=context.trace_id,
                )
        scoped_objects.append(
            obj.model_copy(
                update={field: getattr(scope, field) for field in fields},
            )
        )
    return request.model_copy(
        update={
            "objects": scoped_objects,
            **{field: getattr(scope, field) for field in fields},
        }
    )


async def append_long_documents_to_context(
    pack: ContextPack,
    request: ContextRequest,
    context: RequestContext,
    *,
    vector_search: VectorSearchPort | None,
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


async def record_access_many(
    dependencies: RuntimeDependencies,
    traces: list[AccessTrace],
) -> None:
    if not traces or dependencies.access_trace is None:
        return
    try:
        record_many = getattr(dependencies.access_trace, "record_many", None)
        if record_many is not None:
            await record_many(traces)
            return
        for trace in traces:
            await dependencies.access_trace.record(trace)
    except Exception:
        return
    try:
        await dependencies.access_trace.record(trace)
    except Exception:
        return


async def enqueue_projection_work(
    dependencies: RuntimeDependencies,
    memory: Memory,
) -> None:
    """Best-effort enqueue of missing derived state after the primary write.

    The Memory fact is authoritative. Queue outages must not roll back a
    successful write; the same work is recoverable through Reconcile API.
    """
    reconciler = dependencies.projection_reconciler
    queue = dependencies.projection_queue
    if reconciler is None or queue is None:
        return
    try:
        for item in await reconciler.plan_for(memory):
            await queue.enqueue(item)
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


def _enforce_trace_scope(trace: RetrievalTrace, context: RequestContext) -> None:
    required = ("tenant_id", "user_id", "agent_id")
    if any(getattr(context, field) is None for field in required):
        raise ScopeError(
            "tenant_id, user_id and agent_id are required to read retrieval traces",
            trace_id=context.trace_id,
        )
    for field in (*required, "session_id"):
        if getattr(trace.scope, field) != getattr(context, field):
            raise ScopeError(
                "retrieval trace does not belong to the requested scope",
                trace_id=context.trace_id,
            )


def _require_context_scope(context: RequestContext) -> None:
    required = ("tenant_id", "user_id", "agent_id", "session_id")
    if any(getattr(context, field) is None for field in required):
        raise ScopeError(
            "tenant_id, user_id, agent_id and session_id are required for context catalog reads",
            trace_id=context.trace_id,
        )


def _enforce_item_scope(item: ContextItem, context: RequestContext) -> None:
    if not item_is_visible_to_scope(item, context.scope):
        raise ScopeError(
            "context item does not belong to the requested scope",
            trace_id=context.trace_id,
        )


def _enforce_uri_scope(uri: AetherUri, context: RequestContext) -> None:
    if not uri_is_visible_to_scope(uri, context.scope):
        raise ScopeError(
            "context URI does not belong to the requested scope",
            trace_id=context.trace_id,
        )


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
