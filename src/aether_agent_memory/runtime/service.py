from __future__ import annotations

from typing import Any, cast

from aether_agent_memory.application import (
    BuildContextUseCase,
    GetTaskStatusUseCase,
    IngestLongMemoryUseCase,
    ScheduleMemoryUseCase,
    SearchMemoryUseCase,
    WriteMemoryUseCase,
)
from aether_agent_memory.application.services import (
    EmbedTextUseCase,
    _claim_idempotency,
    _complete_idempotency,
    _fail_idempotency,
    append_long_documents_to_context,
    persist_action_log,
    record_access,
    trim_context_to_budget,
)
from aether_agent_memory.b1 import EmbeddingRequest, EmbeddingResult
from aether_agent_memory.b2 import MemoryEvent
from aether_agent_memory.b3 import ScheduleRequest, ScheduleRunResult
from aether_agent_memory.context import ContextPack, ContextRequest
from aether_agent_memory.core.memory import Memory
from aether_agent_memory.memory.formation import MemoryFormationService
from aether_agent_memory.memory.retrieval.models import AccessTrace
from aether_agent_memory.runtime.dependencies import RuntimeDependencies, RuntimeProfile
from aether_agent_memory.runtime.dtos import (
    LongMemorySubmission,
    MemorySearchResult,
    TaskStatusRecord,
)
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
    """P3 facade over application use cases and adapter ports."""

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
        self._embed_text = EmbedTextUseCase(dependencies)
        self._write_memory = WriteMemoryUseCase(
            formation=self.formation,
            dependencies=dependencies,
        )
        self._ingest_long_memory = IngestLongMemoryUseCase(
            formation=self.formation,
            dependencies=dependencies,
        )
        self._get_task_status = GetTaskStatusUseCase(dependencies)
        self._search_memory = SearchMemoryUseCase(dependencies)
        self._build_context = BuildContextUseCase(dependencies)
        self._schedule_memory = ScheduleMemoryUseCase(dependencies)

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
        from aether_agent_memory.memory.retrieval import MemoryRetrievalService
        from aether_agent_memory.memory.retrieval.sources import P2E1RecallSource

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
        vector_search = P2VectorSearchAdapter(legacy_runtime)
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
                CeleryLongMemoryTaskAdapter(redis_url=legacy_runtime.config.redis_url),
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
                vector_search=vector_search,
                retrieval=MemoryRetrievalService([P2E1RecallSource(vector_search)]),
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
        """Deprecated compatibility bridge for smoke/demo paths."""
        return self.dependencies.legacy_runtime

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
        return await self._embed_text.execute(request, context or _context_from_model(request))

    async def write_memory(
        self,
        event: MemoryEvent,
        context: RequestContext | None = None,
    ) -> Memory:
        return await self._write_memory.execute(event, context or _context_from_model(event))

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
    ) -> LongMemorySubmission:
        ctx = context or RequestContext.from_values(
            tenant_id=tenant_id,
            user_id=user_id,
            agent_id=agent_id,
            session_id=session_id,
        )
        return await self._ingest_long_memory.execute(
            text=text,
            tenant_id=tenant_id,
            user_id=user_id,
            agent_id=agent_id,
            session_id=session_id,
            source_id=source_id,
            object_id=object_id,
            content_ref=content_ref,
            context=ctx,
        )

    async def get_task(
        self,
        task_id: str,
        context: RequestContext | None = None,
    ) -> TaskStatusRecord | None:
        return await self._get_task_status.execute(
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
    ) -> MemorySearchResult:
        ctx = context or RequestContext.from_values(
            tenant_id=tenant_id,
            user_id=user_id,
            agent_id=agent_id,
            task_id=task_id,
        )
        return await self._search_memory.execute(
            query=query,
            tenant_id=tenant_id,
            user_id=user_id,
            agent_id=agent_id,
            limit=limit,
            context=ctx,
            task_id=task_id,
        )

    async def build_context(
        self,
        request: ContextRequest,
        context: RequestContext | None = None,
    ) -> ContextPack:
        return await self._build_context.execute(request, context or _context_from_model(request))

    async def _recall_long_documents(
        self,
        pack: ContextPack,
        request: ContextRequest,
        context: RequestContext,
    ) -> None:
        """Compatibility wrapper for older focused tests."""
        await append_long_documents_to_context(
            pack,
            request,
            context,
            vector_search=self.dependencies.vector_search,
        )

    def _trim_to_budget(self, pack: ContextPack) -> None:
        """Compatibility wrapper for older focused tests."""
        trim_context_to_budget(pack)

    async def schedule(
        self,
        request: ScheduleRequest,
        context: RequestContext | None = None,
    ) -> ScheduleRunResult:
        return await self._schedule_memory.execute(
            request,
            context or _context_from_model(request),
        )

    async def _persist_action_log(self, result: ScheduleRunResult) -> None:
        """Compatibility wrapper for older focused tests."""
        await persist_action_log(self.dependencies, result)

    async def record_access(self, trace: AccessTrace) -> None:
        await record_access(self.dependencies, trace)

    async def _claim_idempotency(
        self,
        context: RequestContext,
        *,
        op_type: str,
        request_hash: str,
    ) -> tuple[str, dict[str, object] | None]:
        """Compatibility wrapper for older focused tests."""
        return await _claim_idempotency(
            self.dependencies,
            context,
            op_type=op_type,
            request_hash=request_hash,
        )

    async def _complete_idempotency(
        self,
        context: RequestContext,
        *,
        op_type: str,
        response: dict[str, object],
    ) -> None:
        """Compatibility wrapper for older focused tests."""
        await _complete_idempotency(
            self.dependencies,
            context,
            op_type=op_type,
            response=response,
        )

    async def _fail_idempotency(
        self,
        context: RequestContext,
        *,
        op_type: str,
    ) -> None:
        """Compatibility wrapper for older focused tests."""
        await _fail_idempotency(self.dependencies, context, op_type=op_type)

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
