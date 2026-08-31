from __future__ import annotations

import base64
import binascii
import json
from collections import deque

from pydantic import BaseModel, Field

from aether_agent_memory.context_store.models import ContextLayer, ContextLayerStatus
from aether_agent_memory.context_store.ports import (
    ContextCatalogReaderPort,
    ContextContentReaderPort,
    SemanticIndexPort,
)
from aether_agent_memory.context_store.uri import AetherUri


class ReindexReport(BaseModel):
    root_uri: AetherUri
    scanned_items: int = Field(default=0, ge=0)
    indexed_layers: int = Field(default=0, ge=0)
    skipped_layers: int = Field(default=0, ge=0)
    failed_layers: int = Field(default=0, ge=0)
    complete: bool = True
    truncated_items: int = Field(default=0, ge=0)
    next_cursor: str | None = None
    errors: dict[str, str] = Field(default_factory=dict)


class _ReindexCursor(BaseModel):
    version: int = 1
    root_uri: str
    layers: list[str]
    pending: list[str] = Field(default_factory=list)
    deferred: list[str] = Field(default_factory=list)
    visited: list[str] = Field(default_factory=list)


class ReindexService:
    """Rebuild a derived semantic index from authoritative context data."""

    def __init__(
        self,
        catalog: ContextCatalogReaderPort,
        content_store: ContextContentReaderPort,
        semantic_index: SemanticIndexPort,
        *,
        max_items: int = 10_000,
        max_children_per_directory: int = 1_000,
    ) -> None:
        if max_items < 1:
            raise ValueError("reindex max_items must be positive")
        if max_children_per_directory < 1:
            raise ValueError("reindex max_children_per_directory must be positive")
        self._catalog = catalog
        self._content_store = content_store
        self._semantic_index = semantic_index
        self._max_items = max_items
        self._max_children_per_directory = max_children_per_directory

    async def rebuild(
        self,
        root_uri: AetherUri,
        *,
        layers: tuple[ContextLayer, ...] = (
            ContextLayer.ABSTRACT,
            ContextLayer.OVERVIEW,
        ),
        cursor: str | None = None,
    ) -> ReindexReport:
        report = ReindexReport(root_uri=root_uri)
        pending, deferred, visited = _restore_state(root_uri, layers, cursor)
        while pending:
            if len(visited) >= self._max_items:
                report.complete = False
                report.truncated_items += len(pending) + len(deferred)
                report.next_cursor = _encode_cursor(
                    root_uri, layers, pending, deferred, visited
                )
                break
            uri = pending.popleft()
            uri_key = str(uri)
            if uri_key in visited:
                continue
            visited.add(uri_key)
            item = await self._catalog.get(uri)
            if item is not None:
                report.scanned_items += 1
                # Virtual directory summaries guide traversal directly. Keep
                # the derived vector index focused on addressable leaf objects
                # so adding directory L0/L1 does not duplicate catalog entries.
                if item.kind.value != "directory":
                    for layer in layers:
                        content = await self._content_store.read(uri, layer)
                        if content is None:
                            content = item.content_for(layer)
                        if content is None or content.status != ContextLayerStatus.AVAILABLE:
                            report.skipped_layers += 1
                            continue
                        try:
                            await self._semantic_index.index(item, content)
                        except Exception as exc:
                            report.failed_layers += 1
                            report.errors[
                                f"{item.uri}#{layer.value}"
                            ] = f"{type(exc).__name__}: {exc}"
                        else:
                            report.indexed_layers += 1
            children = await self._catalog.list_children(uri)
            if len(children) > self._max_children_per_directory:
                report.complete = False
                first_page = children[: self._max_children_per_directory]
                deferred.extend(
                    child.uri
                    for child in children[self._max_children_per_directory :]
                )
                report.truncated_items += len(deferred)
                pending.extend(child.uri for child in first_page)
                report.next_cursor = _encode_cursor(
                    root_uri, layers, pending, deferred, visited
                )
                break
            pending.extend(child.uri for child in children)
            if not pending and deferred:
                page_size = min(
                    self._max_children_per_directory,
                    len(deferred),
                )
                pending.extend(deferred.popleft() for _ in range(page_size))
        if report.failed_layers:
            report.complete = False
        if report.complete and deferred:
            report.complete = False
            report.next_cursor = _encode_cursor(
                root_uri, layers, pending, deferred, visited
            )
        return report


def _restore_state(
    root_uri: AetherUri,
    layers: tuple[ContextLayer, ...],
    cursor: str | None,
) -> tuple[deque[AetherUri], deque[AetherUri], set[str]]:
    if cursor is None:
        return deque([root_uri]), deque(), set()
    if len(cursor) > 1_000_000:
        raise ValueError("reindex cursor is too large")
    try:
        encoded = cursor.removeprefix("aether-reindex-v1.")
        payload = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
        state = _ReindexCursor.model_validate_json(payload)
    except (binascii.Error, UnicodeDecodeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError("invalid reindex cursor") from exc
    if state.version != 1 or state.root_uri != str(root_uri):
        raise ValueError("reindex cursor does not match root_uri")
    if state.layers != [layer.value for layer in layers]:
        raise ValueError("reindex cursor does not match requested layers")
    try:
        pending = deque(AetherUri(value) for value in state.pending)
        deferred = deque(AetherUri(value) for value in state.deferred)
    except (TypeError, ValueError) as exc:
        raise ValueError("reindex cursor contains an invalid URI") from exc
    return pending, deferred, set(state.visited)


def _encode_cursor(
    root_uri: AetherUri,
    layers: tuple[ContextLayer, ...],
    pending: deque[AetherUri],
    deferred: deque[AetherUri],
    visited: set[str],
) -> str:
    state = _ReindexCursor(
        root_uri=str(root_uri),
        layers=[layer.value for layer in layers],
        pending=[str(uri) for uri in pending],
        deferred=[str(uri) for uri in deferred],
        visited=sorted(visited),
    )
    encoded = base64.urlsafe_b64encode(state.model_dump_json().encode()).decode()
    return f"aether-reindex-v1.{encoded.rstrip('=')}"
