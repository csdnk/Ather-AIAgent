"""Compatibility exports from aether_agent_memory.recall.embedding.service."""

from aether_agent_memory.recall.embedding.service import (
    ComputedEmbedding as ComputedEmbedding,
)
from aether_agent_memory.recall.embedding.service import (
    EmbeddingBackendPort as EmbeddingBackendPort,
)
from aether_agent_memory.recall.embedding.service import (
    EmbeddingInputPort as EmbeddingInputPort,
)
from aether_agent_memory.recall.embedding.service import (
    EmbeddingPolicy as EmbeddingPolicy,
)
from aether_agent_memory.recall.embedding.service import (
    EmbeddingRuntime as EmbeddingRuntime,
)
from aether_agent_memory.recall.embedding.service import (
    ResolvedEmbeddingInput as ResolvedEmbeddingInput,
)
from aether_agent_memory.recall.embedding.service import (
    SemanticEmbeddingCapability as SemanticEmbeddingCapability,
)
from aether_agent_memory.recall.embedding.service import (
    SemanticEmbeddingError as SemanticEmbeddingError,
)
from aether_agent_memory.recall.embedding.service import (
    SemanticEmbeddingService as SemanticEmbeddingService,
)
from aether_agent_memory.recall.embedding.service import (
    TransientEmbeddingError as TransientEmbeddingError,
)
from aether_agent_memory.recall.embedding.service import (
    make_embedding_request as make_embedding_request,
)
from aether_agent_memory.recall.embedding.service import (
    reuse_digest as reuse_digest,
)

__all__ = [
    "SemanticEmbeddingError",
    "TransientEmbeddingError",
    "ResolvedEmbeddingInput",
    "ComputedEmbedding",
    "EmbeddingInputPort",
    "EmbeddingBackendPort",
    "SemanticEmbeddingCapability",
    "EmbeddingRuntime",
    "EmbeddingPolicy",
    "reuse_digest",
    "make_embedding_request",
    "SemanticEmbeddingService",
]
