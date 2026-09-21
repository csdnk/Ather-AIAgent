from __future__ import annotations

import heapq
from dataclasses import dataclass
from itertools import count
from time import perf_counter
from uuid import uuid4

from aether_agent_memory.context_store.access import item_is_visible_to_scope
from aether_agent_memory.context_store.models import (
    ContextContent,
    ContextItem,
    ContextItemKind,
    ContextLayer,
    ContextLayerStatus,
    ContextSearchHit,
    ContextSearchQuery,
    ContextSearchResult,
    RetrievalTrace,
    RetrievalTraceAction,
    RetrievalTraceStep,
)
from aether_agent_memory.context_store.ports import (
    ContextCatalogReaderPort,
    ContextContentReaderPort,
    RetrievalTraceStorePort,
    SemanticIndexPort,
)
from aether_agent_memory.context_store.uri import AetherUri


@dataclass(frozen=True, slots=True)
class HierarchicalSearchPolicy:
    max_depth: int = 12
    max_visited_nodes: int = 500
    max_children_per_directory: int = 100
    minimum_score: float = 0.0

    def __post_init__(self) -> None:
        if self.max_depth < 1:
            raise ValueError("max_depth must be positive")
        if self.max_visited_nodes < 1:
            raise ValueError("max_visited_nodes must be positive")
        if self.max_children_per_directory < 1:
            raise ValueError("max_children_per_directory must be positive")
        if not 0.0 <= self.minimum_score <= 1.0:
            raise ValueError("minimum_score must be between 0 and 1")


class HierarchicalContextSearchService:
    """Bounded directory traversal over provider-neutral Context ports.

    This service establishes the P3 hierarchy and convergence boundary. Its
    scorer is deliberately deterministic; a production semantic/rerank adapter
    can replace scoring without changing traversal or application contracts.
    """

    def __init__(
        self,
        catalog: ContextCatalogReaderPort,
        content: ContextContentReaderPort,
        *,
        policy: HierarchicalSearchPolicy | None = None,
        semantic_index: SemanticIndexPort | None = None,
        trace_store: RetrievalTraceStorePort | None = None,
    ) -> None:
        self._catalog = catalog
        self._content = content
        self._policy = policy or HierarchicalSearchPolicy()
        self._semantic_index = semantic_index
        self._trace_store = trace_store

    async def search(self, query: ContextSearchQuery) -> ContextSearchResult:
        if query.root_uri is None:
            raise ValueError("hierarchical context search requires root_uri")

        trace = RetrievalTrace(
            trace_id=query.trace_id or uuid4().hex,
            query=query.query,
            scope=query.scope,
            target_uri=query.root_uri,
        )
        semantic_started = perf_counter()

        sequence = count()
        pending: list[tuple[float, int, int, AetherUri]] = [
            (-1.0, 0, next(sequence), query.root_uri)
        ]
        visited: set[str] = set()
        hits: list[ContextSearchHit] = []
        expanded_directories = 0
        truncated_children = 0
        semantic_hits: list[ContextSearchHit] = []
        semantic_error: str | None = None

        if self._semantic_index is not None:
            try:
                semantic_result = await self._semantic_index.search(query)
                semantic_hits = await self._filter_semantic_hits(
                    semantic_result.hits,
                    query,
                )
                trace.record(
                    RetrievalTraceStep(
                        action=RetrievalTraceAction.SOURCE_RECALL,
                        source="semantic-index",
                        latency_ms=(perf_counter() - semantic_started) * 1000,
                        candidate_count=len(semantic_hits),
                    )
                )
            except Exception as exc:
                semantic_error = f"{type(exc).__name__}: {exc}"
                trace.record(
                    RetrievalTraceStep(
                        action=RetrievalTraceAction.DEGRADED,
                        source="semantic-index",
                        latency_ms=(perf_counter() - semantic_started) * 1000,
                        candidate_count=0,
                        reason=semantic_error,
                    )
                )

        catalog_started = perf_counter()
        while pending and len(visited) < self._policy.max_visited_nodes:
            negative_priority, depth, _, uri = heapq.heappop(pending)
            uri_key = str(uri)
            if uri_key in visited:
                continue
            visited.add(uri_key)
            if depth >= self._policy.max_depth:
                continue

            children = await self._catalog.list_children(uri)
            if len(children) > self._policy.max_children_per_directory:
                truncated_children += len(children) - self._policy.max_children_per_directory
                children = children[: self._policy.max_children_per_directory]
            if children:
                expanded_directories += 1

            parent_priority = -negative_priority
            for child in children:
                # Catalog adapters are replaceable; enforce visibility again
                # before traversing or scoring any returned child.
                if not item_is_visible_to_scope(child, query.scope):
                    continue
                score, selected = await self._score_item(child, query)
                if child.kind == ContextItemKind.DIRECTORY:
                    traversal_priority = max(score, parent_priority * 0.95)
                    heapq.heappush(
                        pending,
                        (-traversal_priority, depth + 1, next(sequence), child.uri),
                    )
                    continue
                if query.kinds and child.kind not in query.kinds:
                    continue
                if selected is None or score <= self._policy.minimum_score:
                    continue
                hits.append(
                    ContextSearchHit(
                        uri=child.uri,
                        kind=child.kind,
                        layer=selected.layer,
                        score=score,
                        text=selected.text,
                        content_ref=selected.content_ref,
                        source="catalog-hierarchical",
                        source_revision=child.source_revision,
                    )
                )

        hits = _merge_hits(hits, semantic_hits)
        hits.sort(key=lambda hit: (-hit.score, str(hit.uri), hit.layer.value))
        incomplete = bool(pending) or truncated_children > 0 or semantic_error is not None
        missing_sources: list[str] = []
        if bool(pending) or truncated_children > 0:
            missing_sources.append("catalog_truncated")
        if semantic_error is not None:
            missing_sources.append("semantic_index")
        trace.record(
            RetrievalTraceStep(
                action=RetrievalTraceAction.SOURCE_RECALL,
                source="catalog-hierarchical",
                latency_ms=(perf_counter() - catalog_started) * 1000,
                candidate_count=len(hits),
                reason=(
                    "bounded traversal"
                    if bool(pending) or truncated_children > 0
                    else None
                ),
            )
        )
        for hit in hits[: query.limit]:
            trace.record(
                RetrievalTraceStep(
                    action=RetrievalTraceAction.CONTEXT_SELECTED,
                    source=hit.source,
                    uri=hit.uri,
                    layer=hit.layer,
                    score=hit.score,
                )
            )
        if bool(pending) or truncated_children > 0:
            trace.record(
                RetrievalTraceStep(
                    action=RetrievalTraceAction.DEGRADED,
                    source="catalog-hierarchical",
                    candidate_count=len(hits),
                    reason="bounded traversal reached its limit",
                )
            )
        trace.finish(complete=not incomplete, missing_sources=missing_sources)
        await self._persist_trace(trace)
        backend = (
            "catalog-hierarchical+semantic-v1"
            if semantic_hits
            else "catalog-hierarchical-v1"
        )
        metadata = {
            "visited_nodes": len(visited),
            "expanded_directories": expanded_directories,
            "pending_directories": len(pending),
            "truncated_children": truncated_children,
            "scoring": "deterministic-lexical-v1",
            "semantic_index_configured": self._semantic_index is not None,
            "semantic_index_hits": len(semantic_hits),
            "trace_id": trace.trace_id,
            **({"semantic_index_error": semantic_error} if semantic_error else {}),
        }
        return ContextSearchResult(
            hits=hits[: query.limit],
            backend=backend,
            complete=not incomplete,
            missing_sources=missing_sources,
            metadata=metadata,
        )

    async def _persist_trace(self, trace: RetrievalTrace) -> None:
        if self._trace_store is None:
            return
        try:
            await self._trace_store.put(trace)
        except Exception:
            # Observability is best effort and must not fail context search.
            return

    async def _filter_semantic_hits(
        self,
        candidates: list[ContextSearchHit],
        query: ContextSearchQuery,
    ) -> list[ContextSearchHit]:
        """Validate derived hits against the authoritative catalog and scope."""

        allowed: list[ContextSearchHit] = []
        for hit in candidates:
            if not _semantic_hit_is_allowed(hit, query):
                continue
            item = await self._catalog.get(hit.uri)
            if item is None or item.kind != hit.kind:
                continue
            if (
                hit.source_revision is not None
                and hit.source_revision != item.source_revision
            ):
                continue
            if not item_is_visible_to_scope(item, query.scope):
                continue
            authoritative = await self._content.read(hit.uri, hit.layer)
            if authoritative is None:
                authoritative = item.content_for(hit.layer)
            if authoritative is None or authoritative.status != ContextLayerStatus.AVAILABLE:
                continue
            # The index is derived and may lag. Return the current catalog
            # content while retaining the index score and source attribution.
            allowed.append(
                hit.model_copy(
                    update={
                        "text": authoritative.text,
                        "content_ref": authoritative.content_ref,
                    }
                )
            )
        return allowed

    async def _score_item(
        self,
        item: ContextItem,
        query: ContextSearchQuery,
    ) -> tuple[float, ContextContent | None]:
        title_score = _lexical_score(query.query, item.title)
        best_score = title_score
        best_content: ContextContent | None = None
        for layer in query.layers:
            content = await self._content.read(item.uri, layer)
            if content is None:
                content = item.content_for(layer)
            if content is None or content.status != ContextLayerStatus.AVAILABLE:
                continue
            score = _lexical_score(query.query, content.text or "")
            if score >= best_score or best_content is None:
                best_score = max(score, title_score)
                best_content = content
        return best_score, best_content


def _lexical_score(query: str, text: str) -> float:
    normalized_query = " ".join(query.casefold().split())
    normalized_text = " ".join(text.casefold().split())
    if not normalized_query or not normalized_text:
        return 0.0
    if normalized_query in normalized_text:
        return 1.0
    query_terms = set(normalized_query.split())
    text_terms = set(normalized_text.split())
    if not query_terms:
        return 0.0
    return len(query_terms & text_terms) / len(query_terms)


def _merge_hits(
    catalog_hits: list[ContextSearchHit],
    semantic_hits: list[ContextSearchHit],
) -> list[ContextSearchHit]:
    """Merge derived-index hits without duplicate logical entries."""

    merged: dict[tuple[str, ContextLayer], ContextSearchHit] = {
        (str(hit.uri), hit.layer): hit for hit in catalog_hits
    }
    for hit in semantic_hits:
        key = (str(hit.uri), hit.layer)
        current = merged.get(key)
        if current is None or hit.score > current.score:
            merged[key] = hit
    return list(merged.values())


def _semantic_hit_is_allowed(
    hit: ContextSearchHit,
    query: ContextSearchQuery,
) -> bool:
    """Apply the catalog boundary again to untrusted derived-index output."""

    if query.root_uri is None or not hit.uri.is_within(query.root_uri):
        return False
    if query.kinds and hit.kind not in query.kinds:
        return False
    return not query.layers or hit.layer in query.layers
