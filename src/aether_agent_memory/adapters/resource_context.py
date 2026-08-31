from __future__ import annotations

from aether_agent_memory.context_store.mapping import (
    agent_scope_uri,
    context_directory_item,
    resource_collection_uri,
    resource_root_uri,
    resource_uri,
    scope_uri,
)
from aether_agent_memory.context_store.models import (
    ContextContent,
    ContextItem,
    ContextItemKind,
    ContextLayer,
    ContextLayerStatus,
    ContextVisibility,
)
from aether_agent_memory.context_store.uri import AetherUri
from aether_agent_memory.core.scope import Scope
from aether_agent_memory.resource.models import ResourceRecord
from aether_agent_memory.resource.parsing import (
    BinaryResourceContentParserPort,
    OptionalDocumentResourceParser,
    PlainTextResourceParser,
    ResourceContentParserPort,
)
from aether_agent_memory.resource.ports import ResourceStorePort
from aether_agent_memory.runtime.ports import ObjectStorePort
from aether_agent_memory.runtime.request_context import RequestContext

_MISSING_SCOPE = "_"


class ResourceContextReader:
    """Expose independently stored P3 Resources through the Context Catalog."""

    def __init__(
        self,
        store: ResourceStorePort,
        *,
        content_reader: ObjectStorePort | None = None,
        content_parser: ResourceContentParserPort | None = None,
        binary_parser: BinaryResourceContentParserPort | None = None,
    ) -> None:
        self._store = store
        self._content_reader = content_reader
        self._content_parser = content_parser or PlainTextResourceParser()
        self._binary_parser = binary_parser or OptionalDocumentResourceParser()

    async def get(self, uri: AetherUri) -> ContextItem | None:
        scope = _resource_root_scope(uri)
        if scope is not None:
            return context_directory_item(
                uri,
                scope,
                "Resources",
                directory_type="resource_root",
                visibility=ContextVisibility.AGENT,
            )
        scope, category = _resource_collection_scope(uri)
        if scope is not None:
            return context_directory_item(
                uri,
                scope,
                category.title(),
                directory_type="resource_collection",
                visibility=ContextVisibility.AGENT,
            )
        parsed = _resource_item_scope(uri)
        if parsed is None:
            return None
        scope, category, resource_id = parsed
        resource = await self._store.get(scope, resource_id)
        if resource is None or resource.category != category:
            return None
        return resource_to_context_item(
            resource,
            parser=self._content_parser,
            binary_parser=self._binary_parser,
        )

    async def list_children(
        self,
        parent: AetherUri,
        *,
        kind: ContextItemKind | None = None,
    ) -> list[ContextItem]:
        if kind is not None and kind not in {
            ContextItemKind.DIRECTORY,
            ContextItemKind.RESOURCE,
        }:
            return []
        agent_scope = _agent_scope_from_uri(parent)
        if agent_scope is not None:
            if kind is not None and kind != ContextItemKind.DIRECTORY:
                return []
            return [
                context_directory_item(
                    resource_root_uri(agent_scope),
                    agent_scope,
                    "Resources",
                    directory_type="resource_root",
                    visibility=ContextVisibility.AGENT,
                )
            ]
        session_scope = _session_scope_from_uri(parent)
        if session_scope is not None:
            if kind is not None and kind != ContextItemKind.DIRECTORY:
                return []
            agent_scope = _agent_scope(session_scope)
            return [
                context_directory_item(
                    resource_root_uri(agent_scope),
                    agent_scope,
                    "Resources",
                    directory_type="resource_root",
                    visibility=ContextVisibility.AGENT,
                    mounted_from=scope_uri(session_scope),
                )
            ]
        scope = _resource_root_scope(parent)
        if scope is not None:
            categories = {
                resource.category
                for resource in await self._store.list_scoped(
                    tenant_id=scope.tenant_id,
                    user_id=scope.user_id,
                    agent_id=scope.agent_id,
                )
            }
            categories.add("documents")
            return [
                context_directory_item(
                    resource_collection_uri(scope, category),
                    scope,
                    category.title(),
                    directory_type="resource_collection",
                    visibility=ContextVisibility.AGENT,
                )
                for category in sorted(categories)
            ]
        collection = _resource_collection_scope(parent)
        scope, category = collection
        if scope is None or (kind is not None and kind != ContextItemKind.RESOURCE):
            return []
        resources = await self._store.list_scoped(
            tenant_id=scope.tenant_id,
            user_id=scope.user_id,
            agent_id=scope.agent_id,
            category=category,
        )
        return [
            resource_to_context_item(
                resource,
                parser=self._content_parser,
                binary_parser=self._binary_parser,
            )
            for resource in resources
        ]

    async def read(self, uri: AetherUri, layer: ContextLayer) -> ContextContent | None:
        item = await self.get(uri)
        if item is None:
            return None
        content = item.content_for(layer)
        if (
            layer == ContextLayer.DETAIL
            and content is not None
            and content.text is None
            and content.content_ref is not None
            and self._content_reader is not None
        ):
            read_context = RequestContext.from_values(
                tenant_id=item.scope.tenant_id,
                user_id=item.scope.user_id,
                agent_id=item.scope.agent_id,
                session_id=item.scope.session_id,
            )
            media_type = item.metadata.get("media_type", "text/plain")
            try:
                if self._content_parser.supports(media_type):
                    text = await self._content_reader.read_text(
                        content_ref=content.content_ref,
                        context=read_context,
                    )
                    parsed = (
                        self._content_parser.parse(text, media_type)
                        if text is not None
                        else None
                    )
                elif self._binary_parser.supports(media_type):
                    read_bytes = getattr(self._content_reader, "read_bytes", None)
                    raw = (
                        await read_bytes(
                            content_ref=content.content_ref,
                            context=read_context,
                        )
                        if callable(read_bytes)
                        else None
                    )
                    parsed = (
                        self._binary_parser.parse_bytes(raw, media_type)
                        if raw is not None
                        else None
                    )
                else:
                    parsed = None
            except (UnicodeDecodeError, ValueError):
                parsed = None
            if parsed is not None:
                return content.model_copy(
                    update={
                        "status": ContextLayerStatus.AVAILABLE,
                        "text": parsed.text,
                        "token_estimate": parsed.token_estimate,
                    }
                )
            if parsed is None:
                return content.model_copy(update={"status": ContextLayerStatus.PENDING})
        return content.model_copy(deep=True) if content is not None else None

    async def close(self) -> None:
        close = getattr(self._store, "close", None)
        if close is not None:
            await close()


def resource_to_context_item(
    resource: ResourceRecord,
    *,
    parser: ResourceContentParserPort | None = None,
    binary_parser: BinaryResourceContentParserPort | None = None,
) -> ContextItem:
    resolved_parser = parser or PlainTextResourceParser()
    resolved_binary_parser = binary_parser or OptionalDocumentResourceParser()
    abstract = resource.description or resource.name
    overview = resource.overview or abstract
    detail = resource.content
    return ContextItem(
        uri=resource_uri(resource.scope, resource.resource_id, category=resource.category),
        kind=ContextItemKind.RESOURCE,
        visibility=ContextVisibility.AGENT,
        scope=resource.scope,
        title=resource.name,
        layers={
            ContextLayer.ABSTRACT: ContextContent(
                layer=ContextLayer.ABSTRACT,
                text=abstract,
                token_estimate=max(len(abstract) // 4, 1),
                derived=False,
            ),
            ContextLayer.OVERVIEW: ContextContent(
                layer=ContextLayer.OVERVIEW,
                text=overview,
                token_estimate=max(len(overview) // 4, 1),
                derived=False,
            ),
            ContextLayer.DETAIL: ContextContent(
                layer=ContextLayer.DETAIL,
                text=detail,
                content_ref=resource.content_ref,
                token_estimate=max(len(detail) // 4, 1) if detail else None,
                derived=False,
                status=(
                    ContextLayerStatus.AVAILABLE
                    if detail
                    or (
                        resource.content_ref is not None
                        and (
                            resolved_parser.supports(resource.media_type)
                            or resolved_binary_parser.supports(resource.media_type)
                        )
                    )
                    else ContextLayerStatus.PENDING
                ),
            ),
        },
        source_revision=resource.revision,
        created_at=resource.created_at,
        updated_at=resource.updated_at,
        metadata={
            "resource_id": resource.resource_id,
            "category": resource.category,
            "media_type": resource.media_type,
            "source": resource.source,
            "status": resource.status.value,
            **resource.metadata,
        },
    )


def _agent_scope_from_uri(uri: AetherUri) -> Scope | None:
    if len(uri.segments) != 6 or uri.segments[0::2] != (
        "tenants",
        "users",
        "agents",
    ):
        return None
    scope = Scope(
        tenant_id=_scope_value(uri.segments[1]),
        user_id=_scope_value(uri.segments[3]),
        agent_id=_scope_value(uri.segments[5]),
    )
    return scope if agent_scope_uri(scope) == uri else None


def _session_scope_from_uri(uri: AetherUri) -> Scope | None:
    if len(uri.segments) != 8 or uri.segments[0::2] != (
        "tenants",
        "users",
        "agents",
        "sessions",
    ):
        return None
    scope = Scope(
        tenant_id=_scope_value(uri.segments[1]),
        user_id=_scope_value(uri.segments[3]),
        agent_id=_scope_value(uri.segments[5]),
        session_id=_scope_value(uri.segments[7]),
    )
    return scope if scope_uri(scope) == uri else None


def _resource_root_scope(uri: AetherUri) -> Scope | None:
    parent = uri.parent
    if parent is None or uri.segments[-1] != "resources":
        return None
    scope = _agent_scope_from_uri(parent)
    return scope if scope is not None and resource_root_uri(scope) == uri else None


def _resource_collection_scope(uri: AetherUri) -> tuple[Scope | None, str]:
    parent = uri.parent
    if parent is None:
        return None, ""
    scope = _resource_root_scope(parent)
    category = uri.segments[-1]
    return scope, category


def _resource_item_scope(
    uri: AetherUri,
) -> tuple[Scope, str, str] | None:
    parent = uri.parent
    if parent is None:
        return None
    scope, category = _resource_collection_scope(parent)
    return (
        (scope, category, uri.segments[-1])
        if scope is not None
        else None
    )


def _scope_value(value: str) -> str | None:
    return None if value == _MISSING_SCOPE else value


def _agent_scope(scope: Scope) -> Scope:
    return Scope(
        tenant_id=scope.tenant_id,
        user_id=scope.user_id,
        agent_id=scope.agent_id,
    )
