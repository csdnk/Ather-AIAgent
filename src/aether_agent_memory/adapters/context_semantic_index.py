"""Context semantic-index adapter built from existing P3 ports.

The adapter keeps Context identity in ``aether://`` URIs while using the
existing B1 embedding and P2 vector ports as a derived representation.  It
does not expose either provider's response models to the Context layer.
"""

from __future__ import annotations

from base64 import urlsafe_b64decode, urlsafe_b64encode

from aether_agent_memory.b1 import EmbeddingRequest, ProcessingStatus
from aether_agent_memory.context_store.models import (
    ContextContent,
    ContextItem,
    ContextItemKind,
    ContextLayer,
    ContextSearchHit,
    ContextSearchQuery,
    ContextSearchResult,
)
from aether_agent_memory.context_store.ports import (
    ContextIndexTombstonePort,
    SemanticIndexPort,
)
from aether_agent_memory.context_store.uri import AetherUri
from aether_agent_memory.core.enums import MemoryType, SourceType
from aether_agent_memory.core.memory import Memory
from aether_agent_memory.runtime.ports import EmbeddingPort, VectorIndexPort, VectorSearchPort
from aether_agent_memory.runtime.request_context import RequestContext

_VECTOR_ID_PREFIX = "aether-context-v1:"
_SEPARATOR = "\x1f"


class P2ContextSemanticIndexAdapter(SemanticIndexPort):
    """Use B1 + the existing P2 vector ports for Context-derived vectors.

    The P2 contract currently has no vector-delete operation. ``remove``
    therefore writes a P3 logical tombstone; the Context Catalog remains
    authoritative and the hierarchical search service rejects stale or missing
    catalog items. Stable vector IDs make repeated reindex operations idempotent.
    """

    def __init__(
        self,
        *,
        embedding: EmbeddingPort,
        vector_index: VectorIndexPort,
        vector_search: VectorSearchPort,
        tombstones: ContextIndexTombstonePort | None = None,
    ) -> None:
        self._embedding = embedding
        self._vector_index = vector_index
        self._vector_search = vector_search
        self._tombstones = tombstones

    async def index(self, item: ContextItem, content: ContextContent) -> None:
        text = (content.text or "").strip()
        if not text:
            raise ValueError("context semantic indexing requires textual content")
        vector_id = _context_vector_id(item, content.layer)
        context = RequestContext.from_values(
            tenant_id=item.scope.tenant_id,
            user_id=item.scope.user_id,
            agent_id=item.scope.agent_id,
            session_id=item.scope.session_id,
        )
        result = await self._embedding.embed(
            EmbeddingRequest(
                text=text,
                source_type=SourceType.DOCUMENT,
                request_id=context.request_id,
                trace_id=context.trace_id,
                tenant_id=item.scope.tenant_id,
                source_id=str(item.uri),
                object_id=content.content_ref,
                memory_id=vector_id,
                metadata={
                    "context_uri": str(item.uri),
                "context_kind": item.kind.value,
                "context_layer": content.layer.value,
                "source_revision": item.source_revision,
            },
            ),
            context,
        )
        if result.status != ProcessingStatus.SUCCESS or not result.records:
            detail = result.error_message or "embedding provider returned no records"
            raise RuntimeError(f"context semantic indexing failed: {detail}")
        vector = _mean_vector([record.vector for record in result.records])
        proxy = Memory(
            id=vector_id,
            type=MemoryType.SEMANTIC,
            session_id=item.scope.session_id or "",
            agent_id=item.scope.agent_id or "",
            user_id=item.scope.user_id,
            tenant_id=item.scope.tenant_id,
            source_id=str(item.uri),
            object_id=content.content_ref,
            content=text,
            source=SourceType.RAG,
            embedding=vector,
            embedding_status="succeeded",
            vector_projection_status="pending",
            metadata={
                "context_uri": str(item.uri),
                "context_kind": item.kind.value,
                "context_layer": content.layer.value,
                "embedding_model": result.records[0].embedding_model,
            },
        )
        await self._vector_index.upsert_memory(proxy, context)
        if self._tombstones is not None:
            await self._tombstones.restore(item.uri)

    async def remove(self, uri: AetherUri) -> None:
        if self._tombstones is not None:
            await self._tombstones.invalidate(uri)

    async def search(self, query: ContextSearchQuery) -> ContextSearchResult:
        scope = query.scope
        context = RequestContext.from_values(
            tenant_id=scope.tenant_id,
            user_id=scope.user_id,
            agent_id=scope.agent_id,
            session_id=scope.session_id,
        )
        result = await self._vector_search.search_memory(
            query=query.query,
            tenant_id=scope.tenant_id or "",
            user_id=scope.user_id or "",
            agent_id=scope.agent_id or "",
            limit=max(query.limit * 10, 100),
            context=context,
        )
        hits: list[ContextSearchHit] = []
        for item in result.items:
            decoded = _decode_context_vector_id(item.chunk_id)
            if decoded is None:
                continue
            uri, kind, layer = decoded
            if query.root_uri is not None and not uri.is_within(query.root_uri):
                continue
            if query.kinds and kind not in query.kinds:
                continue
            if query.layers and layer not in query.layers:
                continue
            if self._tombstones is not None and await self._tombstones.is_invalidated(uri):
                continue
            hits.append(
                ContextSearchHit(
                    uri=uri,
                    kind=kind,
                    layer=layer,
                    score=max(0.0, min(float(item.score), 1.0)),
                    text=item.text,
                    content_ref=item.content_ref,
                    source="p2-context-semantic",
                    source_revision=item.source_revision,
                )
            )
            if len(hits) >= query.limit:
                break
        return ContextSearchResult(
            hits=hits,
            backend="p2-context-semantic-v1",
            metadata={
                "provider": result.provider or "p2",
                "namespace": result.namespace,
                "query_dimension": result.query_dimension,
            },
        )


def _context_vector_id(item: ContextItem, layer: ContextLayer) -> str:
    raw = _SEPARATOR.join((item.kind.value, layer.value, str(item.uri)))
    encoded = urlsafe_b64encode(raw.encode("utf-8")).decode("ascii").rstrip("=")
    return f"{_VECTOR_ID_PREFIX}{encoded}"


def _decode_context_vector_id(
    value: str | None,
) -> tuple[AetherUri, ContextItemKind, ContextLayer] | None:
    if value is None or not value.startswith(_VECTOR_ID_PREFIX):
        return None
    encoded = value.removeprefix(_VECTOR_ID_PREFIX)
    try:
        raw = urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)).decode("utf-8")
        kind_value, layer_value, uri_value = raw.split(_SEPARATOR, 2)
        return (
            AetherUri(uri_value),
            ContextItemKind(kind_value),
            ContextLayer(layer_value),
        )
    except (ValueError, UnicodeDecodeError):
        return None


def _mean_vector(vectors: list[list[float]]) -> list[float]:
    if not vectors:
        raise ValueError("cannot average an empty vector list")
    dimension = len(vectors[0])
    if dimension == 0 or any(len(vector) != dimension for vector in vectors):
        raise ValueError("embedding records have inconsistent dimensions")
    return [sum(vector[index] for vector in vectors) / len(vectors) for index in range(dimension)]


__all__ = ["P2ContextSemanticIndexAdapter"]
