"""Lazy compatibility exports; importing flow modules does not start legacy modules."""

from __future__ import annotations

from importlib import import_module
from typing import Any

_EXPORTS: dict[str, tuple[str, str]] = {
    "P2GrpcClient": ("aether_agent_memory.p2.client", "P2GrpcClient"),
    "P2MigrationAck": ("aether_agent_memory.p2.client", "P2MigrationAck"),
    "P2SegmentInfo": ("aether_agent_memory.p2.client", "P2SegmentInfo"),
    "P2StorageClient": ("aether_agent_memory.p2.adapters", "P2StorageClient"),
    "P2UnavailableError": ("aether_agent_memory.p2.client", "P2UnavailableError"),
    "P2VectorHit": ("aether_agent_memory.p2.client", "P2VectorHit"),
    "P2VectorSink": ("aether_agent_memory.p2.adapters", "P2VectorSink"),
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
