from __future__ import annotations

from typing import Any, cast
from uuid import uuid4

from aether_agent_memory.b1 import EmbeddingRequest, EmbeddingResult
from aether_agent_memory.b2 import MemoryEvent
from aether_agent_memory.b3 import ScheduleRequest, ScheduleRunResult
from aether_agent_memory.context import ContextPack, ContextRequest
from aether_agent_memory.core.memory import Memory
from aether_agent_memory.memory.formation import MemoryFormationService
from aether_agent_memory.memory.retrieval.models import AccessTrace
from aether_agent_memory.runtime.dependencies import RuntimeDependencies, RuntimeProfile
from aether_agent_memory.runtime.errors import DependencyUnavailableError
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
        from aether_agent_memory.adapters.access_trace import InMemoryAccessTraceAdapter
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
                CeleryLongMemoryTaskAdapter(),
            ]
        )
        return cls(
            dependencies=RuntimeDependencies(
                embedding=embedding,
                memory_events=LegacyMemoryEventAdapter(legacy_runtime),
                context_builder=LegacyContextAdapter(legacy_runtime),
                task_queue=CeleryLongMemoryTaskAdapter(),
                task_status=RedisTaskStatusAdapter(),
                object_store=object_store,
                vector_search=P2VectorSearchAdapter(legacy_runtime),
                scheduler=scheduler,
                access_trace=InMemoryAccessTraceAdapter(),
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
        return await self.formation.write_event(event, ctx)

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
        return await self.dependencies.scheduler.schedule(
            request,
            context or _context_from_model(request),
        )

    async def record_access(self, trace: AccessTrace) -> None:
        if self.dependencies.access_trace is None:
            return
        try:
            await self.dependencies.access_trace.record(trace)
        except Exception:
            return

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
