from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from aether_agent_memory.api.dependencies import enforce_scope, json_model, runtime_of
from aether_agent_memory.context_store import (
    AetherUri,
    ContextItem,
    ContextItemKind,
    ContextLayer,
    ContextSearchQuery,
)
from aether_agent_memory.integration import request_context_from_payload

router = APIRouter()


class CatalogScopeRequest(BaseModel):
    tenant_id: str
    user_id: str
    agent_id: str
    session_id: str
    request_id: str | None = None
    trace_id: str | None = None


class CatalogChildrenRequest(CatalogScopeRequest):
    parent_uri: AetherUri
    kind: ContextItemKind | None = None


class CatalogItemRequest(CatalogScopeRequest):
    uri: AetherUri


class CatalogSearchRequest(CatalogScopeRequest):
    query: str = Field(min_length=1)
    root_uri: AetherUri | None = None
    kinds: list[ContextItemKind] = Field(default_factory=list)
    layers: list[ContextLayer] = Field(
        default_factory=lambda: [ContextLayer.ABSTRACT, ContextLayer.OVERVIEW]
    )
    limit: int = Field(default=20, gt=0, le=1000)


class CatalogReindexRequest(CatalogScopeRequest):
    root_uri: AetherUri | None = None
    layers: list[ContextLayer] = Field(
        default_factory=lambda: [ContextLayer.ABSTRACT, ContextLayer.OVERVIEW]
    )
    cursor: str | None = None


@router.post("/api/v1/context/catalog/children")
async def catalog_children(
    request: Request,
    body: CatalogChildrenRequest,
) -> dict[str, list[dict[str, Any]]]:
    runtime = runtime_of(request)
    enforce_scope(body)
    context = request_context_from_payload(body)
    items = await runtime.list_context_children(
        body.parent_uri,
        context,
        kind=body.kind,
    )
    return {"items": [_json_item(item) for item in items]}


@router.post("/api/v1/context/catalog/item")
async def catalog_item(
    request: Request,
    body: CatalogItemRequest,
) -> dict[str, dict[str, Any] | None]:
    runtime = runtime_of(request)
    enforce_scope(body)
    context = request_context_from_payload(body)
    item = await runtime.get_context_item(body.uri, context)
    return {"item": _json_item(item) if item is not None else None}


@router.post("/api/v1/context/catalog/search")
async def catalog_search(
    request: Request,
    body: CatalogSearchRequest,
) -> dict[str, Any]:
    runtime = runtime_of(request)
    enforce_scope(body)
    context = request_context_from_payload(body)
    query = ContextSearchQuery(
        query=body.query,
        scope=context.scope,
        root_uri=body.root_uri,
        kinds=body.kinds,
        layers=body.layers,
        limit=body.limit,
    )
    result = await runtime.search_context(query, context)
    return json_model(result)


@router.post("/api/v1/context/catalog/reindex")
async def catalog_reindex(
    request: Request,
    body: CatalogReindexRequest,
) -> dict[str, Any]:
    runtime = runtime_of(request)
    enforce_scope(body)
    context = request_context_from_payload(body)
    report = await runtime.reindex_context(
        context,
        root_uri=body.root_uri,
        layers=tuple(body.layers),
        cursor=body.cursor,
    )
    return json_model(report)


def _json_item(item: ContextItem) -> dict[str, Any]:
    payload = item.model_dump(mode="json")
    if not isinstance(payload, dict):
        raise TypeError("context item did not serialize to a mapping")
    return dict(payload)


__all__ = [
    "CatalogChildrenRequest",
    "CatalogItemRequest",
    "CatalogSearchRequest",
    "CatalogReindexRequest",
    "router",
]
