from __future__ import annotations

import os
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from aether_agent_memory.context_store.ports import (
    ContextCatalogReaderPort,
    ContextContentReaderPort,
    ContextIndexTombstonePort,
    ContextProjectionExecutorPort,
    ContextProjectionQueuePort,
    ContextReindexPort,
    ContextSearchPort,
    RetrievalTraceStorePort,
    SemanticIndexPort,
)
from aether_agent_memory.memory.formation import MemoryExtractionPort
from aether_agent_memory.resource.ports import ResourceStorePort
from aether_agent_memory.runtime.ports import (
    AccessTracePort,
    ActionLogPort,
    ContextPort,
    EmbeddingPort,
    HealthCheckPort,
    IdempotencyPort,
    MemoryEventPort,
    ObjectStorePort,
    SchedulerPort,
    TaskQueuePort,
    TaskStatusPort,
    VectorIndexPort,
    VectorSearchPort,
)
from aether_agent_memory.session.ports import (
    SessionExtractionQueuePort,
    SessionStorePort,
)
from aether_agent_memory.skill.ports import SkillStorePort

if TYPE_CHECKING:
    from aether_agent_memory.memory.projection import (
        MemoryProjectionReconciler,
        ProjectionExecutorPort,
        ProjectionQueuePort,
    )
    from aether_agent_memory.memory.retrieval import ContextRetrievalService
    from aether_agent_memory.runtime.ports import MemoryStorePort


class RuntimeProfile(StrEnum):
    DEMO = "demo"
    INTEGRATION = "integration"
    PRODUCTION = "production"
    # Backward-compatible alias: a "local" development run is an integration run.
    LOCAL = "integration"

    @classmethod
    def from_environment(cls) -> RuntimeProfile:
        raw = os.getenv("AETHER_RUNTIME_PROFILE", "").strip().lower()
        if raw in {"demo", "mock"}:
            return cls.DEMO
        if raw in {"prod", "production"}:
            return cls.PRODUCTION
        if raw in {"local", "dev", "development", "integration", "integ"}:
            return cls.INTEGRATION
        if os.getenv("AETHER_ENABLE_DEMO", "false").lower() in {"1", "true", "yes"}:
            return cls.DEMO
        return cls.INTEGRATION


@dataclass(slots=True)
class RuntimeDependencies:
    embedding: EmbeddingPort
    memory_events: MemoryEventPort
    context_builder: ContextPort
    task_queue: TaskQueuePort | None = None
    task_status: TaskStatusPort | None = None
    object_store: ObjectStorePort | None = None
    vector_search: VectorSearchPort | None = None
    vector_index: VectorIndexPort | None = None
    memory_store: MemoryStorePort | None = None
    resource_store: ResourceStorePort | None = None
    skill_store: SkillStorePort | None = None
    retrieval: ContextRetrievalService | None = None
    retrieval_trace_store: RetrievalTraceStorePort | None = None
    context_catalog: ContextCatalogReaderPort | None = None
    context_content: ContextContentReaderPort | None = None
    context_search: ContextSearchPort | None = None
    context_semantic_index: SemanticIndexPort | None = None
    context_reindex: ContextReindexPort | None = None
    context_index_tombstones: ContextIndexTombstonePort | None = None
    context_projection_queue: ContextProjectionQueuePort | None = None
    context_projection_executor: ContextProjectionExecutorPort | None = None
    projection_queue: ProjectionQueuePort | None = None
    projection_reconciler: MemoryProjectionReconciler | None = None
    projection_executor: ProjectionExecutorPort | None = None
    session_store: SessionStorePort | None = None
    session_extraction_queue: SessionExtractionQueuePort | None = None
    memory_extraction: MemoryExtractionPort | None = None
    scheduler: SchedulerPort | None = None
    access_trace: AccessTracePort | None = None
    idempotency_store: IdempotencyPort | None = None
    action_log_store: ActionLogPort | None = None
    health_checks: list[HealthCheckPort] = field(default_factory=list)
    # Deprecated compatibility bridge for smoke/demo paths only. New P3
    # application logic must depend on explicit ports above instead.
    legacy_runtime: Any | None = None
