"""Public runtime API with lazy compatibility exports."""

from __future__ import annotations

from typing import Any

from aether_agent_memory.runtime.dependencies import RuntimeDependencies, RuntimeProfile
from aether_agent_memory.runtime.dtos import (
    LongMemorySubmission,
    MemorySearchHit,
    MemorySearchResult,
    ObjectReference,
    TaskStatusRecord,
    VectorQueryResult,
)
from aether_agent_memory.runtime.errors import (
    BusyError,
    ConflictError,
    DegradedError,
    DependencyUnavailableError,
    InternalRuntimeError,
    ProjectionPendingError,
    RuntimeErrorBase,
    ScopeError,
    TimeoutError,
    ValidationError,
)
from aether_agent_memory.runtime.health import RuntimeHealth
from aether_agent_memory.runtime.request_context import RequestContext, Scope
from aether_agent_memory.runtime.status import (
    ComponentHealth,
    ComponentStatus,
    RuntimeComponent,
    RuntimeStatus,
)

__all__ = [
    "BusyError",
    "ComponentHealth",
    "ComponentStatus",
    "ConflictError",
    "DegradedError",
    "DependencyUnavailableError",
    "InternalRuntimeError",
    "LongMemorySubmission",
    "MemoryRuntime",
    "MemorySearchHit",
    "MemorySearchResult",
    "ObjectReference",
    "P3Runtime",
    "P3RuntimeConfig",
    "ProjectionPendingError",
    "RequestContext",
    "RuntimeComponent",
    "RuntimeDependencies",
    "RuntimeErrorBase",
    "RuntimeHealth",
    "RuntimeProfile",
    "RuntimeStatus",
    "Scope",
    "ScopeError",
    "TaskStatusRecord",
    "TimeoutError",
    "ValidationError",
    "VectorQueryResult",
]


def __getattr__(name: str) -> Any:
    if name == "MemoryRuntime":
        from aether_agent_memory.runtime.service import MemoryRuntime

        return MemoryRuntime
    if name in {"P3Runtime", "P3RuntimeConfig"}:
        from aether_agent_memory.runtime.legacy import P3Runtime, P3RuntimeConfig

        return {"P3Runtime": P3Runtime, "P3RuntimeConfig": P3RuntimeConfig}[name]
    raise AttributeError(name)
