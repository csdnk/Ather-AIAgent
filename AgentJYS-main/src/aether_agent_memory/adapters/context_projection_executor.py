from __future__ import annotations

from aether_agent_memory.context_store.access import (
    item_is_visible_to_scope,
    uri_is_visible_to_scope,
)
from aether_agent_memory.context_store.errors import ContextProjectionSupersededError
from aether_agent_memory.context_store.models import (
    ContextLayerStatus,
    ContextProjectionWorkItem,
)
from aether_agent_memory.context_store.ports import (
    ContextCatalogReaderPort,
    ContextContentReaderPort,
    ContextProjectionExecutorPort,
    SemanticIndexPort,
)
from aether_agent_memory.runtime.errors import ScopeError


class ContextProjectionNotReadyError(RuntimeError):
    """The authoritative Context item currently has no indexable layer."""


class ProviderContextProjectionExecutor(ContextProjectionExecutorPort):
    """Project authoritative Context data through the semantic-index Port."""

    def __init__(
        self,
        *,
        catalog: ContextCatalogReaderPort,
        content: ContextContentReaderPort,
        semantic_index: SemanticIndexPort,
    ) -> None:
        self._catalog = catalog
        self._content = content
        self._semantic_index = semantic_index

    async def execute(self, item: ContextProjectionWorkItem) -> None:
        current = await self._catalog.get(item.uri)
        if current is None:
            raise ContextProjectionSupersededError(
                f"context item was deleted before projection: {item.uri}"
            )
        if current.source_revision > item.source_revision:
            raise ContextProjectionSupersededError(
                f"context projection revision {item.source_revision} was superseded "
                f"by {current.source_revision}"
            )
        if current.source_revision < item.source_revision:
            raise ContextProjectionNotReadyError(
                f"context projection revision {item.source_revision} is not available; "
                f"current revision is {current.source_revision}"
            )
        if not uri_is_visible_to_scope(item.uri, item.scope) or not item_is_visible_to_scope(
            current, item.scope
        ):
            raise ScopeError("context projection work is outside its authorized scope")

        indexed = 0
        for layer in item.layers:
            content = await self._content.read(item.uri, layer)
            if content is None:
                content = current.content_for(layer)
            if content is None or content.status != ContextLayerStatus.AVAILABLE:
                continue
            await self._semantic_index.index(current, content)
            indexed += 1
        if indexed == 0:
            raise ContextProjectionNotReadyError(
                f"context item has no available projection layers: {item.uri}"
            )
        await self._ensure_projection_is_current(item)

    async def _ensure_projection_is_current(
        self, item: ContextProjectionWorkItem
    ) -> None:
        """Close the read-index-delete race against the authoritative catalog."""
        latest = await self._catalog.get(item.uri)
        current = (
            latest is not None
            and latest.source_revision == item.source_revision
            and uri_is_visible_to_scope(item.uri, item.scope)
            and item_is_visible_to_scope(latest, item.scope)
        )
        if current:
            return
        await self._semantic_index.remove(item.uri)
        latest_revision = latest.source_revision if latest is not None else "deleted"
        raise ContextProjectionSupersededError(
            f"context projection revision {item.source_revision} changed during "
            f"indexing; current revision is {latest_revision}"
        )


__all__ = [
    "ContextProjectionSupersededError",
    "ContextProjectionNotReadyError",
    "ProviderContextProjectionExecutor",
]
