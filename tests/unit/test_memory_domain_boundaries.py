from __future__ import annotations

from pathlib import Path

import pytest

from aether_agent_memory.core.enums import MemoryType
from aether_agent_memory.core.memory import (
    Memory,
    MemoryPlacement,
    MemoryProcessingState,
    MemoryProjection,
    MemoryValueState,
)

ROOT = Path(__file__).parents[2]
CORE_MEMORY = ROOT / "src" / "aether_agent_memory" / "core" / "memory.py"


@pytest.mark.unit
def test_core_memory_does_not_define_p2_specific_model() -> None:
    source = CORE_MEMORY.read_text("utf-8")

    assert "class P2Ref" not in source
    assert "from aether_agent_memory.p2" not in source
    assert "from aether_agent_memory.placement" not in source


@pytest.mark.unit
def test_memory_is_valid_without_infrastructure_dependencies() -> None:
    memory = Memory(
        type=MemoryType.WORKING,
        session_id="session-1",
        agent_id="agent-1",
        content="domain memory only",
    )

    assert isinstance(memory.projection, MemoryProjection)
    assert isinstance(memory.processing, MemoryProcessingState)
    assert isinstance(memory.value_state, MemoryValueState)
    assert memory.placement is None
    assert memory.embedding is None


@pytest.mark.unit
def test_legacy_infrastructure_payload_deserializes_to_split_models() -> None:
    memory = Memory.model_validate(
        {
            "type": "semantic",
            "session_id": "session-1",
            "agent_id": "agent-1",
            "content": "legacy stored memory",
            "embedding": [0.1, 0.2],
            "embedding_status": "succeeded",
            "vector_projection_status": "succeeded",
            "compression_artifact_id": "artifact-1",
            "compression_status": "ready",
            "compression_ratio": 5.0,
            "importance": 0.7,
            "access_count": 3,
            "p2_ref": {
                "segment_id": "object/p3-memory",
                "object_key": "b2/doc.txt",
                "tier": "L3",
            },
        }
    )

    assert memory.projection.embedding == [0.1, 0.2]
    assert memory.projection.embedding_status == "succeeded"
    assert memory.processing.compression_artifact_id == "artifact-1"
    assert memory.processing.compression_ratio == 5.0
    assert memory.value_state.importance == 0.7
    assert memory.value_state.access_count == 3
    assert isinstance(memory.placement, MemoryPlacement)
    assert memory.placement.provider == "p2"


@pytest.mark.unit
def test_legacy_aliases_remain_serialized_for_stored_data_compatibility() -> None:
    memory = Memory(
        type=MemoryType.SEMANTIC,
        session_id="session-1",
        agent_id="agent-1",
        content="new model",
        embedding_status="succeeded",
        placement={
            "provider": "p2",
            "segment_id": "segment-1",
            "object_key": "object-1",
        },
    )

    payload = memory.model_dump(mode="json")

    assert payload["embedding_status"] == "succeeded"
    assert payload["projection"]["embedding_status"] == "succeeded"
    assert payload["p2_ref"]["object_key"] == "object-1"

