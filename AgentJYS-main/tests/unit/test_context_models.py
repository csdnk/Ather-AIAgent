from datetime import UTC, datetime

import pytest

from aether_agent_memory.context.models import ContextPack, ContextRequest
from aether_agent_memory.core.enums import MemoryType
from aether_agent_memory.core.memory import Memory


@pytest.mark.unit
def test_context_request_defaults() -> None:
    req = ContextRequest(session_id="s1", agent_id="a1", query="what is memory?")
    assert req.user_id is None
    assert req.memory_types == [MemoryType.WORKING, MemoryType.EPISODIC, MemoryType.SEMANTIC]
    assert req.max_tokens == 4096
    assert req.max_candidates == 100
    assert req.filters == {}


@pytest.mark.unit
def test_context_request_custom_types() -> None:
    req = ContextRequest(
        session_id="s1",
        agent_id="a1",
        query="q",
        memory_types=[MemoryType.EPISODIC],
        max_tokens=2048,
        max_candidates=50,
        filters={"tags": ["important"]},
    )
    assert req.memory_types == [MemoryType.EPISODIC]
    assert req.max_tokens == 2048
    assert req.filters["tags"] == ["important"]


@pytest.mark.unit
def test_context_pack_assembly() -> None:
    req = ContextRequest(session_id="s1", agent_id="a1", query="q")
    m1 = Memory(type=MemoryType.WORKING, session_id="s1", agent_id="a1", content="c1")
    m2 = Memory(type=MemoryType.EPISODIC, session_id="s1", agent_id="a1", content="c2")
    pack = ContextPack(
        request=req,
        memories=[m1, m2],
        total_tokens=120,
        budget_tokens=4096,
        recall_scores={m1.id: 0.9, m2.id: 0.5},
        assembled_text="[1] c1\n[2] c2",
        built_at=datetime.now(UTC),
    )
    assert len(pack.memories) == 2
    assert pack.recall_scores[m1.id] == 0.9
    assert pack.budget_tokens == 4096
