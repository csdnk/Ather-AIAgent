"""Projection state normalization tests (top-level vs metadata consistency)."""

from __future__ import annotations

from aether_agent_memory.core.enums import MemoryType
from aether_agent_memory.core.memory import Memory, normalize_projection_state


def _memory(**overrides: object) -> Memory:
    base: dict[str, object] = {
        "type": MemoryType.SEMANTIC,
        "session_id": "s",
        "agent_id": "a",
        "content": "sample content",
    }
    base.update(overrides)
    return Memory(**base)  # type: ignore[arg-type]


def test_embedding_status_synced_from_metadata() -> None:
    memory = _memory(metadata={"embedding_status": "succeeded"})
    normalized = normalize_projection_state(memory)
    assert normalized.embedding_status == "succeeded"
    assert normalized.metadata["embedding_status"] == "succeeded"


def test_compression_status_mirrored_to_metadata() -> None:
    memory = _memory(
        compression_status="failed", metadata={"compression_status": "pending"}
    )
    normalized = normalize_projection_state(memory)
    assert normalized.compression_status == "failed"
    assert normalized.metadata["compression_status"] == "failed"


def test_vector_projection_status_synced_from_metadata() -> None:
    memory = _memory(metadata={"vector_projection_status": "SUCCEEDED"})
    normalized = normalize_projection_state(memory)
    assert normalized.vector_projection_status == "SUCCEEDED"


def test_consistent_state_unchanged() -> None:
    memory = _memory(
        embedding_status="succeeded", metadata={"embedding_status": "succeeded"}
    )
    normalized = normalize_projection_state(memory)
    assert normalized.embedding_status == "succeeded"
    assert normalized.metadata["embedding_status"] == "succeeded"
    # 其他投影字段被镜像（默认值），顶层与 metadata 不再矛盾
    assert normalized.metadata["vector_projection_status"] == "pending"


def test_not_applicable_compression_mirrored() -> None:
    memory = _memory(metadata={})
    normalized = normalize_projection_state(memory)
    assert normalized.compression_status == "not_applicable"
    assert normalized.metadata["compression_status"] == "not_applicable"
