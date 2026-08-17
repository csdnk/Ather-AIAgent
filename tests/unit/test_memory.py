from datetime import UTC, datetime, timedelta

import pytest

from aether_agent_memory.core.enums import MemoryState, MemoryType, SourceType, StorageTier
from aether_agent_memory.core.memory import Memory, P2Ref, RecalledMemory


@pytest.mark.unit
def test_memory_defaults() -> None:
    m = Memory(type=MemoryType.WORKING, session_id="s1", agent_id="a1", content="hello")
    assert m.id and len(m.id) == 32
    assert m.state == MemoryState.ACTIVE
    assert m.source == SourceType.USER
    assert m.access_count == 0
    assert m.importance == 1.0
    assert m.embedding is None
    assert m.metadata == {}
    assert m.tags == []
    assert m.p2_ref is None
    assert m.superseded_by is None
    assert m.created_at <= m.updated_at


@pytest.mark.unit
def test_p2_ref_defaults() -> None:
    ref = P2Ref(segment_id="seg-1", object_key="obj-1")
    assert ref.tier == StorageTier.L0_DRAM


@pytest.mark.unit
def test_memory_touch_updates_access() -> None:
    m = Memory(type=MemoryType.WORKING, session_id="s1", agent_id="a1", content="x")
    before = m.last_accessed_at
    m.touch()
    assert m.access_count == 1
    assert m.last_accessed_at is not None
    assert m.last_accessed_at >= (before or m.created_at)
    assert m.updated_at == m.last_accessed_at


@pytest.mark.unit
def test_is_expired_no_expiry() -> None:
    m = Memory(type=MemoryType.WORKING, session_id="s1", agent_id="a1", content="x")
    assert m.is_expired() is False


@pytest.mark.unit
def test_is_expired_with_future_expiry() -> None:
    m = Memory(
        type=MemoryType.WORKING,
        session_id="s1",
        agent_id="a1",
        content="x",
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    assert m.is_expired() is False


@pytest.mark.unit
def test_is_expired_with_past_expiry() -> None:
    m = Memory(
        type=MemoryType.WORKING,
        session_id="s1",
        agent_id="a1",
        content="x",
        expires_at=datetime.now(UTC) - timedelta(hours=1),
    )
    assert m.is_expired() is True


@pytest.mark.unit
def test_is_expired_with_explicit_now() -> None:
    expires = datetime(2026, 1, 1, tzinfo=UTC)
    m = Memory(
        type=MemoryType.EPISODIC,
        session_id="s1",
        agent_id="a1",
        content="x",
        expires_at=expires,
    )
    assert m.is_expired(now=datetime(2026, 6, 1, tzinfo=UTC)) is True
    assert m.is_expired(now=datetime(2025, 1, 1, tzinfo=UTC)) is False


@pytest.mark.unit
def test_recalled_memory_model() -> None:
    m = Memory(type=MemoryType.SEMANTIC, session_id="s1", agent_id="a1", content="x")
    r = RecalledMemory(memory=m, score=0.87)
    assert r.memory is m
    assert r.score == 0.87
