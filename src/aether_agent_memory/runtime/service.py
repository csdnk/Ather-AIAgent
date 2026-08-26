from __future__ import annotations

import asyncio
from typing import Any, cast
from uuid import uuid4

from aether_agent_memory.b1 import EmbeddingRequest, EmbeddingResult
from aether_agent_memory.b2 import MemoryEvent
from aether_agent_memory.b3 import ScheduleRequest, ScheduleRunResult
from aether_agent_memory.context import ContextPack, ContextRequest
from aether_agent_memory.core.enums import MemoryType, SourceType
from aether_agent_memory.core.memory import Memory
from aether_agent_memory.memory.formation import MemoryFormationService
from aether_agent_memory.memory.retrieval.models import AccessTrace
from aether_agent_memory.persistence.idempotency import ClaimOutcome, payload_hash
from aether_agent_memory.runtime.dependencies import RuntimeDependencies, RuntimeProfile
from aether_agent_memory.runtime.errors import ConflictError, DependencyUnavailableError
from aether_agent_memory.runtime.health import RuntimeHealth
from aether_agent_memory.runtime.legacy import P3Runtime, P3RuntimeConfig
from aether_agent_memory.runtime.ports import HealthCheckPort
from aether_agent_memory.runtime.request_context import RequestContext
from aether_agent_memory.runtime.status import (
    ComponentHealth,
    ComponentStatus,
    RuntimeComponent,
)


class MemoryRuntime:
    """Unified P3 business orchestration entrypoint.

    This is intentionally a strangler facade: it calls the existing B1/B2/B3
    implementation through ports/adapters, while HTTP integration stops
    reaching into concrete Redis, Milvus, Celery, B1 and B3 clients directly.
    """

    def __init__(
        self,
        *,
        dependencies: RuntimeDependencies,
        profile: RuntimeProfile,
    ) -> None:
        self.dependencies = dependencies
        self.profile = profile
        self.formation = MemoryFormationService(
            memory_events=dependencies.memory_events,
            task_queue=dependencies.task_queue,
        )

    @classmethod
    def from_config(
        cls,
        config: P3RuntimeConfig,
        *,
        profile: RuntimeProfile | None = None,
    ) -> MemoryRuntime:
        return cls.from_legacy(
            P3Runtime(config),
            profile=profile or RuntimeProfile.from_environment(),
        )

    @classmethod
    def from_legacy(
        cls,
        legacy_runtime: P3Runtime,
        *,
        profile: RuntimeProfile | None = None,
    ) -> MemoryRuntime:
        from aether_agent_memory.adapters.access_trace import (
            InMemoryAccessTraceAdapter,
            RedisAccessTraceAdapter,
        )
        from aether_agent_memory.adapters.b1_client import LegacyEmbeddingAdapter
        from aether_agent_memory.adapters.b3 import LegacySchedulerAdapter
        from aether_agent_memory.adapters.celery import (
            CeleryLongMemoryTaskAdapter,
            RedisTaskStatusAdapter,
        )
        from aether_agent_memory.adapters.memory_service import (
            LegacyContextAdapter,
            LegacyMemoryEventAdapter,
        )
        from aether_agent_memory.adapters.milvus import MilvusHealthAdapter
        from aether_agent_memory.adapters.p2 import P2ObjectStoreAdapter, P2VectorSearchAdapter
        from aether_agent_memory.adapters.redis import RedisHealthAdapter

        resolved_profile = profile or RuntimeProfile.from_environment()
        idempotency_store = None
        action_log_store = None
        access_trace: Any = InMemoryAccessTraceAdapter()
        if legacy_runtime.config.memory_store == "redis":
            from aether_agent_memory.b3.action_log import RedisActionLogStore
            from aether_agent_memory.persistence.idempotency import RedisIdempotencyStore

            idempotency_store = RedisIdempotencyStore(legacy_runtime.config.redis_url)
            access_trace = RedisAccessTraceAdapter(legacy_runtime.config.redis_url)
            action_log_store = RedisActionLogStore(legacy_runtime.config.redis_url)
        embedding = LegacyEmbeddingAdapter(legacy_runtime)
        scheduler = LegacySchedulerAdapter(legacy_runtime)
        object_store = P2ObjectStoreAdapter(legacy_runtime)
        health_checks: list[HealthCheckPort] = [embedding, object_store, scheduler]
        if legacy_runtime.config.memory_store == "redis":
            health_checks.append(
                RedisHealthAdapter(
                    legacy_runtime.config.redis_url,
                    critical=resolved_profile == RuntimeProfile.PRODUCTION,
                )
            )
        health_checks.extend(
            [
                MilvusHealthAdapter(legacy_runtime.config.milvus_uri),
                CeleryLongMemoryTaskAdapter(
                    redis_url=legacy_runtime.config.redis_url
                ),
            ]
        )
        return cls(
            dependencies=RuntimeDependencies(
                embedding=embedding,
                memory_events=LegacyMemoryEventAdapter(legacy_runtime),
                context_builder=LegacyContextAdapter(legacy_runtime),
                task_queue=CeleryLongMemoryTaskAdapter(
                    redis_url=legacy_runtime.config.redis_url
                ),
                task_status=RedisTaskStatusAdapter(),
                object_store=object_store,
                vector_search=P2VectorSearchAdapter(legacy_runtime),
                scheduler=scheduler,
                access_trace=access_trace,
                idempotency_store=idempotency_store,
                action_log_store=action_log_store,
                health_checks=health_checks,
                legacy_runtime=legacy_runtime,
            ),
            profile=resolved_profile,
        )

    @property
    def legacy_runtime(self) -> Any | None:
        return self.dependencies.legacy_runtime

    @property
    def client(self) -> Any:
        if self.legacy_runtime is None:
            raise AttributeError("MemoryRuntime has no legacy client")
        return self.legacy_runtime.client

    async def close(self) -> None:
        legacy_runtime = self.legacy_runtime
        if legacy_runtime is not None:
            await legacy_runtime.close()

    async def health(self) -> RuntimeHealth:
        components = [
            ComponentHealth(
                component=RuntimeComponent.P3,
                status=ComponentStatus.HEALTHY,
                detail="MemoryRuntime live",
                critical=True,
                metadata={"runtime_profile": self.profile.value},
            )
        ]
        for check in self.dependencies.health_checks:
            try:
                components.append(await check.health())
            except Exception as exc:
                components.append(
                    ComponentHealth(
                        component=RuntimeComponent.P3,
                        status=ComponentStatus.DEGRADED,
                        detail=f"{type(exc).__name__}: {exc}",
                        critical=False,
                    )
                )
        return RuntimeHealth(runtime_profile=self.profile.value, components=components)

    async def embed(
        self,
        request: EmbeddingRequest,
        context: RequestContext | None = None,
    ) -> EmbeddingResult:
        ctx = context or _context_from_model(request)
        return await self.dependencies.embedding.embed(request, ctx)

    async def write_memory(
        self,
        event: MemoryEvent,
        context: RequestContext | None = None,
    ) -> Memory:
        ctx = context or _context_from_model(event)
        outcome, cached = await self._claim_idempotency(
            ctx,
            op_type="memory_event",
            request_hash=payload_hash(event.model_dump(mode="json")),
        )
        if outcome == ClaimOutcome.CACHED.value and cached is not None:
            return Memory.model_validate(cached)
        try:
            memory = await self.formation.write_event(event, ctx)
        except Exception:
            await self._fail_idempotency(ctx, op_type="memory_event")
            raise
        await self._complete_idempotency(
            ctx,
            op_type="memory_event",
            response=memory.model_dump(mode="json"),
        )
        return memory

    async def submit_long_memory(
        self,
        *,
        text: str,
        tenant_id: str,
        user_id: str,
        agent_id: str,
        session_id: str,
        source_id: str,
        object_id: str | None = None,
        content_ref: str | None = None,
        context: RequestContext | None = None,
    ) -> dict[str, Any]:
        ctx = context or RequestContext.from_values(
            tenant_id=tenant_id,
            user_id=user_id,
            agent_id=agent_id,
            session_id=session_id,
        )
        outcome, cached = await self._claim_idempotency(
            ctx,
            op_type="long_text",
            request_hash=payload_hash({"text": text, "source_id": source_id}),
        )
        if outcome == ClaimOutcome.CACHED.value and cached is not None:
            return cached
        try:
            submission = await self._submit_long_memory_uncached(
                text=text,
                tenant_id=tenant_id,
                user_id=user_id,
                agent_id=agent_id,
                session_id=session_id,
                source_id=source_id,
                object_id=object_id,
                content_ref=content_ref,
                ctx=ctx,
            )
        except Exception:
            await self._fail_idempotency(ctx, op_type="long_text")
            raise
        await self._complete_idempotency(
            ctx, op_type="long_text", response=submission
        )
        return submission

    async def _submit_long_memory_uncached(
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
        ctx: RequestContext,
    ) -> dict[str, Any]:
        object_meta: dict[str, str] = {}
        resolved_object_id = object_id
        resolved_content_ref = content_ref
        needs_object_write = resolved_object_id is None or resolved_content_ref is None
        if needs_object_write and self.dependencies.object_store:
            object_key = resolved_object_id or f"b2/long-text/{uuid4().hex}.txt"
            object_meta = await self.dependencies.object_store.put_text(
                text=text,
                object_key=object_key,
                context=ctx,
            )
            resolved_object_id = object_meta["object_key"]
            resolved_content_ref = object_meta["content_ref"]
        submission, formation = await self.formation.submit_long_memory(
            text=text,
            tenant_id=tenant_id,
            user_id=user_id,
            agent_id=agent_id,
            session_id=session_id,
            source_id=source_id,
            object_id=resolved_object_id,
            content_ref=resolved_content_ref,
            context=ctx,
        )
        if not submission:
            raise DependencyUnavailableError(
                "long-memory task queue is not configured",
                component=RuntimeComponent.CELERY.value,
                trace_id=ctx.trace_id,
            )
        submission.update(object_meta)
        submission["formation"] = formation.model_dump(mode="json")
        return submission

    async def get_task(
        self,
        task_id: str,
        context: RequestContext | None = None,
    ) -> dict[str, Any] | None:
        if self.dependencies.task_status is None:
            raise DependencyUnavailableError(
                "task status store is not configured",
                component=RuntimeComponent.REDIS.value,
                trace_id=context.trace_id if context else None,
            )
        return await self.dependencies.task_status.get_task(
            task_id,
            context or RequestContext.from_values(task_id=task_id),
        )

    async def search_memory(
        self,
        *,
        query: str,
        tenant_id: str,
        user_id: str,
        agent_id: str,
        limit: int,
        context: RequestContext | None = None,
        task_id: str | None = None,
    ) -> dict[str, Any]:
        if self.dependencies.vector_search is None:
            raise DependencyUnavailableError(
                "vector search is not configured",
                component=RuntimeComponent.P2.value,
                trace_id=context.trace_id if context else None,
            )
        ctx = context or RequestContext.from_values(
            tenant_id=tenant_id,
            user_id=user_id,
            agent_id=agent_id,
            task_id=task_id,
        )
        result = await self.dependencies.vector_search.search_memory(
            query=query,
            tenant_id=tenant_id,
            user_id=user_id,
            agent_id=agent_id,
            limit=limit,
            context=ctx,
            task_id=task_id,
        )
        for item in result.get("items", []):
            memory_id = item.get("memory_id")
            if memory_id is not None:
                await self.record_access(
                    AccessTrace(
                        trace_id=ctx.trace_id,
                        request_id=ctx.request_id,
                        memory_id=str(memory_id),
                        source=str(result.get("backend", "search")),
                        hit=True,
                        score=float(item.get("score", 0.0)),
                    )
                )
        return result

    async def build_context(
        self,
        request: ContextRequest,
        context: RequestContext | None = None,
    ) -> ContextPack:
        ctx = context or _context_from_model(request)
        pack = await self.dependencies.context_builder.build_context(request, ctx)
        await self._recall_long_documents(pack, request, ctx)
        for memory_id, score in pack.recall_scores.items():
            await self.record_access(
                AccessTrace(
                    trace_id=ctx.trace_id,
                    request_id=ctx.request_id,
                    memory_id=memory_id,
                    source="context",
                    hit=True,
                    score=score,
                )
            )
        return pack

    async def _recall_long_documents(
        self,
        pack: ContextPack,
        request: ContextRequest,
        context: RequestContext,
    ) -> None:
        """Append long-document (P2 E1) recall to the unified context path.

        The existing working/episodic/semantic recall and ranking are untouched;
        long-document hits are appended and reported honestly.  A P2 E1 failure
        degrades the context pack instead of failing the whole request.
        """
        if self.dependencies.vector_search is None or not request.query:
            return
        tenant = context.tenant_id or request.tenant_id
        user = context.user_id or request.user_id
        agent = context.agent_id or request.agent_id
        if not tenant or not user or not agent:
            return
        try:
            search = await self.dependencies.vector_search.search_memory(
                query=request.query,
                tenant_id=tenant,
                user_id=user,
                agent_id=agent,
                limit=request.max_candidates,
                context=context,
            )
        except Exception as exc:
            if "long_document" not in pack.missing_sources:
                pack.missing_sources.append("long_document")
            pack.degradation_reasons["long_document"] = f"{type(exc).__name__}: {exc}"
            pack.status = "degraded"
            pack.complete = False
            return
        for item in search.get("items", []):
            memory = _long_document_memory(item, context)
            score = float(item.get("score", 0.0))
            if any(existing.id == memory.id for existing in pack.memories):
                continue
            pack.memories.append(memory)
            pack.recall_scores[memory.id] = score
            pack.memory_refs.append(memory.id)
            content_ref = item.get("content_ref")
            if content_ref and str(content_ref) not in pack.evidence_refs:
                pack.evidence_refs.append(str(content_ref))
            pack.total_tokens += _estimate_tokens(memory.content)
            pack.assembled_text += (
                f"\n[{len(pack.memory_refs)}] (long_document) {memory.content}"
            )
        self._trim_to_budget(pack)

    def _trim_to_budget(self, pack: ContextPack) -> None:
        """Re-rank and re-budget after all sources (incl. long documents) fused.

        The token budget is applied once across the fused candidate set, so
        appended long-document hits can no longer push ``total_tokens`` above
        ``budget_tokens``.  Ranking stays score-descending.
        """
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

    async def schedule(
        self,
        request: ScheduleRequest,
        context: RequestContext | None = None,
    ) -> ScheduleRunResult:
        if self.dependencies.scheduler is None:
            raise DependencyUnavailableError(
                "scheduler is not configured",
                component=RuntimeComponent.B3.value,
                trace_id=context.trace_id if context else None,
            )
        result = await self.dependencies.scheduler.schedule(
            request,
            context or _context_from_model(request),
        )
        await self._persist_action_log(result)
        return result

    async def _persist_action_log(self, result: ScheduleRunResult) -> None:
        """Best-effort persistence of B3 action entries; never blocks the decision path."""
        store = self.dependencies.action_log_store
        if store is None:
            return
        try:
            await asyncio.to_thread(store.append_entries, result.entries)
        except Exception:
            return

    async def record_access(self, trace: AccessTrace) -> None:
        if self.dependencies.access_trace is None:
            return
        try:
            await self.dependencies.access_trace.record(trace)
        except Exception:
            return

    async def _claim_idempotency(
        self,
        context: RequestContext,
        *,
        op_type: str,
        request_hash: str,
    ) -> tuple[str, dict[str, Any] | None]:
        """Claim an idempotency key for an operation.

        Returns ``(outcome, cached_response)``; ``conflict`` raises.
        """
        store = self.dependencies.idempotency_store
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
            raise ConflictError(
                f"idempotency key is processing or payload differs: {key}"
            )
        return outcome, cached

    async def _complete_idempotency(
        self,
        context: RequestContext,
        *,
        op_type: str,
        response: dict[str, Any],
    ) -> None:
        store = self.dependencies.idempotency_store
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
        self, context: RequestContext, *, op_type: str
    ) -> None:
        store = self.dependencies.idempotency_store
        key = context.idempotency_key
        if store is None or not key:
            return
        await asyncio.to_thread(
            store.fail, key=key, tenant_id=context.tenant_id, op_type=op_type
        )

    async def ingest_memory(self, event: MemoryEvent) -> Memory:
        return await self.write_memory(event)

    async def schedule_once(self, request: ScheduleRequest) -> ScheduleRunResult:
        return await self.schedule(request)

    async def run_knowledge_session(self, **kwargs: Any) -> dict[str, Any]:
        if self.legacy_runtime is None:
            raise DependencyUnavailableError(
                "legacy knowledge-session path is not configured",
                component=RuntimeComponent.P3.value,
            )
        return cast(dict[str, Any], await self.legacy_runtime.run_knowledge_session(**kwargs))

    async def smoke(self) -> dict[str, Any]:
        if self.legacy_runtime is None:
            raise DependencyUnavailableError(
                "legacy smoke path is not configured",
                component=RuntimeComponent.P3.value,
            )
        return cast(dict[str, Any], await self.legacy_runtime.smoke())


def _context_from_model(model: Any) -> RequestContext:
    return RequestContext.from_values(
        request_id=getattr(model, "request_id", None),
        trace_id=getattr(model, "trace_id", None),
        tenant_id=getattr(model, "tenant_id", None),
        user_id=getattr(model, "user_id", None),
        agent_id=getattr(model, "agent_id", None),
        session_id=getattr(model, "session_id", None),
        task_id=getattr(model, "task_id", None),
        deadline_ms=getattr(model, "deadline_ms", None),
    )


def _estimate_tokens(text: str) -> int:
    return max(len(text) // 4, 1)


def _long_document_memory(item: dict[str, Any], context: RequestContext) -> Memory:
    return Memory(
        id=str(item.get("memory_id") or item.get("chunk_id") or uuid4().hex),
        type=MemoryType.SEMANTIC,
        session_id=context.session_id or "",
        agent_id=context.agent_id or "",
        user_id=context.user_id,
        tenant_id=context.tenant_id,
        task_id=item.get("task_id"),
        request_id=context.request_id,
        trace_id=item.get("trace_id") or context.trace_id,
        source_id=item.get("content_ref"),
        content=str(item.get("text", "")),
        source=SourceType.DOCUMENT,
        importance=1.0,
        embedding_status="succeeded",
        vector_projection_status="succeeded",
        metadata={
            "source": "long_document",
            "chunk_id": item.get("chunk_id"),
            "content_ref": item.get("content_ref"),
            "category": item.get("category"),
            "keywords": item.get("keywords", []),
            "embedding_status": "succeeded",
            "vector_projection_status": "succeeded",
        },
    )
