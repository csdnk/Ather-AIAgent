"""Host composition and legacy provider compatibility; business operations live in B/A."""

from typing import Protocol

from aether_agent_memory.recall.basic.vector_search import MilvusVectorSearch
from aether_agent_memory.recall.contracts.ports import VectorSearchPort
from aether_agent_memory.remember.basic.projection import MilvusProjection
from aether_agent_memory.remember.contracts.ports import ProjectionPort


class VectorBackend(ProjectionPort, VectorSearchPort, Protocol):
    """Deployment-level backend; never injected wholesale into a business service."""




class MilvusVectors(MilvusProjection, MilvusVectorSearch):
    """Shared Milvus connection; Host narrows capabilities for each consumer."""
