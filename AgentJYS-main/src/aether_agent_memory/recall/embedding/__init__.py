"""Shared Query/Passage embedding owned by the Recall flow.

Remember may consume this capability with approved Passage input. Model backends
implement EmbeddingBackendPort; the capability does not depend on legacy B1.
"""

from aether_agent_memory.recall.embedding.service import (
    EmbeddingBackendPort,
    EmbeddingInputPort,
    EmbeddingPolicy,
    EmbeddingRuntime,
    SemanticEmbeddingCapability,
    SemanticEmbeddingService,
)

__all__ = [
    "EmbeddingBackendPort",
    "EmbeddingInputPort",
    "EmbeddingPolicy",
    "EmbeddingRuntime",
    "SemanticEmbeddingCapability",
    "SemanticEmbeddingService",
]
