"""Compatibility exports from aether_agent_memory.recall.embedding.models."""

from aether_agent_memory.recall.embedding.models import (
    EmbeddingModelBinding as EmbeddingModelBinding,
)
from aether_agent_memory.recall.embedding.models import (
    SemanticEmbeddingExecution as SemanticEmbeddingExecution,
)
from aether_agent_memory.recall.embedding.models import (
    SemanticEmbeddingRequest as SemanticEmbeddingRequest,
)
from aether_agent_memory.recall.embedding.models import (
    SemanticEmbeddingResult as SemanticEmbeddingResult,
)

__all__ = [
    "SemanticEmbeddingRequest",
    "EmbeddingModelBinding",
    "SemanticEmbeddingResult",
    "SemanticEmbeddingExecution",
]
