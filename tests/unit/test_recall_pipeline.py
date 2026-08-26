from __future__ import annotations

from datetime import UTC, datetime

import pytest

from aether_agent_memory.context.models import ContextPack, ContextRequest
from aether_agent_memory.memory.retrieval.models import RecallCandidate
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
