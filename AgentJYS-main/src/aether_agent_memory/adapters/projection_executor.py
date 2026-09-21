"""Provider-neutral projection execution assembled from P3 ports."""

from __future__ import annotations

from typing import TYPE_CHECKING

from aether_agent_memory.b1 import EmbeddingRequest, ProcessingStatus
from aether_agent_memory.context_store.mapping import memory_to_context_item
from aether_agent_memory.context_store.models import ContextLayer, ContextLayerStatus
from aether_agent_memory.context_store.ports import SemanticIndexPort
from aether_agent_memory.core.memory import Memory
from aether_agent_memory.memory.projection import (
    ProjectionExecutorPort,
    ProjectionSupersededError,
    ProjectionWorkItem,
    ProjectionWorkKind,
)
from aether_agent_memory.runtime.errors import ScopeError
from aether_agent_memory.runtime.ports import EmbeddingPort, MemoryStorePort, VectorIndexPort
from aether_agent_memory.runtime.request_context import RequestContext

if TYPE_CHECKING:
    from collections.abc import Sequence


class ProjectionNotConfiguredError(RuntimeError):
    """A valid projection has no configured provider implementation."""


class ProviderProjectionExecutor(ProjectionExecutorPort):
    """Execute derived work while keeping provider details behind ports.

    Embedding and vector indexing are deliberately separate work kinds. This
    allows the current B1 pipeline to be replaced later without changing the
    Memory or queue contracts. Summary generation remains an explicit boundary
    until a real summarizer is configured.
    """

    def __init__(
        self,
        *,
        memory_store: MemoryStorePort,
        embedding: EmbeddingPort,
        vector_index: VectorIndexPort,
        context_semantic_index: SemanticIndexPort | None = None,
    ) -> None:
        self._memory_store = memory_store
        self._embedding = embedding
        self._vector_index = vector_index
        self._context_semantic_index = context_semantic_index

    async def execute(self, item: ProjectionWorkItem) -> None:
        memory = await self._memory_store.get(item.memory_id)
        if memory is None:
            raise ProjectionNotConfiguredError(
                f"memory not found for projection work: {item.memory_id}"
            )
        _assert_scope(memory, item)
        if memory.revision != item.revision:
            if memory.revision > item.revision:
                raise ProjectionSupersededError(
                    f"projection revision {item.revision} was superseded by "
                    f"{memory.revision}"
                )
            raise ProjectionNotConfiguredError(
                f"future projection work revision {item.revision}; "
                f"memory is at revision {memory.revision}"
            )
        if _is_satisfied(memory, item.kind):
            return
        context = _projection_context(item)

        if item.kind == ProjectionWorkKind.EMBEDDING:
            await self._execute_embedding(memory, context)
            return
        if item.kind == ProjectionWorkKind.VECTOR_INDEX:
            if memory.embedding is None:
                raise RuntimeError("vector projection requires a completed embedding")
            await self._vector_index.upsert_memory(memory, context)
            await self._index_context(memory)
            await self._persist_if_current(
                memory,
                {"vector_projection_status": "succeeded"},
            )
            return
        raise ProjectionNotConfiguredError(
            "summary projection executor is not configured; "
            "use the B2 compression pipeline when it is enabled"
        )

    async def _execute_embedding(
        self,
        memory: Memory,
        context: RequestContext,
    ) -> None:
        result = await self._embedding.embed(
            EmbeddingRequest(
                text=memory.content,
                source_type=memory.source,
                request_id=context.request_id,
                trace_id=context.trace_id,
                tenant_id=memory.tenant_id,
                source_id=memory.source_id or memory.id,
                object_id=memory.object_id,
                memory_id=memory.id,
                metadata={
                    **memory.metadata,
                    "projection_work_id": context.idempotency_key,
                    "agent_id": memory.agent_id,
                    "session_id": memory.session_id,
                },
            ),
            context,
        )
        if result.status != ProcessingStatus.SUCCESS or not result.records:
            detail = result.error_message or "embedding provider returned no records"
            raise RuntimeError(f"embedding projection failed: {detail}")
        vector = _mean_vector([record.vector for record in result.records])
        await self._persist_if_current(
            memory,
            {
                "embedding": vector,
                "embedding_status": "succeeded",
            },
        )

    async def _persist_if_current(
        self,
        memory: Memory,
        update: dict[str, object],
    ) -> None:
        # Keep legacy metadata readers consistent with the typed projection
        # state, including facts that arrived with explicit pending metadata.
        updated = memory.model_copy(
            update={
                **update,
                "metadata": {
                    **memory.metadata,
                    **{
                        key: value for key, value in update.items()
                        if key in {"embedding_status", "vector_projection_status"}
                    },
                },
            }
        )
        if await self._memory_store.upsert_if_revision(
            updated,
            expected_revision=memory.revision,
        ):
            return
        current = await self._memory_store.get(memory.id)
        if current is not None and current.revision > memory.revision:
            raise ProjectionSupersededError(
                f"projection revision {memory.revision} was superseded by "
                f"{current.revision}"
            )
        raise RuntimeError("projection compare-and-set failed")

    async def _index_context(self, memory: Memory) -> None:
        """Publish Memory's derived L0/L1 views after its vector is durable."""

        if self._context_semantic_index is None:
            return
        item = memory_to_context_item(memory)
        for layer in (ContextLayer.ABSTRACT, ContextLayer.OVERVIEW):
            content = item.content_for(layer)
            if content is None or content.status != ContextLayerStatus.AVAILABLE:
                continue
            await self._context_semantic_index.index(item, content)


def _projection_context(item: ProjectionWorkItem) -> RequestContext:
    return RequestContext.from_values(
        request_id=f"projection:{item.work_id}",
        trace_id=f"projection:{item.work_id}",
        tenant_id=item.scope.tenant_id,
        user_id=item.scope.user_id,
        agent_id=item.scope.agent_id,
        session_id=item.scope.session_id,
        task_id=item.scope.task_id,
        idempotency_key=f"projection:{item.work_id}",
    )


def _assert_scope(memory: Memory, item: ProjectionWorkItem) -> None:
    for field in ("tenant_id", "user_id", "agent_id", "session_id", "task_id"):
        expected = getattr(item.scope, field)
        actual = getattr(memory, field)
        if expected is not None and actual != expected:
            raise ScopeError(f"projection work does not belong to this {field}")


def _is_satisfied(memory: Memory, kind: ProjectionWorkKind) -> bool:
    if kind == ProjectionWorkKind.EMBEDDING:
        return memory.embedding_status == "succeeded" and memory.embedding is not None
    if kind == ProjectionWorkKind.VECTOR_INDEX:
        return memory.vector_projection_status == "succeeded"
    return memory.compression_status == "succeeded"


def _mean_vector(vectors: Sequence[list[float]]) -> list[float]:
    if not vectors or not vectors[0]:
        raise RuntimeError("embedding provider returned empty vectors")
    dimension = len(vectors[0])
    if any(len(vector) != dimension for vector in vectors):
        raise RuntimeError("embedding provider returned inconsistent dimensions")
    count = float(len(vectors))
    return [sum(vector[index] for vector in vectors) / count for index in range(dimension)]
