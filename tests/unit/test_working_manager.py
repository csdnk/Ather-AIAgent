from datetime import UTC, datetime, timedelta

import pytest

from aether_agent_memory.context.models import ContextRequest
from aether_agent_memory.core.enums import MemoryState, MemoryType
from aether_agent_memory.core.memory import Memory
from aether_agent_memory.working.manager import MockWorkingMemoryManager


@pytest.mark.unit
async def test_write_and_get() -> None:
    mgr = MockWorkingMemoryManager()
    m = Memory(type=MemoryType.WORKING, session_id="s1", agent_id="a1", content="hello")
    written = await mgr.write(m)
    fetched = await mgr.get(m.id)
    assert fetched is not None
    assert fetched.content == "hello"
    assert fetched.access_count == 1
    assert written.id == m.id


@pytest.mark.unit
async def test_get_missing_returns_none() -> None:
    mgr = MockWorkingMemoryManager()
    assert await mgr.get("nope") is None


@pytest.mark.unit
async def test_query_by_session() -> None:
    mgr = MockWorkingMemoryManager()
    await mgr.write(Memory(type=MemoryType.WORKING, session_id="s1", agent_id="a1", content="a"))
    await mgr.write(Memory(type=MemoryType.WORKING, session_id="s1", agent_id="a1", content="b"))
    await mgr.write(Memory(type=MemoryType.WORKING, session_id="s2", agent_id="a1", content="c"))
    results = await mgr.query(session_id="s1")
    assert len(results) == 2
    assert all(r.session_id == "s1" for r in results)


@pytest.mark.unit
async def test_query_by_agent_and_user() -> None:
    mgr = MockWorkingMemoryManager()
    await mgr.write(
        Memory(
            type=MemoryType.WORKING,
            session_id="s1",
            agent_id="a1",
            user_id="u1",
            content="x",
        )
    )
    await mgr.write(
        Memory(
            type=MemoryType.WORKING,
            session_id="s2",
            agent_id="a2",
            user_id="u1",
            content="y",
        )
    )
    assert len(await mgr.query(agent_id="a1")) == 1
    assert len(await mgr.query(user_id="u1")) == 2
    assert len(await mgr.query(agent_id="a1", user_id="u1")) == 1


@pytest.mark.unit
async def test_write_applies_default_ttl() -> None:
    mgr = MockWorkingMemoryManager(default_ttl=timedelta(seconds=60))
    m = Memory(type=MemoryType.WORKING, session_id="s1", agent_id="a1", content="x")
    await mgr.write(m)
    assert m.expires_at is not None
    assert not m.is_expired()


@pytest.mark.unit
async def test_expire_stale_marks_expired() -> None:
    mgr = MockWorkingMemoryManager()
    past = datetime.now(UTC) - timedelta(hours=1)
    m = Memory(
        type=MemoryType.WORKING,
        session_id="s1",
        agent_id="a1",
        content="x",
        expires_at=past,
    )
    await mgr.write(m)
    count = await mgr.expire_stale()
    assert count == 1
    expired = await mgr.get(m.id)
    assert expired is not None
    assert expired.state == MemoryState.EXPIRED


@pytest.mark.unit
async def test_expire_stale_skips_already_expired() -> None:
    mgr = MockWorkingMemoryManager()
    past = datetime.now(UTC) - timedelta(hours=1)
    m = Memory(
        type=MemoryType.WORKING,
        session_id="s1",
        agent_id="a1",
        content="x",
        expires_at=past,
        state=MemoryState.EXPIRED,
    )
    await mgr.write(m)
    count = await mgr.expire_stale()
    assert count == 0


@pytest.mark.unit
async def test_update_state_active_to_archived() -> None:
    mgr = MockWorkingMemoryManager()
    m = Memory(type=MemoryType.WORKING, session_id="s1", agent_id="a1", content="x")
    await mgr.write(m)
    updated = await mgr.update_state(m.id, MemoryState.ARCHIVED)
    assert updated.state == MemoryState.ARCHIVED


@pytest.mark.unit
async def test_update_state_illegal_raises() -> None:
    from aether_agent_memory.core.exceptions import InvalidStateTransitionError

    mgr = MockWorkingMemoryManager()
    m = Memory(
        type=MemoryType.WORKING,
        session_id="s1",
        agent_id="a1",
        content="x",
        state=MemoryState.EXPIRED,
    )
    await mgr.write(m)
    with pytest.raises(InvalidStateTransitionError):
        await mgr.update_state(m.id, MemoryState.ACTIVE)


@pytest.mark.unit
async def test_update_state_missing_raises() -> None:
    from aether_agent_memory.core.exceptions import MemoryNotFoundError

    mgr = MockWorkingMemoryManager()
    with pytest.raises(MemoryNotFoundError):
        await mgr.update_state("ghost", MemoryState.ARCHIVED)


@pytest.mark.unit
async def test_delete() -> None:
    mgr = MockWorkingMemoryManager()
    m = Memory(type=MemoryType.WORKING, session_id="s1", agent_id="a1", content="x")
    await mgr.write(m)
    assert await mgr.delete(m.id) is True
    assert await mgr.get(m.id) is None
    assert await mgr.delete(m.id) is False


@pytest.mark.unit
async def test_recall_orders_by_recency() -> None:
    mgr = MockWorkingMemoryManager()
    old = Memory(
        type=MemoryType.WORKING,
        session_id="s1",
        agent_id="a1",
        content="old",
        created_at=datetime.now(UTC) - timedelta(hours=2),
    )
    recent = Memory(
        type=MemoryType.WORKING,
        session_id="s1",
        agent_id="a1",
        content="recent",
        created_at=datetime.now(UTC),
    )
    await mgr.write(old)
    await mgr.write(recent)
    req = ContextRequest(session_id="s1", agent_id="a1", query="q")
    recalled = await mgr.recall(req)
    assert len(recalled) == 2
    assert recalled[0].memory.content == "recent"
    assert recalled[0].score >= recalled[1].score


@pytest.mark.unit
async def test_recall_filters_by_session() -> None:
    mgr = MockWorkingMemoryManager()
    await mgr.write(Memory(type=MemoryType.WORKING, session_id="s1", agent_id="a1", content="a"))
    await mgr.write(Memory(type=MemoryType.WORKING, session_id="s2", agent_id="a1", content="b"))
    req = ContextRequest(session_id="s1", agent_id="a1", query="q")
    recalled = await mgr.recall(req)
    assert len(recalled) == 1
    assert recalled[0].memory.session_id == "s1"


@pytest.mark.unit
async def test_recall_excludes_expired() -> None:
    mgr = MockWorkingMemoryManager()
    m = Memory(
        type=MemoryType.WORKING,
        session_id="s1",
        agent_id="a1",
        content="x",
        state=MemoryState.EXPIRED,
    )
    await mgr.write(m)
    req = ContextRequest(session_id="s1", agent_id="a1", query="q")
    assert await mgr.recall(req) == []
