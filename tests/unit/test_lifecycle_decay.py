from datetime import UTC, datetime, timedelta

import pytest

from aether_agent_memory.core.enums import MemoryType
from aether_agent_memory.core.memory import Memory
from aether_agent_memory.lifecycle.decay import ebb_decay_weight, is_ttl_expired


@pytest.mark.unit
def test_is_ttl_expired_no_expiry() -> None:
    m = Memory(type=MemoryType.WORKING, session_id="s1", agent_id="a1", content="x")
    assert is_ttl_expired(m) is False


@pytest.mark.unit
def test_is_ttl_expired_past() -> None:
    m = Memory(
        type=MemoryType.WORKING,
        session_id="s1",
        agent_id="a1",
        content="x",
        expires_at=datetime.now(UTC) - timedelta(hours=1),
    )
    assert is_ttl_expired(m) is True


@pytest.mark.unit
def test_is_ttl_expired_future() -> None:
    m = Memory(
        type=MemoryType.WORKING,
        session_id="s1",
        agent_id="a1",
        content="x",
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    assert is_ttl_expired(m) is False


@pytest.mark.unit
def test_ebb_decay_weight_fresh() -> None:
    now = datetime.now(UTC)
    m = Memory(
        type=MemoryType.EPISODIC,
        session_id="s1",
        agent_id="a1",
        content="x",
        created_at=now,
    )
    w = ebb_decay_weight(m, now=now)
    assert w == pytest.approx(1.0)


@pytest.mark.unit
def test_ebb_decay_weight_at_half_life() -> None:
    now = datetime.now(UTC)
    m = Memory(
        type=MemoryType.EPISODIC,
        session_id="s1",
        agent_id="a1",
        content="x",
        created_at=now - timedelta(hours=168),
    )
    w = ebb_decay_weight(m, now=now, half_life_hours=168.0)
    assert w == pytest.approx(0.5, abs=0.01)


@pytest.mark.unit
def test_ebb_decay_weight_decreases_monotonically() -> None:
    now = datetime.now(UTC)
    base = Memory(
        type=MemoryType.EPISODIC,
        session_id="s1",
        agent_id="a1",
        content="x",
        created_at=now - timedelta(hours=1),
    )
    older = Memory(
        type=MemoryType.EPISODIC,
        session_id="s1",
        agent_id="a1",
        content="x",
        created_at=now - timedelta(hours=100),
    )
    assert ebb_decay_weight(base, now=now) > ebb_decay_weight(older, now=now)


@pytest.mark.unit
def test_ebb_decay_weight_floor_zero() -> None:
    now = datetime.now(UTC)
    m = Memory(
        type=MemoryType.EPISODIC,
        session_id="s1",
        agent_id="a1",
        content="x",
        created_at=now - timedelta(days=36500),
    )
    assert ebb_decay_weight(m, now=now) == 0.0
