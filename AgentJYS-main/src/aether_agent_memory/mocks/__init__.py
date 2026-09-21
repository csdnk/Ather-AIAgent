"""Test doubles, loaded individually so flow tests do not load legacy services."""

from importlib import import_module
from typing import Any

_EXPORTS = {
    "BaseMockMemoryManager": ("aether_agent_memory.mocks._base", "BaseMockMemoryManager"),
    "MockEmbeddingClient": ("aether_agent_memory.mocks.embedding", "MockEmbeddingClient"),
    "MockStorageClient": ("aether_agent_memory.mocks.storage", "MockStorageClient"),
    "cosine_similarity": ("aether_agent_memory.mocks._base", "cosine_similarity"),
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
