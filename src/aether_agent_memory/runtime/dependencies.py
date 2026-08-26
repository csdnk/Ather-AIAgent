from __future__ import annotations

import os
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, Any

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
    VectorSearchPort,
)

if TYPE_CHECKING:
    from aether_agent_memory.memory.retrieval import MemoryRetrievalService


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
    retrieval: MemoryRetrievalService | None = None
    scheduler: SchedulerPort | None = None
    access_trace: AccessTracePort | None = None
    idempotency_store: IdempotencyPort | None = None
    action_log_store: ActionLogPort | None = None
    health_checks: list[HealthCheckPort] = field(default_factory=list)
    # Deprecated compatibility bridge for smoke/demo paths only. New P3
    # application logic must depend on explicit ports above instead.
    legacy_runtime: Any | None = None
