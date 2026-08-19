from __future__ import annotations

import os
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from aether_agent_memory.runtime.ports import (
    AccessTracePort,
    ContextPort,
    EmbeddingPort,
    HealthCheckPort,
    MemoryEventPort,
    ObjectStorePort,
    SchedulerPort,
    TaskQueuePort,
    TaskStatusPort,
    VectorSearchPort,
)


class RuntimeProfile(StrEnum):
    DEMO = "demo"
    LOCAL = "local"
    PRODUCTION = "production"

    @classmethod
    def from_environment(cls) -> RuntimeProfile:
        raw = os.getenv("AETHER_RUNTIME_PROFILE", "").strip().lower()
        if raw in {"demo", "mock"}:
            return cls.DEMO
        if raw in {"prod", "production"}:
            return cls.PRODUCTION
        if raw in {"local", "dev", "development"}:
            return cls.LOCAL
        if os.getenv("AETHER_ENABLE_DEMO", "false").lower() in {"1", "true", "yes"}:
            return cls.DEMO
        return cls.LOCAL


@dataclass(slots=True)
class RuntimeDependencies:
    embedding: EmbeddingPort
    memory_events: MemoryEventPort
    context_builder: ContextPort
    task_queue: TaskQueuePort | None = None
    task_status: TaskStatusPort | None = None
    object_store: ObjectStorePort | None = None
    vector_search: VectorSearchPort | None = None
    scheduler: SchedulerPort | None = None
    access_trace: AccessTracePort | None = None
    health_checks: list[HealthCheckPort] = field(default_factory=list)
    legacy_runtime: Any | None = None
