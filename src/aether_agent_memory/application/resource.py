from __future__ import annotations

from datetime import UTC, datetime

from pydantic import BaseModel, Field, model_validator

from aether_agent_memory.application.scope import require_agent_scope
from aether_agent_memory.context_store import (
    AetherUri,
    ContextDeleteResult,
    ContextItemKind,
    ContextLayer,
    ContextProjectionWorkItem,
    resource_uri,
)
from aether_agent_memory.context_store.errors import ContextRevisionConflictError
from aether_agent_memory.context_store.ports import (
    ContextProjectionQueuePort,
    SemanticIndexPort,
)
from aether_agent_memory.core.scope import Scope
from aether_agent_memory.resource.models import ResourceRecord, ResourceStatus
from aether_agent_memory.resource.parsing import (
    BinaryResourceContentParserPort,
    OptionalDocumentResourceParser,
    PlainTextResourceParser,
    ResourceContentParserPort,
)
from aether_agent_memory.resource.ports import ResourceStorePort
from aether_agent_memory.runtime.errors import ConflictError
from aether_agent_memory.runtime.ports import ObjectStorePort
from aether_agent_memory.runtime.request_context import RequestContext


class ResourceRegistration(BaseModel):
    resource_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    scope: Scope
    category: str = "documents"
    media_type: str = "text/plain"
    description: str = ""
    overview: str = ""
    content: str | None = None
    content_ref: str | None = None
    source: str = "p3"
    metadata: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _requires_content_source(self) -> ResourceRegistration:
        if not (self.content and self.content.strip()) and not self.content_ref:
            raise ValueError("resource requires content or content_ref")
        if any(
            getattr(self.scope, field) is None
            for field in ("tenant_id", "user_id", "agent_id")
        ):
            raise ValueError("resource requires tenant/user/agent scope")
        return self


class RegisterResourceUseCase:
    """Register a P3 Resource without putting parsing in the HTTP layer."""

    def __init__(
        self,
        *,
        resources: ResourceStorePort,
        content_store: ObjectStorePort | None = None,
        content_parser: ResourceContentParserPort | None = None,
        binary_parser: BinaryResourceContentParserPort | None = None,
        context_projection_queue: ContextProjectionQueuePort | None = None,
        semantic_index: SemanticIndexPort | None = None,
    ) -> None:
        self._resources = resources
        self._content_store = content_store
        self._content_parser = content_parser or PlainTextResourceParser()
        self._binary_parser = binary_parser or OptionalDocumentResourceParser()
        self._context_projection_queue = context_projection_queue
        self._semantic_index = semantic_index

    async def execute(
        self,
        registration: ResourceRegistration,
        context: RequestContext,
    ) -> ResourceRecord:
        scope = require_agent_scope(
            context,
            expected=registration.scope,
            operation="resource registration",
        )
        content = (
            registration.content
            if registration.content and registration.content.strip()
            else None
        )
        content_ref = registration.content_ref
        metadata = dict(registration.metadata)
        if content:
            parsed = self._content_parser.parse(content, registration.media_type)
            content = parsed.text
            metadata.setdefault("content_parser", parsed.parser)
        if content and content_ref is None and self._content_store is not None:
            reference = await self._content_store.put_text(
                text=content,
                object_key=f"resources/{registration.resource_id}.txt",
                context=context,
            )
            content_ref = reference.content_ref
            content = None
            metadata.setdefault("content_authority", "object-store")
        elif content:
            metadata.setdefault("content_authority", "inline")
        else:
            metadata.setdefault("content_authority", "external-ref")
            metadata.setdefault(
                "content_parser",
                self._content_parser.name
                if self._content_parser.supports(registration.media_type)
                else (
                    self._binary_parser.name
                    if self._binary_parser.supports(registration.media_type)
                    else "unavailable"
                ),
            )

        existing = await self._resources.get(scope, registration.resource_id)
        revision = await self._resources.reserve_revision(
            scope,
            registration.resource_id,
            minimum=existing.revision if existing is not None else 0,
        )
        record = ResourceRecord(
            resource_id=registration.resource_id,
            name=registration.name,
            scope=scope,
            category=registration.category,
            media_type=registration.media_type,
            description=registration.description,
            overview=registration.overview,
            content=content,
            content_ref=content_ref,
            source=registration.source,
            status=ResourceStatus.READY,
            revision=revision,
            metadata=metadata,
            created_at=existing.created_at if existing is not None else datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        try:
            await self._resources.upsert(record)
        except ContextRevisionConflictError as exc:
            raise ConflictError(str(exc), trace_id=context.trace_id) from exc
        if existing is not None and existing.category != record.category:
            await _invalidate_index(
                self._semantic_index,
                resource_uri(scope, record.resource_id, category=existing.category),
            )
        if self._context_projection_queue is not None:
            try:
                await self._context_projection_queue.enqueue(
                    ContextProjectionWorkItem(
                        uri=resource_uri(
                            scope, record.resource_id, category=record.category
                        ),
                        source_revision=record.revision,
                        scope=scope,
                        layers=[ContextLayer.ABSTRACT, ContextLayer.OVERVIEW],
                    )
                )
            except Exception:
                return record.model_copy(
                    update={
                        "metadata": {
                            **record.metadata,
                            "context_projection_status": "queue_failed",
                        }
                    }
                )
            return record.model_copy(
                update={
                    "metadata": {
                        **record.metadata,
                        "context_projection_status": "pending",
                    }
                }
            )
        return record


class DeleteResourceUseCase:
    """Delete a P3 Resource fact and invalidate its derived index entry."""

    def __init__(
        self,
        *,
        resources: ResourceStorePort,
        semantic_index: SemanticIndexPort | None = None,
    ) -> None:
        self._resources = resources
        self._semantic_index = semantic_index

    async def execute(
        self,
        resource_id: str,
        context: RequestContext,
        *,
        category: str = "documents",
    ) -> ContextDeleteResult:
        scope = require_agent_scope(context, operation="resource deletion")
        existing = await self._resources.get(scope, resource_id)
        uri = resource_uri(
            scope,
            resource_id,
            category=existing.category if existing is not None else category,
        )
        if existing is None:
            return ContextDeleteResult(
                uri=uri,
                kind=ContextItemKind.RESOURCE,
                deleted=False,
            )
        deleted = await self._resources.delete(scope, resource_id)
        invalidated = (
            await _invalidate_index(self._semantic_index, uri) if deleted else False
        )
        return ContextDeleteResult(
            uri=uri,
            kind=ContextItemKind.RESOURCE,
            deleted=deleted,
            index_invalidated=invalidated,
        )


async def _invalidate_index(
    semantic_index: SemanticIndexPort | None,
    uri: AetherUri,
) -> bool:
    if semantic_index is None:
        return False
    try:
        await semantic_index.remove(uri)
    except Exception:
        return False
    return True
