from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from aether_agent_memory.context_store.models import (
    ContextContent,
    ContextItem,
    ContextItemKind,
    ContextLayer,
    ContextProjectionWorkItem,
    ContextSearchQuery,
    ContextSearchResult,
    RetrievalTrace,
)
from aether_agent_memory.context_store.uri import AetherUri

if TYPE_CHECKING:
    from aether_agent_memory.context_store.reindex import ReindexReport


class ContextCatalogReaderPort(Protocol):
    """Authoritative metadata tree for logical context items."""

    async def get(self, uri: AetherUri) -> ContextItem | None: ...

    async def list_children(
        self,
        parent: AetherUri,
        *,
        kind: ContextItemKind | None = None,
    ) -> list[ContextItem]: ...


class ContextCatalogWriterPort(Protocol):
    async def upsert(self, item: ContextItem) -> None: ...


class ContextCatalogPort(ContextCatalogReaderPort, ContextCatalogWriterPort, Protocol):
    pass


class ContextContentReaderPort(Protocol):
    """Source-of-truth content access; indexes must remain rebuildable from it."""

    async def read(self, uri: AetherUri, layer: ContextLayer) -> ContextContent | None: ...


class ContextContentWriterPort(Protocol):
    async def write(self, uri: AetherUri, content: ContextContent) -> None: ...


class ContextContentPort(ContextContentReaderPort, ContextContentWriterPort, Protocol):
    pass


class SemanticIndexPort(Protocol):
    """Derived semantic index; no provider response types cross this boundary."""

    async def index(self, item: ContextItem, content: ContextContent) -> None: ...

    async def remove(self, uri: AetherUri) -> None: ...

    async def search(self, query: ContextSearchQuery) -> ContextSearchResult: ...


class ContextIndexTombstonePort(Protocol):
    """Durable logical invalidation for derived Context index entries."""

    async def invalidate(self, uri: AetherUri) -> None: ...

    async def restore(self, uri: AetherUri) -> None: ...

    async def is_invalidated(self, uri: AetherUri) -> bool: ...

    async def close(self) -> None: ...


class ContextReindexPort(Protocol):
    """Operational recovery boundary for rebuilding derived context indexes."""

    async def rebuild(
        self,
        root_uri: AetherUri,
        *,
        layers: tuple[ContextLayer, ...],
        cursor: str | None = None,
    ) -> ReindexReport: ...


class ContextProjectionQueuePort(Protocol):
    """Durable delivery boundary for Context-derived projections."""

    async def enqueue(
        self, item: ContextProjectionWorkItem
    ) -> ContextProjectionWorkItem: ...

    async def claim(self, work_id: str) -> ContextProjectionWorkItem | None: ...

    async def complete(self, work_id: str) -> ContextProjectionWorkItem | None: ...

    async def fail(
        self, work_id: str, error: str
    ) -> ContextProjectionWorkItem | None: ...

    async def supersede(self, work_id: str) -> ContextProjectionWorkItem | None: ...

    async def retry(self, work_id: str) -> ContextProjectionWorkItem | None: ...

    async def pending(self) -> list[ContextProjectionWorkItem]: ...

    async def close(self) -> None: ...


class ContextProjectionExecutorPort(Protocol):
    """Execute derived Context work without exposing provider response types."""

    async def execute(self, item: ContextProjectionWorkItem) -> None: ...


class ContextSearchPort(Protocol):
    """Application-facing search over the logical context hierarchy."""

    async def search(self, query: ContextSearchQuery) -> ContextSearchResult: ...


class RetrievalTraceStorePort(Protocol):
    async def put(self, trace: RetrievalTrace) -> None: ...

    async def get(self, trace_id: str) -> RetrievalTrace | None: ...

    async def close(self) -> None: ...
