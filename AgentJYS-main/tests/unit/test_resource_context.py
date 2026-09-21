from __future__ import annotations

import pytest

from aether_agent_memory.adapters.resource_context import (
    ResourceContextReader,
    resource_to_context_item,
)
from aether_agent_memory.adapters.resource_store import InMemoryResourceStore
from aether_agent_memory.application.resource import (
    DeleteResourceUseCase,
    RegisterResourceUseCase,
    ResourceRegistration,
)
from aether_agent_memory.context_store import (
    ContextItemKind,
    ContextLayer,
    ContextSearchQuery,
    resource_collection_uri,
    resource_root_uri,
    resource_uri,
    scope_uri,
)
from aether_agent_memory.context_store.in_memory import InMemorySemanticIndex
from aether_agent_memory.core.scope import Scope
from aether_agent_memory.resource.models import ResourceRecord
from aether_agent_memory.resource.parsing import (
    ParsedResourceContent,
    PlainTextResourceParser,
)
from aether_agent_memory.runtime.errors import ScopeError
from aether_agent_memory.runtime.request_context import RequestContext


def _scope() -> Scope:
    return Scope(tenant_id="tenant-1", user_id="user-1", agent_id="agent-1")


@pytest.mark.unit
@pytest.mark.asyncio
async def test_resource_is_first_class_context_item_with_authoritative_ref() -> None:
    resource = ResourceRecord(
        resource_id="prd-1",
        name="P3 PRD",
        description="P3 architecture requirements",
        overview="Runtime and context database requirements",
        content_ref="object://documents/p3-prd.md",
        scope=_scope(),
    )
    reader = ResourceContextReader(InMemoryResourceStore([resource]))

    item = await reader.get(resource_uri(_scope(), resource.resource_id))
    detail = await reader.read(
        resource_uri(_scope(), resource.resource_id),
        ContextLayer.DETAIL,
    )

    assert item is not None and item.kind == ContextItemKind.RESOURCE
    assert item.visibility.value == "agent"
    assert item.content_for(ContextLayer.ABSTRACT).text == resource.description
    assert detail is not None
    assert detail.text is None
    assert detail.content_ref == resource.content_ref


@pytest.mark.unit
@pytest.mark.asyncio
async def test_resource_reader_lists_category_and_session_mount() -> None:
    resource = ResourceRecord(
        resource_id="report-1",
        name="Report",
        category="reports",
        scope=_scope(),
        content="report body",
    )
    reader = ResourceContextReader(InMemoryResourceStore([resource]))
    session_scope = Scope(
        tenant_id="tenant-1",
        user_id="user-1",
        agent_id="agent-1",
        session_id="session-1",
    )

    mounted = await reader.list_children(scope_uri(session_scope))
    categories = await reader.list_children(resource_root_uri(_scope()))
    resources = await reader.list_children(resource_collection_uri(_scope(), "reports"))

    assert mounted[0].metadata["mounted"] is True
    assert [item.uri for item in categories] == [
        resource_collection_uri(_scope(), "documents"),
        resource_collection_uri(_scope(), "reports"),
    ]
    assert [item.metadata["resource_id"] for item in resources] == ["report-1"]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_resource_reader_lazily_resolves_external_l2_content() -> None:
    resource = ResourceRecord(
        resource_id="external-1",
        name="External document",
        scope=_scope(),
        content_ref="object://documents/external.md",
    )

    class _ContentReader:
        async def read_text(self, *, content_ref, context):
            assert content_ref == resource.content_ref
            assert context.scope.agent_id == "agent-1"
            return "loaded from the authoritative object store"

    reader = ResourceContextReader(
        InMemoryResourceStore([resource]),
        content_reader=_ContentReader(),
    )
    detail = await reader.read(
        resource_uri(_scope(), resource.resource_id),
        ContextLayer.DETAIL,
    )

    assert detail is not None
    assert detail.text == "loaded from the authoritative object store"
    assert detail.token_estimate == 10


@pytest.mark.unit
def test_plain_text_parser_rejects_binary_media_without_a_binary_adapter() -> None:
    parser = PlainTextResourceParser()

    assert parser.supports("text/markdown; charset=utf-8")
    assert not parser.supports("application/pdf")
    with pytest.raises(ValueError, match="application/pdf"):
        parser.parse("not a PDF", "application/pdf")


@pytest.mark.unit
@pytest.mark.asyncio
async def test_external_binary_resource_is_pending_until_parser_is_available() -> None:
    resource = ResourceRecord(
        resource_id="pdf-1",
        name="Pending PDF",
        scope=_scope(),
        media_type="application/pdf",
        content_ref="object://documents/pending.pdf",
    )
    class _UnavailableBinaryParser:
        name = "unavailable"

        def supports(self, media_type):
            return False

        def parse_bytes(self, content, media_type):
            raise AssertionError("unavailable parser must not be called")

    reader = ResourceContextReader(
        InMemoryResourceStore([resource]),
        binary_parser=_UnavailableBinaryParser(),
    )

    item = await reader.get(resource_uri(_scope(), resource.resource_id))
    detail = await reader.read(
        resource_uri(_scope(), resource.resource_id),
        ContextLayer.DETAIL,
    )

    assert item is not None
    assert detail is not None
    assert detail.status.value == "pending"
    assert detail.text is None
    assert detail.content_ref == resource.content_ref


@pytest.mark.unit
@pytest.mark.asyncio
async def test_context_reader_uses_binary_parser_port_for_external_l2() -> None:
    resource = ResourceRecord(
        resource_id="pdf-2",
        name="Binary document",
        scope=_scope(),
        media_type="application/pdf",
        content_ref="object://documents/ready.pdf",
    )

    class _ContentReader:
        async def read_bytes(self, *, content_ref, context):
            assert content_ref == resource.content_ref
            assert context.scope == _scope()
            return b"binary payload"

    class _BinaryParser:
        name = "test-binary-parser"

        def supports(self, media_type):
            return media_type == "application/pdf"

        def parse_bytes(self, content, media_type):
            assert content == b"binary payload"
            return ParsedResourceContent(
                text="decoded binary content",
                media_type=media_type,
                parser=self.name,
                token_estimate=5,
            )

    reader = ResourceContextReader(
        InMemoryResourceStore([resource]),
        content_reader=_ContentReader(),
        binary_parser=_BinaryParser(),
    )
    detail = await reader.read(
        resource_uri(_scope(), resource.resource_id),
        ContextLayer.DETAIL,
    )

    assert detail is not None
    assert detail.status.value == "available"
    assert detail.text == "decoded binary content"
    assert detail.token_estimate == 5


@pytest.mark.unit
@pytest.mark.asyncio
async def test_resource_category_move_invalidates_previous_canonical_uri() -> None:
    scope = _scope()
    context = RequestContext.from_values(
        request_id="request-1",
        trace_id="trace-1",
        tenant_id=scope.tenant_id,
        user_id=scope.user_id,
        agent_id=scope.agent_id,
    )
    store = InMemoryResourceStore()
    index = InMemorySemanticIndex()
    registration = RegisterResourceUseCase(resources=store, semantic_index=index)
    first = await registration.execute(
        ResourceRegistration(
            resource_id="resource-1",
            name="Architecture",
            scope=scope,
            category="documents",
            content="first version",
        ),
        context,
    )
    first_item = resource_to_context_item(first)
    first_content = first_item.content_for(ContextLayer.ABSTRACT)
    assert first_content is not None
    await index.index(first_item, first_content)

    second = await registration.execute(
        ResourceRegistration(
            resource_id="resource-1",
            name="Architecture",
            scope=scope,
            category="reports",
            content="second version",
        ),
        context,
    )

    assert second.revision == 2
    assert second.category == "reports"
    assert index.entry_count == 0


@pytest.mark.unit
@pytest.mark.asyncio
async def test_resource_registration_rejects_mismatched_request_scope() -> None:
    store = InMemoryResourceStore()
    context = RequestContext.from_values(
        tenant_id="tenant-1",
        user_id="user-1",
        agent_id="agent-2",
    )

    with pytest.raises(ScopeError, match="does not match"):
        await RegisterResourceUseCase(resources=store).execute(
            ResourceRegistration(
                resource_id="private",
                name="Private",
                scope=_scope(),
                content="must not cross scope",
            ),
            context,
        )

    assert await store.get(_scope(), "private") is None


@pytest.mark.unit
@pytest.mark.asyncio
async def test_resource_delete_removes_fact_and_derived_index_entry() -> None:
    resource = ResourceRecord(
        resource_id="delete-me",
        name="Disposable resource",
        scope=_scope(),
        content="derived resource content",
    )
    store = InMemoryResourceStore([resource])
    reader = ResourceContextReader(store)
    item = await reader.get(resource_uri(_scope(), resource.resource_id))
    assert item is not None
    index = InMemorySemanticIndex()
    await index.index(item, item.layers[ContextLayer.OVERVIEW])
    context = RequestContext.from_values(
        tenant_id="tenant-1",
        user_id="user-1",
        agent_id="agent-1",
    )

    result = await DeleteResourceUseCase(
        resources=store,
        semantic_index=index,
    ).execute(resource.resource_id, context)

    assert result.deleted is True
    assert result.index_invalidated is True
    assert await store.get(_scope(), resource.resource_id) is None
    search = await index.search(
        ContextSearchQuery(query="resource", scope=_scope())
    )
    assert search.hits == []


@pytest.mark.unit
@pytest.mark.asyncio
async def test_resource_delete_cannot_remove_another_agent_item() -> None:
    resource = ResourceRecord(
        resource_id="private-resource",
        name="Private resource",
        scope=_scope(),
        content="private",
    )
    store = InMemoryResourceStore([resource])
    foreign_context = RequestContext.from_values(
        tenant_id="tenant-1",
        user_id="user-1",
        agent_id="agent-2",
    )

    result = await DeleteResourceUseCase(resources=store).execute(
        resource.resource_id,
        foreign_context,
    )

    assert result.deleted is False
    assert await store.get(_scope(), resource.resource_id) is not None
