from __future__ import annotations

from datetime import UTC, datetime

import pytest

from aether_agent_memory.context.models import ContextPack, ContextRequest
from aether_agent_memory.context_store.mapping import resource_uri
from aether_agent_memory.core.enums import MemoryType
from aether_agent_memory.core.memory import Memory
from aether_agent_memory.core.scope import Scope
from aether_agent_memory.memory.retrieval.models import RecallCandidate
from aether_agent_memory.memory.retrieval.pipeline import RecallPipeline
from aether_agent_memory.memory.retrieval.service import MemoryRetrievalService
from aether_agent_memory.runtime.request_context import RequestContext


class _CrmRecallSource:
    @property
    def name(self) -> str:
        return "crm"

    async def recall(
        self,
        request: ContextRequest,
        context: RequestContext,
    ) -> list[RecallCandidate]:
        return [
            RecallCandidate(
                memory_id="crm-1",
                content="customer prefers weekly delivery",
                content_ref="crm://customer/1",
                source=self.name,
                score=0.87,
            )
        ]


@pytest.mark.asyncio
async def test_recall_pipeline_can_add_source_without_runtime_flow_change() -> None:
    request = ContextRequest(session_id="s", agent_id="a", query="delivery")
    pack = ContextPack(
        request=request,
        memories=[],
        total_tokens=0,
        budget_tokens=128,
        recall_scores={},
        assembled_text="",
        built_at=datetime.now(UTC),
    )

    await MemoryRetrievalService([_CrmRecallSource()]).augment_context(
        pack,
        request,
        RequestContext.from_values(session_id="s", agent_id="a"),
    )

    assert [memory.id for memory in pack.memories] == ["crm-1"]
    assert pack.recall_scores["crm-1"] == pytest.approx(0.87)
    assert pack.evidence_refs == ["crm://customer/1"]
    assert "crm" in pack.assembled_text


def test_recall_pipeline_exposes_normalize_filter_rank_stages() -> None:
    class _Normalizer:
        def normalize(self, candidate, request, context):
            return candidate.model_copy(update={"score": candidate.score / 2})

    class _Policy:
        def allow(self, candidate, request, context):
            return candidate.score >= 0.4

    class _Ranker:
        def rank(self, candidates):
            return list(reversed(candidates))

    request = ContextRequest(session_id="s", agent_id="a", query="query", max_candidates=1)
    context = RequestContext.from_values(session_id="s", agent_id="a")
    candidates = [
        RecallCandidate(memory_id="low", source="test", score=0.2),
        RecallCandidate(memory_id="high", source="test", score=0.9),
    ]

    result = RecallPipeline(
        normalizer=_Normalizer(),
        policy=_Policy(),
        ranker=_Ranker(),
    ).process(candidates, request, context)

    assert [candidate.memory_id for candidate in result] == ["high"]
    assert result[0].score == pytest.approx(0.45)


def test_default_recall_policy_rejects_foreign_and_unscoped_candidates() -> None:
    request = ContextRequest(
        tenant_id="tenant-a",
        user_id="user-a",
        agent_id="agent-a",
        session_id="session-a",
        query="private",
    )
    context = RequestContext.from_values(
        tenant_id="tenant-a",
        user_id="user-a",
        agent_id="agent-a",
        session_id="session-a",
    )
    candidates = [
        RecallCandidate(
            memory_id="foreign",
            source="memory",
            score=1.0,
            memory=Memory(
                id="foreign",
                type=MemoryType.SEMANTIC,
                tenant_id="tenant-b",
                user_id="user-a",
                agent_id="agent-a",
                session_id="session-a",
                content="must not escape",
            ),
        ),
        RecallCandidate(memory_id="unscoped", source="crm", score=0.9),
        RecallCandidate(
            memory_id="allowed",
            source="crm",
            score=0.8,
            scope=Scope(
                tenant_id="tenant-a",
                user_id="user-a",
                agent_id="agent-a",
            ),
        ),
    ]

    result = RecallPipeline().process(candidates, request, context)

    assert [candidate.memory_id for candidate in result] == ["allowed"]


def test_default_recall_policy_rejects_working_memory_from_other_session() -> None:
    request = ContextRequest(
        tenant_id="tenant-a",
        user_id="user-a",
        agent_id="agent-a",
        session_id="session-a",
        query="private",
    )
    context = RequestContext.from_values(
        tenant_id="tenant-a",
        user_id="user-a",
        agent_id="agent-a",
        session_id="session-a",
    )
    candidate = RecallCandidate(
        memory_id="working-b",
        source="working",
        memory=Memory(
            id="working-b",
            type=MemoryType.WORKING,
            tenant_id="tenant-a",
            user_id="user-a",
            agent_id="agent-a",
            session_id="session-b",
            content="other session",
        ),
    )

    assert RecallPipeline().process([candidate], request, context) == []


def test_recall_fusion_keeps_highest_score_for_one_canonical_uri() -> None:
    request = ContextRequest(
        tenant_id="tenant-a",
        user_id="user-a",
        agent_id="agent-a",
        session_id="session-a",
        query="document",
    )
    context = RequestContext.from_values(
        tenant_id="tenant-a",
        user_id="user-a",
        agent_id="agent-a",
        session_id="session-a",
    )
    uri = resource_uri(context.scope, "document-1")
    candidates = [
        RecallCandidate(
            memory_id="document-1",
            context_uri=uri,
            scope=context.scope,
            source="p2",
            score=0.7,
        ),
        RecallCandidate(
            memory_id=str(uri),
            context_uri=uri,
            scope=context.scope,
            source="context_catalog",
            score=0.9,
        ),
    ]

    result = RecallPipeline().process(candidates, request, context)

    assert len(result) == 1
    assert result[0].source == "context_catalog"
    assert result[0].score == pytest.approx(0.9)
