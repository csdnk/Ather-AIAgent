"""Lazy compatibility exports; importing flow modules does not start legacy modules."""

from __future__ import annotations

from importlib import import_module
from typing import Any

_EXPORTS: dict[str, tuple[str, str]] = {
    "BusyError": ("aether_agent_memory.runtime.errors", "BusyError"),
    "ComponentHealth": ("aether_agent_memory.runtime.status", "ComponentHealth"),
    "ComponentStatus": ("aether_agent_memory.runtime.status", "ComponentStatus"),
    "ConflictError": ("aether_agent_memory.runtime.errors", "ConflictError"),
    "DegradedError": ("aether_agent_memory.runtime.errors", "DegradedError"),
    "DependencyUnavailableError": (
        "aether_agent_memory.runtime.errors",
        "DependencyUnavailableError",
    ),
    "InternalRuntimeError": ("aether_agent_memory.runtime.errors", "InternalRuntimeError"),
    "LongMemorySubmission": ("aether_agent_memory.runtime.dtos", "LongMemorySubmission"),
    "MemoryRuntime": ("aether_agent_memory.runtime.service", "MemoryRuntime"),
    "MemorySearchHit": ("aether_agent_memory.runtime.dtos", "MemorySearchHit"),
    "MemorySearchResult": ("aether_agent_memory.runtime.dtos", "MemorySearchResult"),
    "ObjectReference": ("aether_agent_memory.runtime.dtos", "ObjectReference"),
    "P3Runtime": ("aether_agent_memory.runtime.legacy", "P3Runtime"),
    "P3RuntimeConfig": ("aether_agent_memory.runtime.legacy", "P3RuntimeConfig"),
    "ProjectionPendingError": ("aether_agent_memory.runtime.errors", "ProjectionPendingError"),
    "RequestContext": ("aether_agent_memory.runtime.request_context", "RequestContext"),
    "RuntimeComponent": ("aether_agent_memory.runtime.status", "RuntimeComponent"),
    "RuntimeDependencies": ("aether_agent_memory.runtime.dependencies", "RuntimeDependencies"),
    "RuntimeErrorBase": ("aether_agent_memory.runtime.errors", "RuntimeErrorBase"),
    "RuntimeHealth": ("aether_agent_memory.runtime.health", "RuntimeHealth"),
    "RuntimeProfile": ("aether_agent_memory.runtime.dependencies", "RuntimeProfile"),
    "RuntimeStatus": ("aether_agent_memory.runtime.status", "RuntimeStatus"),
    "Scope": ("aether_agent_memory.runtime.request_context", "Scope"),
    "ScopeError": ("aether_agent_memory.runtime.errors", "ScopeError"),
    "TaskStatusRecord": ("aether_agent_memory.runtime.dtos", "TaskStatusRecord"),
    "TimeoutError": ("aether_agent_memory.runtime.errors", "TimeoutError"),
    "ValidationError": ("aether_agent_memory.runtime.errors", "ValidationError"),
    "VectorQueryResult": ("aether_agent_memory.runtime.dtos", "VectorQueryResult"),
}

__all__ = list(_EXPORTS)


def __getattr__(name: str) -> Any:
    target = _EXPORTS.get(name)
    if target is None:
        raise AttributeError(name)
    module, attribute = target
    value = getattr(import_module(module), attribute)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
