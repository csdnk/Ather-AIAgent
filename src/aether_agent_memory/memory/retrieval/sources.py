from __future__ import annotations

from typing import Protocol
from uuid import uuid4

from aether_agent_memory.context import ContextRequest
from aether_agent_memory.context_store.mapping import (
    agent_scope_uri,
    memory_uri,
    resource_uri,
    scope_uri,
)
from aether_agent_memory.context_store.models import (
    ContextItemKind,
    ContextLayer,
    ContextSearchQuery,
)
from aether_agent_memory.context_store.ports import ContextSearchPort
from aether_agent_memory.core.enums import MemoryType
from aether_agent_memory.core.memory import RecalledMemory
from aether_agent_memory.core.scope import Scope
from aether_agent_memory.memory.retrieval.models import RecallCandidate, RecallSourceResult
from aether_agent_memory.runtime.dtos import MemorySearchResult
from aether_agent_memory.runtime.ports import VectorSearchPort
from aether_agent_memory.runtime.request_context import RequestContext


class RecallSource(Protocol):
    @property
    def name(self) -> str: ...

    async def recall(
        self,
        request: ContextRequest,
        context: RequestContext,
    ) -> list[RecallCandidate] | RecallSourceResult: ...


class MemoryRecallPort(Protocol):
    """Minimal B2-compatible recall boundary used by P3 retrieval sources."""

    async def recall(self, request: ContextRequest) -> list[RecalledMemory]: ...


class MemoryManagerRecallSource:
    def __init__(
        self,
        name: str,
        manager: MemoryRecallPort,
        *,
        memory_type: MemoryType,
    ) -> None:
        self._name = name
        self._manager = manager
        self._memory_type = memory_type

    @property
    def name(self) -> str:
        return self._name

    async def recall(
        self,
        request: ContextRequest,
        context: RequestContext,
    ) -> list[RecallCandidate]:
        if self._memory_type not in request.memory_types:
            return []
        recalled = await self._manager.recall(request)
        return [_candidate_from_recalled(item, self.name, context) for item in recalled]


class WorkingRecallSource(MemoryManagerRecallSource):
    def __init__(self, manager: MemoryRecallPort) -> None:
        super().__init__("working", manager, memory_type=MemoryType.WORKING)


class EpisodicRecallSource(MemoryManagerRecallSource):
    def __init__(self, manager: MemoryRecallPort) -> None:
        super().__init__("episodic", manager, memory_type=MemoryType.EPISODIC)


class SemanticRecallSource(MemoryManagerRecallSource):
    def __init__(self, manager: MemoryRecallPort) -> None:
        super().__init__("semantic", manager, memory_type=MemoryType.SEMANTIC)


class LongTermRecallSource(SemanticRecallSource):
    """Backward-compatible alias for the semantic long-term source."""


class P2E1RecallSource:
    """Long-document recall from P2 E1 vectors through the vector-search port."""

    def __init__(self, vector_search: VectorSearchPort, name: str = "long_document") -> None:
        self._vector_search = vector_search
        self._name = name

    @property
    def name(self) -> str:
        return self._name

    async def recall(
        self,
        request: ContextRequest,
        context: RequestContext,
    ) -> list[RecallCandidate]:
        if MemoryType.SEMANTIC not in request.memory_types:
            return []
        tenant_id = context.tenant_id or request.tenant_id
        user_id = context.user_id or request.user_id
        agent_id = context.agent_id or request.agent_id
        if not request.query or not tenant_id or not user_id or not agent_id:
            return []
        search = await self._vector_search.search_memory(
            query=request.query,
            tenant_id=tenant_id,
            user_id=user_id,
            agent_id=agent_id,
            limit=request.max_candidates,
            context=context,
        )
        result = (
            search
            if isinstance(search, MemorySearchResult)
            else MemorySearchResult.from_mapping(search)
        )
        candidates: list[RecallCandidate] = []
        scope = Scope(
            tenant_id=tenant_id,
            user_id=user_id,
            agent_id=agent_id,
            session_id=context.session_id,
        )
        for item in result.items:
            content_ref = item.content_ref
            item_id = item.memory_id or item.chunk_id or item.task_id or uuid4().hex
            candidates.append(
                RecallCandidate(
                    memory_id=item_id,
                    context_uri=resource_uri(
                        scope,
                        item_id,
                    ),
                    context_kind=ContextItemKind.RESOURCE,
                    scope=scope,
                    content=item.text,
                    content_ref=content_ref,
                    source=self._name,
                    score=item.score,
                    memory_type=MemoryType.SEMANTIC,
                    trace_metadata={
                        "request_id": context.request_id,
                        "trace_id": item.trace_id or context.trace_id,
                        "task_id": item.task_id,
                        "tenant_id": context.tenant_id,
                        "user_id": context.user_id,
                        "agent_id": context.agent_id,
                    },
                )
            )
        return candidates


class ContextCatalogRecallSource:
    """Expose Resource/Skill/Session directory hits to the main context flow."""

    def __init__(
        self,
        search: ContextSearchPort,
        *,
        name: str = "context_catalog",
    ) -> None:
        self._search = search
        self._name = name

    @property
    def name(self) -> str:
        return self._name

    async def recall(
        self,
        request: ContextRequest,
        context: RequestContext,
    ) -> RecallSourceResult:
        if not request.query:
            return RecallSourceResult()
        queries = [
            ContextSearchQuery(
                query=request.query,
                scope=context.scope,
                trace_id=f"{context.trace_id}:catalog:agent",
                root_uri=agent_scope_uri(context.scope),
                kinds=[ContextItemKind.RESOURCE, ContextItemKind.SKILL],
                layers=_CATALOG_RECALL_LAYERS,
                limit=request.max_candidates,
            )
        ]
        if context.session_id is not None:
            queries.append(
                ContextSearchQuery(
                    query=request.query,
                    scope=context.scope,
                    trace_id=f"{context.trace_id}:catalog:session",
                    root_uri=scope_uri(context.scope),
                    kinds=[ContextItemKind.SESSION],
                    layers=_CATALOG_RECALL_LAYERS,
                    limit=request.max_candidates,
                )
            )
        results = [await self._search.search(query) for query in queries]
        candidates: list[RecallCandidate] = []
        missing_sources: list[str] = []
        reasons: dict[str, str] = {}
        for result in results:
            candidates.extend(
                RecallCandidate(
                    memory_id=str(hit.uri),
                    context_uri=hit.uri,
                    context_kind=hit.kind,
                    context_layer=hit.layer,
                    scope=context.scope,
                    content=hit.text,
                    content_ref=hit.content_ref,
                    source=f"{self._name}:{hit.source}",
                    score=hit.score,
                    memory_type=MemoryType.SEMANTIC,
                    trace_metadata={
                        "source_revision": hit.source_revision,
                        "catalog_backend": result.backend,
                    },
                )
                for hit in result.hits
            )
            for source in result.missing_sources:
                if source not in missing_sources:
                    missing_sources.append(source)
                reasons[source] = str(
                    result.metadata.get(f"{source}_error") or "source degraded"
                )
        return RecallSourceResult(
            candidates=candidates,
            complete=all(result.complete for result in results),
            missing_sources=missing_sources,
            degraded_reasons=reasons,
        )


_CATALOG_RECALL_LAYERS = [
    ContextLayer.ABSTRACT,
    ContextLayer.OVERVIEW,
    ContextLayer.DETAIL,
]


def _candidate_from_recalled(
    item: RecalledMemory,
    source: str,
    context: RequestContext,
) -> RecallCandidate:
    memory = item.memory
    content_ref = memory.metadata.get("content_ref")
    scope = Scope(
        tenant_id=memory.tenant_id,
        user_id=memory.user_id,
        agent_id=memory.agent_id,
        session_id=memory.session_id,
        task_id=memory.task_id,
    )
    return RecallCandidate(
        memory_id=memory.id,
        context_uri=memory_uri(memory),
        context_kind=ContextItemKind.MEMORY,
        scope=scope,
        memory=memory,
        content=memory.content,
        content_ref=str(content_ref) if content_ref is not None else None,
        source=source,
        score=item.score,
        memory_type=memory.type,
        created_at=memory.created_at,
        last_access=memory.last_accessed_at,
        trace_metadata={
            "request_id": context.request_id,
            "trace_id": context.trace_id,
            "tenant_id": memory.tenant_id,
            "user_id": memory.user_id,
            "agent_id": memory.agent_id,
        },
    )
