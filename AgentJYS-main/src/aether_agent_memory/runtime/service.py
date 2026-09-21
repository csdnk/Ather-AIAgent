from __future__ import annotations

from typing import Any, cast

from aether_agent_memory.application import (
    BuildContextUseCase,
    DeleteResourceUseCase,
    DeleteSkillUseCase,
    GetContextItemUseCase,
    GetRetrievalTraceUseCase,
    GetTaskStatusUseCase,
    IngestLongMemoryUseCase,
    ListContextChildrenUseCase,
    RegisterResourceUseCase,
    RegisterSkillUseCase,
    ReindexContextUseCase,
    ResourceRegistration,
    ScheduleMemoryUseCase,
    SearchContextUseCase,
    SearchMemoryUseCase,
    SkillRegistration,
    WriteMemoryUseCase,
)
from aether_agent_memory.application.context_projection import (
    ContextProjectionWorkerReport,
    ContextProjectionWorkService,
)
from aether_agent_memory.application.projection import (
    ProjectionWorkerReport,
    ProjectionWorkService,
)
from aether_agent_memory.application.scope import require_agent_scope, require_session_scope
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
from aether_agent_memory.application.session import (
    ConsolidateSessionUseCase,
    SessionExtractionWorker,
    SessionExtractionWorkerReport,
)
from aether_agent_memory.b1 import EmbeddingRequest, EmbeddingResult
from aether_agent_memory.b2 import MemoryEvent
from aether_agent_memory.b3 import ScheduleRequest, ScheduleRunResult
from aether_agent_memory.context import ContextPack, ContextRequest
from aether_agent_memory.context_store.models import (
    ContextDeleteResult,
    ContextItem,
    ContextItemKind,
    ContextLayer,
    ContextSearchQuery,
    ContextSearchResult,
    RetrievalTrace,
)
from aether_agent_memory.context_store.reindex import ReindexReport
from aether_agent_memory.context_store.uri import AetherUri
from aether_agent_memory.core.memory import Memory
from aether_agent_memory.memory.formation import MemoryFormationService
from aether_agent_memory.memory.projection import ProjectionWorkItem
from aether_agent_memory.memory.retrieval.models import AccessTrace
from aether_agent_memory.resource.models import ResourceRecord
from aether_agent_memory.runtime.dependencies import RuntimeDependencies, RuntimeProfile
from aether_agent_memory.runtime.dtos import (
    LongMemorySubmission,
    MemorySearchResult,
    TaskStatusRecord,
)
from aether_agent_memory.runtime.errors import DependencyUnavailableError
from aether_agent_memory.runtime.health import RuntimeHealth
from aether_agent_memory.runtime.legacy import P3Runtime, P3RuntimeConfig
from aether_agent_memory.runtime.request_context import RequestContext
from aether_agent_memory.runtime.status import (
    ComponentHealth,
    ComponentStatus,
    RuntimeComponent,
)
from aether_agent_memory.session import (
    SessionCommitResult,
    SessionConsolidationResult,
    SessionMessage,
    SessionRecord,
    SessionService,
)
from aether_agent_memory.skill.models import SkillRecord


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
        self._get_retrieval_trace = GetRetrievalTraceUseCase(dependencies)
        self._get_context_item = GetContextItemUseCase(dependencies)
        self._list_context_children = ListContextChildrenUseCase(dependencies)
        self._search_context = SearchContextUseCase(dependencies)
        self._reindex_context = ReindexContextUseCase(dependencies)
        self._register_resource = (
            RegisterResourceUseCase(
                resources=dependencies.resource_store,
                content_store=(
                    dependencies.object_store if self.profile == RuntimeProfile.PRODUCTION else None
                ),
                context_projection_queue=dependencies.context_projection_queue,
                semantic_index=dependencies.context_semantic_index,
            )
            if dependencies.resource_store is not None
            else None
        )
        self._delete_resource = (
            DeleteResourceUseCase(
                resources=dependencies.resource_store,
                semantic_index=dependencies.context_semantic_index,
            )
            if dependencies.resource_store is not None
            else None
        )
        self._register_skill = (
            RegisterSkillUseCase(
                skills=dependencies.skill_store,
                context_projection_queue=dependencies.context_projection_queue,
            )
            if dependencies.skill_store is not None
            else None
        )
        self._delete_skill = (
            DeleteSkillUseCase(
                skills=dependencies.skill_store,
                semantic_index=dependencies.context_semantic_index,
            )
            if dependencies.skill_store is not None
            else None
        )
        self._projection_work = (
            ProjectionWorkService(
                dependencies.projection_reconciler,
                dependencies.projection_queue,
                dependencies.projection_executor,
            )
            if dependencies.projection_reconciler is not None
            and dependencies.projection_queue is not None
            else None
        )
        self._context_projection_work = (
            ContextProjectionWorkService(
                dependencies.context_projection_queue,
                dependencies.context_projection_executor,
            )
            if dependencies.context_projection_queue is not None
            and dependencies.context_projection_executor is not None
            else None
        )
        self._schedule_memory = ScheduleMemoryUseCase(dependencies)
        self._session_service = (
            SessionService(
                dependencies.session_store,
                extraction_queue=dependencies.session_extraction_queue,
                context_projection_queue=dependencies.context_projection_queue,
            )
            if dependencies.session_store is not None
            else None
        )
        self._consolidate_session = (
            ConsolidateSessionUseCase(
                sessions=self._session_service,
                write_memory=self._write_memory,
                extraction_port=dependencies.memory_extraction,
            )
            if self._session_service is not None
            else None
        )
        self._session_extraction_worker = (
            SessionExtractionWorker(
                queue=dependencies.session_extraction_queue,
                consolidate=self._consolidate_session,
            )
            if dependencies.session_extraction_queue is not None
            and self._consolidate_session is not None
            else None
        )

    @classmethod
    def from_config(
        cls,
        config: P3RuntimeConfig,
        *,
        profile: RuntimeProfile | None = None,
        legacy_runtime: P3Runtime | None = None,
    ) -> MemoryRuntime:
        return cls.from_legacy(
            legacy_runtime or P3Runtime(config),
            profile=profile or RuntimeProfile.from_environment(),
            config=config,
        )

    @classmethod
    def from_legacy(
        cls,
        legacy_runtime: P3Runtime,
        *,
        profile: RuntimeProfile | None = None,
        config: P3RuntimeConfig | None = None,
    ) -> MemoryRuntime:
        from aether_agent_memory.bootstrap.container import build_dependencies_from_legacy

        resolved_profile = profile or RuntimeProfile.from_environment()
        return cls(
            dependencies=build_dependencies_from_legacy(
                legacy_runtime,
                config=config or legacy_runtime.config,
                profile=resolved_profile,
            ),
            profile=resolved_profile,
        )

    @property
    def legacy_runtime(self) -> Any | None:
        """Deprecated compatibility bridge for smoke/demo paths."""
        return self.dependencies.legacy_runtime

    async def close(self) -> None:
        if self.dependencies.scheduler is not None:
            close_scheduler = getattr(self.dependencies.scheduler, "close", None)
            if close_scheduler is not None:
                await close_scheduler()
        if self.dependencies.retrieval is not None:
            await self.dependencies.retrieval.close()
        if self._session_service is not None:
            await self._session_service.close()
        if self._projection_work is not None:
            await self._projection_work.close()
        if self._context_projection_work is not None:
            await self._context_projection_work.close()
        if self._session_extraction_worker is not None:
            await self._session_extraction_worker.close()
        if self.dependencies.memory_extraction is not None:
            close = getattr(self.dependencies.memory_extraction, "close", None)
            if close is not None:
                await close()
        context_catalog = self.dependencies.context_catalog
        if context_catalog is not None:
            close = getattr(context_catalog, "close", None)
            if close is not None:
                await close()
        if self.dependencies.context_index_tombstones is not None:
            await self.dependencies.context_index_tombstones.close()
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

    async def get_retrieval_trace(
        self,
        trace_id: str,
        context: RequestContext,
    ) -> RetrievalTrace | None:
        return await self._get_retrieval_trace.execute(trace_id, context)

    async def get_context_item(
        self,
        uri: AetherUri,
        context: RequestContext,
    ) -> ContextItem | None:
        return await self._get_context_item.execute(uri, context)

    async def list_context_children(
        self,
        parent: AetherUri,
        context: RequestContext,
        *,
        kind: ContextItemKind | None = None,
    ) -> list[ContextItem]:
        return await self._list_context_children.execute(
            parent,
            context,
            kind=kind,
        )

    async def search_context(
        self,
        query: ContextSearchQuery,
        context: RequestContext,
    ) -> ContextSearchResult:
        return await self._search_context.execute(query, context)

    async def reindex_context(
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
        return await self._reindex_context.execute(
            context,
            root_uri=root_uri,
            layers=layers,
            cursor=cursor,
        )

    async def register_resource(
        self,
        registration: ResourceRegistration,
        context: RequestContext,
    ) -> ResourceRecord:
        if self._register_resource is None:
            raise DependencyUnavailableError(
                "resource store is not configured",
                component=RuntimeComponent.P3.value,
                trace_id=context.trace_id,
            )
        return await self._register_resource.execute(registration, context)

    async def register_skill(
        self,
        registration: SkillRegistration,
        context: RequestContext,
    ) -> SkillRecord:
        if self._register_skill is None:
            raise DependencyUnavailableError(
                "skill store is not configured",
                component=RuntimeComponent.P3.value,
                trace_id=context.trace_id,
            )
        return await self._register_skill.execute(registration, context)

    async def delete_resource(
        self,
        resource_id: str,
        context: RequestContext,
        *,
        category: str = "documents",
    ) -> ContextDeleteResult:
        if self._delete_resource is None:
            raise DependencyUnavailableError(
                "resource store is not configured",
                component=RuntimeComponent.P3.value,
                trace_id=context.trace_id,
            )
        return await self._delete_resource.execute(
            resource_id,
            context,
            category=category,
        )

    async def delete_skill(
        self,
        skill_id: str,
        context: RequestContext,
    ) -> ContextDeleteResult:
        if self._delete_skill is None:
            raise DependencyUnavailableError(
                "skill store is not configured",
                component=RuntimeComponent.P3.value,
                trace_id=context.trace_id,
            )
        return await self._delete_skill.execute(skill_id, context)

    async def plan_projection_work(
        self,
        context: RequestContext,
        *,
        session_only: bool = False,
    ) -> list[ProjectionWorkItem]:
        service = self._require_projection_work()
        scope = (
            require_session_scope(context, operation="projection planning")
            if session_only
            else require_agent_scope(context, operation="projection planning")
        )
        return await service.plan(scope, session_only=session_only)

    async def reconcile_projection_work(
        self,
        context: RequestContext,
        *,
        session_only: bool = False,
    ) -> list[ProjectionWorkItem]:
        service = self._require_projection_work()
        scope = (
            require_session_scope(context, operation="projection reconciliation")
            if session_only
            else require_agent_scope(context, operation="projection reconciliation")
        )
        return await service.reconcile(scope, session_only=session_only)

    async def drain_projection_work(self, *, limit: int = 100) -> ProjectionWorkerReport:
        service = self._require_projection_work()
        return await service.drain(limit=limit)

    async def drain_context_projection_work(
        self, *, limit: int = 100
    ) -> ContextProjectionWorkerReport:
        if self._context_projection_work is None:
            raise DependencyUnavailableError(
                "context projection worker is not configured",
                component=RuntimeComponent.P3.value,
            )
        return await self._context_projection_work.drain(limit=limit)

    async def append_session_message(
        self,
        message: SessionMessage,
        context: RequestContext,
    ) -> SessionRecord:
        service = self._require_session_service(context)
        scope = require_session_scope(context, operation="session append")
        return await service.append(scope, message)

    async def get_session(self, context: RequestContext) -> SessionRecord | None:
        service = self._require_session_service(context)
        scope = require_session_scope(context, operation="session read")
        return await service.get(scope)

    async def commit_session(
        self,
        context: RequestContext,
        *,
        keep_recent_count: int = 5,
    ) -> SessionCommitResult:
        service = self._require_session_service(context)
        scope = require_session_scope(context, operation="session commit")
        return await service.commit(
            scope,
            keep_recent_count=keep_recent_count,
        )

    async def consolidate_session(
        self,
        context: RequestContext,
        *,
        archive_id: str,
    ) -> SessionConsolidationResult:
        if self._consolidate_session is None:
            raise DependencyUnavailableError(
                "session consolidation is not configured",
                component=RuntimeComponent.P3.value,
                trace_id=context.trace_id,
            )
        return await self._consolidate_session.execute(
            context,
            archive_id=archive_id,
        )

    async def drain_session_extraction(self, *, limit: int = 100) -> SessionExtractionWorkerReport:
        if self._session_extraction_worker is None:
            raise DependencyUnavailableError(
                "session extraction worker is not configured",
                component=RuntimeComponent.P3.value,
            )
        if self._session_service is not None:
            self._session_reconcile_cursor, _ = await self._session_service.reconcile_extractions(
                getattr(self, "_session_reconcile_cursor", 0),
                limit=limit,
            )
        return await self._session_extraction_worker.drain(limit=limit)

    def _require_session_service(self, context: RequestContext) -> SessionService:
        if self._session_service is None:
            raise DependencyUnavailableError(
                "session service is not configured",
                component=RuntimeComponent.P3.value,
                trace_id=context.trace_id,
            )
        return self._session_service

    def _require_projection_work(self) -> ProjectionWorkService:
        if self._projection_work is None:
            raise DependencyUnavailableError(
                "projection work service is not configured",
                component=RuntimeComponent.P3.value,
            )
        return self._projection_work

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
