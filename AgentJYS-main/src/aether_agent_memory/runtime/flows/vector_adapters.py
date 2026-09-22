"""Host composition and legacy provider compatibility; business operations live in B/A."""

from typing import Protocol

from aether_agent_memory.recall.basic.vector_search import MilvusVectorSearch, SQLiteVectorSearch
from aether_agent_memory.recall.contracts.ports import VectorSearchPort
from aether_agent_memory.remember.basic.projection import MilvusProjection, SQLiteProjection
from aether_agent_memory.remember.contracts.ports import ProjectionPort


class VectorBackend(ProjectionPort, VectorSearchPort, Protocol):
    """Deployment-level backend; never injected wholesale into a business service."""


class SQLiteVectors(SQLiteProjection, SQLiteVectorSearch):
    """Shared SQLite resource with separately implemented B writes and A searches."""


class MilvusVectors(MilvusProjection, MilvusVectorSearch):
    """Shared Milvus connection; Host narrows capabilities for each consumer."""
