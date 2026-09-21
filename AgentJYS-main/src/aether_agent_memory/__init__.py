"""Lazy compatibility exports; importing flow modules does not start legacy modules."""

from __future__ import annotations

from importlib import import_module
from typing import Any

_EXPORTS: dict[str, tuple[str, str]] = {
    "AccessStats": ("aether_agent_memory.b3", "AccessStats"),
    "ActionExecutor": ("aether_agent_memory.b3", "ActionExecutor"),
    "ActionLog": ("aether_agent_memory.b3", "ActionLog"),
    "ActionLogEntry": ("aether_agent_memory.b3", "ActionLogEntry"),
    "ActionType": ("aether_agent_memory.b3", "ActionType"),
    "B2MemoryError": ("aether_agent_memory.core.exceptions", "B2MemoryError"),
    "ContextBudgetExceededError": (
        "aether_agent_memory.core.exceptions",
        "ContextBudgetExceededError",
    ),
    "ContextPack": ("aether_agent_memory.context.models", "ContextPack"),
    "ContextRequest": ("aether_agent_memory.context.models", "ContextRequest"),
    "EmbeddingError": ("aether_agent_memory.core.exceptions", "EmbeddingError"),
    "EmbeddingPipeline": ("aether_agent_memory.b1", "EmbeddingPipeline"),
    "EmbeddingRecord": ("aether_agent_memory.b1", "EmbeddingRecord"),
    "EmbeddingRequest": ("aether_agent_memory.b1", "EmbeddingRequest"),
    "EmbeddingResult": ("aether_agent_memory.b1", "EmbeddingResult"),
    "ExecuteStatus": ("aether_agent_memory.b3", "ExecuteStatus"),
    "ExecutionFeedback": ("aether_agent_memory.b3", "ExecutionFeedback"),
    "FastEmbedClient": ("aether_agent_memory.b1", "FastEmbedClient"),
    "HeuristicPolicy": ("aether_agent_memory.b3", "HeuristicPolicy"),
    "HeuristicPolicyConfig": ("aether_agent_memory.b3", "HeuristicPolicyConfig"),
    "HeuristicScheduler": ("aether_agent_memory.b3", "HeuristicScheduler"),
    "InMemoryMemoryStore": ("aether_agent_memory.persistence", "InMemoryMemoryStore"),
    "InMemoryVectorSink": ("aether_agent_memory.b1", "InMemoryVectorSink"),
    "InvalidStateTransitionError": (
        "aether_agent_memory.core.exceptions",
        "InvalidStateTransitionError",
    ),
    "Memory": ("aether_agent_memory.core.memory", "Memory"),
    "MemoryEvent": ("aether_agent_memory.b2", "MemoryEvent"),
    "MemoryEventType": ("aether_agent_memory.b2", "MemoryEventType"),
    "MemoryExpiredError": ("aether_agent_memory.core.exceptions", "MemoryExpiredError"),
    "MemoryFact": ("aether_agent_memory.core.memory", "MemoryFact"),
    "MemoryNotFoundError": ("aether_agent_memory.core.exceptions", "MemoryNotFoundError"),
    "MemoryService": ("aether_agent_memory.b2", "MemoryService"),
    "MemorySignal": ("aether_agent_memory.signal.models", "MemorySignal"),
    "MemoryState": ("aether_agent_memory.core.enums", "MemoryState"),
    "MemoryType": ("aether_agent_memory.core.enums", "MemoryType"),
    "MockContextPackBuilder": ("aether_agent_memory.context.builder", "MockContextPackBuilder"),
    "MockEmbeddingClient": ("aether_agent_memory.mocks.embedding", "MockEmbeddingClient"),
    "MockEpisodicMemoryManager": (
        "aether_agent_memory.episodic.manager",
        "MockEpisodicMemoryManager",
    ),
    "MockExecutor": ("aether_agent_memory.b3", "MockExecutor"),
    "MockSemanticMemoryManager": (
        "aether_agent_memory.semantic.manager",
        "MockSemanticMemoryManager",
    ),
    "MockSignalEmitter": ("aether_agent_memory.signal.emitter", "MockSignalEmitter"),
    "MockStorageClient": ("aether_agent_memory.mocks.storage", "MockStorageClient"),
    "MockWorkingMemoryManager": ("aether_agent_memory.working.manager", "MockWorkingMemoryManager"),
    "P2MigrationExecutor": ("aether_agent_memory.b3", "P2MigrationExecutor"),
    "P2Ref": ("aether_agent_memory.placement", "P2Ref"),
    "ProcessingStatus": ("aether_agent_memory.b1", "ProcessingStatus"),
    "RecalledMemory": ("aether_agent_memory.core.memory", "RecalledMemory"),
    "ResourceState": ("aether_agent_memory.b3", "ResourceState"),
    "SQLiteMemoryStore": ("aether_agent_memory.persistence", "SQLiteMemoryStore"),
    "SchedulableObject": ("aether_agent_memory.b3", "SchedulableObject"),
    "ScheduleAction": ("aether_agent_memory.b3", "ScheduleAction"),
    "ScheduleRequest": ("aether_agent_memory.b3", "ScheduleRequest"),
    "ScheduleRunResult": ("aether_agent_memory.b3", "ScheduleRunResult"),
    "SemanticSignals": ("aether_agent_memory.b3", "SemanticSignals"),
    "Settings": ("aether_agent_memory.config.settings", "Settings"),
    "SignalType": ("aether_agent_memory.core.enums", "SignalType"),
    "SourceType": ("aether_agent_memory.core.enums", "SourceType"),
    "StorageError": ("aether_agent_memory.core.exceptions", "StorageError"),
    "StorageTier": ("aether_agent_memory.core.enums", "StorageTier"),
    "TextChunk": ("aether_agent_memory.b1", "TextChunk"),
    "TextChunker": ("aether_agent_memory.b1", "TextChunker"),
    "TierState": ("aether_agent_memory.b3", "TierState"),
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
