from __future__ import annotations

from typing import Any

from aether_agent_memory.b2.compression import (
    CompressionArtifact,
    CompressionPolicy,
    HybridMemoryCompressor,
    attach_compression_metadata,
)
from aether_agent_memory.b2.compression_store import (
    CompressionArtifactStore,
    InMemoryCompressionArtifactStore,
    RedisCompressionArtifactStore,
    SQLiteCompressionArtifactStore,
)
from aether_agent_memory.b2.events import MemoryEvent, MemoryEventType
from aether_agent_memory.b2.long_text import chunk_text
from aether_agent_memory.b2.memory_consolidation import (
    ConsolidationResult,
    FactExtractionPolicy,
    MemoryConsolidator,
    StructuredFactExtractor,
)
from aether_agent_memory.b2.retrieval import (
    CrossEncoderSemanticReranker,
    HybridMemoryRetriever,
    HybridRetrievalPolicy,
    LexicalSemanticReranker,
)
from aether_agent_memory.b2.task_status import TaskState
from aether_agent_memory.b2.text_classifier import TextClassification, classify_text

__all__ = [
    "MemoryEvent",
    "MemoryEventType",
    "MemoryService",
    "TaskState",
    "TextClassification",
    "CompressionArtifact",
    "CompressionArtifactStore",
    "CompressionPolicy",
    "HybridMemoryCompressor",
    "attach_compression_metadata",
    "ConsolidationResult",
    "FactExtractionPolicy",
    "MemoryConsolidator",
    "StructuredFactExtractor",
    "HybridMemoryRetriever",
    "HybridRetrievalPolicy",
    "LexicalSemanticReranker",
    "CrossEncoderSemanticReranker",
    "InMemoryCompressionArtifactStore",
    "RedisCompressionArtifactStore",
    "SQLiteCompressionArtifactStore",
    "chunk_text",
    "classify_text",
]


def __getattr__(name: str) -> Any:
    """Load the legacy service only when its compatibility export is used."""

    if name == "MemoryService":
        from aether_agent_memory.b2.service import MemoryService

        return MemoryService
    raise AttributeError(name)
