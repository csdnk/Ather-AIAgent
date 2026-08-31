from __future__ import annotations

import asyncio
from collections import OrderedDict

from aether_agent_memory.context_store.models import (
    ContextContent,
    ContextItem,
    ContextItemKind,
    ContextLayer,
    ContextSearchHit,
    ContextSearchQuery,
    ContextSearchResult,
    ContextVisibility,
    RetrievalTrace,
)
from aether_agent_memory.context_store.uri import AetherUri
from aether_agent_memory.core.scope import Scope


class InMemoryContextCatalog:
    """Reference adapter for unit tests and local development only."""

    def __init__(self) -> None:
        self._items: dict[str, ContextItem] = {}

    async def get(self, uri: AetherUri) -> ContextItem | None:
        return self._items.get(str(uri))

    async def upsert(self, item: ContextItem) -> None:
        self._items[str(item.uri)] = item.model_copy(deep=True)

    async def list_children(
        self,
        parent: AetherUri,
        *,
        kind: ContextItemKind | None = None,
    ) -> list[ContextItem]:
        return [
            item.model_copy(deep=True)
            for item in self._items.values()
            if item.uri.parent == parent and (kind is None or item.kind == kind)
        ]


class InMemoryContextContentStore:
    """Reference authoritative-content adapter for tests and local development."""

    def __init__(self) -> None:
        self._content: dict[tuple[str, ContextLayer], ContextContent] = {}

    async def read(self, uri: AetherUri, layer: ContextLayer) -> ContextContent | None:
        content = self._content.get((str(uri), layer))
        return content.model_copy(deep=True) if content is not None else None

    async def write(self, uri: AetherUri, content: ContextContent) -> None:
        self._content[(str(uri), content.layer)] = content.model_copy(deep=True)


class InMemorySemanticIndex:
    """Deterministic reference index; not a production embedding implementation."""

    def __init__(self) -> None:
        self._entries: dict[tuple[str, ContextLayer], tuple[ContextItem, ContextContent]] = {}

    @property
    def entry_count(self) -> int:
        return len(self._entries)

    async def index(self, item: ContextItem, content: ContextContent) -> None:
        self._entries[(str(item.uri), content.layer)] = (
            item.model_copy(deep=True),
            content.model_copy(deep=True),
        )

    async def remove(self, uri: AetherUri) -> None:
        keys = [key for key in self._entries if key[0] == str(uri)]
        for key in keys:
            self._entries.pop(key, None)

    async def search(self, query: ContextSearchQuery) -> ContextSearchResult:
        hits: list[ContextSearchHit] = []
        for item, content in self._entries.values():
            if query.root_uri is not None and not item.uri.is_within(query.root_uri):
                continue
            if query.kinds and item.kind not in query.kinds:
                continue
            if query.layers and content.layer not in query.layers:
                continue
            if not _scope_matches(item.scope, query.scope, item.visibility):
                continue
            score = _reference_score(query.query, content.text or "")
            if score <= 0:
                continue
            hits.append(
                ContextSearchHit(
                    uri=item.uri,
                    kind=item.kind,
                    layer=content.layer,
                    score=score,
                    text=content.text,
                    content_ref=content.content_ref,
                    source="in-memory-reference",
                    source_revision=item.source_revision,
                )
            )
        hits.sort(key=lambda hit: hit.score, reverse=True)
        return ContextSearchResult(
            hits=hits[: query.limit],
            backend="in-memory-reference",
        )


class InMemoryContextIndexTombstones:
    """Reference logical invalidation store for local and unit-test profiles."""

    def __init__(self) -> None:
        self._invalidated: set[str] = set()
        self._lock = asyncio.Lock()

    async def invalidate(self, uri: AetherUri) -> None:
        async with self._lock:
            self._invalidated.add(str(uri))

    async def restore(self, uri: AetherUri) -> None:
        async with self._lock:
            self._invalidated.discard(str(uri))

    async def is_invalidated(self, uri: AetherUri) -> bool:
        async with self._lock:
            return str(uri) in self._invalidated

    async def close(self) -> None:
        async with self._lock:
            self._invalidated.clear()


def _scope_matches(
    candidate: Scope,
    requested: Scope,
    visibility: ContextVisibility,
) -> bool:
    fields = ["tenant_id"]
    if visibility in {
        ContextVisibility.USER,
        ContextVisibility.AGENT,
        ContextVisibility.SESSION,
    }:
        fields.append("user_id")
    if visibility in {ContextVisibility.AGENT, ContextVisibility.SESSION}:
        fields.append("agent_id")
    if visibility == ContextVisibility.SESSION:
        fields.append("session_id")
    return all(
        getattr(requested, field) is None
        or getattr(candidate, field) == getattr(requested, field)
        for field in fields
    )


def _reference_score(query: str, text: str) -> float:
    normalized_query = query.casefold().strip()
    normalized_text = text.casefold()
    if not normalized_query or not normalized_text:
        return 0.0
    if normalized_query in normalized_text:
        return 1.0
    query_terms = set(normalized_query.split())
    if not query_terms:
        return 0.0
    text_terms = set(normalized_text.split())
    return len(query_terms & text_terms) / len(query_terms)


class InMemoryRetrievalTraceStore:
    """Bounded process-local trace store for tests, demo, and integration profiles."""

    def __init__(self, max_entries: int = 1000) -> None:
        if max_entries <= 0:
            raise ValueError("max_entries must be positive")
        self._max_entries = max_entries
        self._traces: OrderedDict[str, RetrievalTrace] = OrderedDict()
        self._lock = asyncio.Lock()

    async def put(self, trace: RetrievalTrace) -> None:
        async with self._lock:
            self._traces[trace.trace_id] = trace.model_copy(deep=True)
            self._traces.move_to_end(trace.trace_id)
            while len(self._traces) > self._max_entries:
                self._traces.popitem(last=False)

    async def get(self, trace_id: str) -> RetrievalTrace | None:
        async with self._lock:
            trace = self._traces.get(trace_id)
            if trace is None:
                return None
            self._traces.move_to_end(trace_id)
            return trace.model_copy(deep=True)

    async def close(self) -> None:
        async with self._lock:
            self._traces.clear()
