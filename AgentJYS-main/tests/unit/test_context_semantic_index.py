from __future__ import annotations

import pytest

from aether_agent_memory.adapters.context_semantic_index import (
    P2ContextSemanticIndexAdapter,
)
from aether_agent_memory.b1 import (
    EmbeddingRecord,
    EmbeddingRequest,
    EmbeddingResult,
    ProcessingStatus,
)
from aether_agent_memory.context_store import (
    ContextLayer,
    ContextSearchQuery,
    agent_scope_uri,
    memory_to_context_item,
)
from aether_agent_memory.context_store.in_memory import InMemoryContextIndexTombstones
from aether_agent_memory.core.enums import MemoryType, SourceType
from aether_agent_memory.core.memory import Memory
from aether_agent_memory.core.scope import Scope
from aether_agent_memory.runtime.dtos import MemorySearchHit, MemorySearchResult
from aether_agent_memory.runtime.request_context import RequestContext


class _Embedding:
    async def embed(
        self,
        request: EmbeddingRequest,
        context: RequestContext,
    ) -> EmbeddingResult:
        return EmbeddingResult(
            request_id=context.request_id,
            trace_id=context.trace_id,
            source_id=request.source_id or "source",
            status=ProcessingStatus.SUCCESS,
            records=[
                EmbeddingRecord(
                    request_id=context.request_id,
                    trace_id=context.trace_id,
                    source_id=request.source_id or "source",
                    chunk_id=request.memory_id or "chunk",
                    chunk_text=request.text,
                    vector=[0.1, 0.2, 0.3],
                    embedding_model="test-model",
                )
            ],
            latency_ms=1.0,
        )


class _VectorIndex:
    def __init__(self) -> None:
        self.memory: Memory | None = None

    async def upsert_memory(self, memory: Memory, context: RequestContext) -> None:
        self.memory = memory


class _VectorSearch:
    def __init__(self, vector_index: _VectorIndex) -> None:
        self._vector_index = vector_index

    async def search_memory(
        self,
        *,
        query: str,
        tenant_id: str,
        user_id: str,
        agent_id: str,
        limit: int,
        context: RequestContext,
        task_id: str | None = None,
    ) -> MemorySearchResult:
        assert self._vector_index.memory is not None
        return MemorySearchResult(
            items=[
                MemorySearchHit(
                    memory_id=self._vector_index.memory.id,
                    chunk_id=self._vector_index.memory.id,
                    text=self._vector_index.memory.content,
                    score=0.88,
                    tenant_id=tenant_id,
                    user_id=user_id,
                    agent_id=agent_id,
                )
            ],
            backend="test-vector",
            provider="test",
            namespace="test",
            query_dimension=3,
        )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_context_semantic_index_projects_and_recovers_context_identity() -> None:
    scope = Scope(
        tenant_id="tenant-1",
        user_id="user-1",
        agent_id="agent-1",
        session_id="session-1",
    )
    memory = Memory(
        id="memory-1",
        type=MemoryType.SEMANTIC,
        tenant_id=scope.tenant_id,
        user_id=scope.user_id,
        agent_id=scope.agent_id,
        session_id=scope.session_id,
        source=SourceType.USER,
        content="provider-neutral context identity",
    )
    item = memory_to_context_item(memory)
    vector_index = _VectorIndex()
    tombstones = InMemoryContextIndexTombstones()
    adapter = P2ContextSemanticIndexAdapter(
        embedding=_Embedding(),  # type: ignore[arg-type]
        vector_index=vector_index,  # type: ignore[arg-type]
        vector_search=_VectorSearch(vector_index),  # type: ignore[arg-type]
        tombstones=tombstones,
    )

    await adapter.index(item, item.layers[ContextLayer.OVERVIEW])
    assert vector_index.memory is not None
    assert vector_index.memory.id.startswith("aether-context-v1:")
    assert vector_index.memory.metadata["context_uri"] == str(item.uri)

    result = await adapter.search(
        ContextSearchQuery(
            query="context identity",
            scope=scope,
            root_uri=agent_scope_uri(scope),
            layers=[ContextLayer.OVERVIEW],
        )
    )

    assert result.backend == "p2-context-semantic-v1"
    assert len(result.hits) == 1
    assert result.hits[0].uri == item.uri
    assert result.hits[0].layer == ContextLayer.OVERVIEW
    assert result.hits[0].score == pytest.approx(0.88)

    await adapter.remove(item.uri)
    hidden = await adapter.search(
        ContextSearchQuery(query="context identity", scope=scope, limit=5)
    )
    assert hidden.hits == []

    await adapter.index(item, item.layers[ContextLayer.OVERVIEW])
    restored = await adapter.search(
        ContextSearchQuery(query="context identity", scope=scope, limit=5)
    )
    assert len(restored.hits) == 1


@pytest.mark.unit
@pytest.mark.asyncio
async def test_context_semantic_index_ignores_non_context_vector_ids() -> None:
    scope = Scope(tenant_id="tenant-1", user_id="user-1", agent_id="agent-1")
    vector_index = _VectorIndex()

    class _NonContextVectorSearch(_VectorSearch):
        async def search_memory(self, **kwargs) -> MemorySearchResult:  # type: ignore[no-untyped-def]
            return MemorySearchResult(
                items=[MemorySearchHit(chunk_id="ordinary-memory", score=0.9)],
                backend="test-vector",
            )

    adapter = P2ContextSemanticIndexAdapter(
        embedding=_Embedding(),  # type: ignore[arg-type]
        vector_index=vector_index,  # type: ignore[arg-type]
        vector_search=_NonContextVectorSearch(vector_index),  # type: ignore[arg-type]
    )

    result = await adapter.search(
        ContextSearchQuery(query="anything", scope=scope, limit=5)
    )

    assert result.hits == []
