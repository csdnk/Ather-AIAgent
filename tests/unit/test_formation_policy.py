from __future__ import annotations

import pytest

from aether_agent_memory.b2 import MemoryEvent, MemoryEventType
from aether_agent_memory.core.enums import MemoryType, SourceType
from aether_agent_memory.memory.formation import (
    DefaultMemoryFormationPolicy,
    FormationAction,
)


@pytest.mark.unit
def test_default_formation_policy_preserves_user_memory_semantic_rule() -> None:
    event = MemoryEvent(
        event_type=MemoryEventType.USER_MEMORY,
        session_id="s",
        agent_id="a",
        content="remember this",
    )

    decision = DefaultMemoryFormationPolicy().decide(event)

    assert decision.action == FormationAction.CREATE_MEMORY
    assert decision.memory_type == MemoryType.SEMANTIC
    assert decision.source == SourceType.USER


@pytest.mark.unit
def test_default_formation_policy_keeps_other_events_working() -> None:
    event = MemoryEvent(
        event_type=MemoryEventType.AFTER_TURN,
        session_id="s",
        agent_id="a",
        content="conversation observation",
        source=SourceType.AGENT,
    )

    decision = DefaultMemoryFormationPolicy().decide(event)

    assert decision.action == FormationAction.CREATE_MEMORY
    assert decision.memory_type == MemoryType.WORKING
    assert decision.source == SourceType.AGENT


@pytest.mark.unit
def test_default_formation_policy_maps_provider_events_to_provider_sources() -> None:
    rag = MemoryEvent(
        event_type=MemoryEventType.RAG_RESULT,
        session_id="s",
        agent_id="a",
        content="retrieved evidence",
    )
    tool = MemoryEvent(
        event_type=MemoryEventType.TOOL_RESULT,
        session_id="s",
        agent_id="a",
        content="tool output",
    )

    policy = DefaultMemoryFormationPolicy()

    assert policy.decide(rag).source == SourceType.RAG
    assert policy.decide(tool).source == SourceType.TOOL

