from __future__ import annotations

from aether_agent_memory.context_store.models import (
    ContextContent,
    ContextItem,
    ContextItemKind,
    ContextLayer,
)
from aether_agent_memory.context_store.ports import (
    ContextCatalogReaderPort,
    ContextContentReaderPort,
)
from aether_agent_memory.context_store.uri import AetherUri


class CompositeContextReader:
    """Merge independently owned context namespaces into one logical catalog."""

    def __init__(
        self,
        readers: list[ContextCatalogReaderPort],
        content_readers: list[ContextContentReaderPort],
    ) -> None:
        self._readers = list(readers)
        self._content_readers = list(content_readers)

    async def get(self, uri: AetherUri) -> ContextItem | None:
        for reader in self._readers:
            item = await reader.get(uri)
            if item is not None:
                return item
        return None

    async def list_children(
        self,
        parent: AetherUri,
        *,
        kind: ContextItemKind | None = None,
    ) -> list[ContextItem]:
        merged: dict[str, ContextItem] = {}
        for reader in self._readers:
            for item in await reader.list_children(parent, kind=kind):
                merged.setdefault(str(item.uri), item)
        return [merged[key] for key in sorted(merged)]

    async def read(self, uri: AetherUri, layer: ContextLayer) -> ContextContent | None:
        for reader in self._content_readers:
            content = await reader.read(uri, layer)
            if content is not None:
                return content
        return None

    async def close(self) -> None:
        closed: set[int] = set()
        for reader in [*self._readers, *self._content_readers]:
            reader_id = id(reader)
            if reader_id in closed:
                continue
            closed.add(reader_id)
            close = getattr(reader, "close", None)
            if close is not None:
                await close()
