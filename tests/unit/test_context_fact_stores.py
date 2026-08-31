from __future__ import annotations

from typing import Any

import pytest

from aether_agent_memory.adapters.resource_store import (
    InMemoryResourceStore,
    RedisResourceStore,
)
from aether_agent_memory.adapters.skill_store import InMemorySkillStore, RedisSkillStore
from aether_agent_memory.context_store.errors import ContextRevisionConflictError
from aether_agent_memory.core.scope import Scope
from aether_agent_memory.resource.models import ResourceRecord
from aether_agent_memory.skill.models import SkillRecord


class _Pipeline:
    def __init__(self, redis: _Redis) -> None:
        self._redis = redis
        self.calls: list[tuple[Any, ...]] = []

    async def __aenter__(self) -> _Pipeline:
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None

    async def watch(self, _key: str) -> None:
        return None

    async def get(self, key: str) -> str | None:
        return self._redis.data.get(key)

    async def unwatch(self) -> None:
        return None

    def multi(self) -> None:
        return None

    def set(self, key: str, value: str, *, ex: int | None) -> _Pipeline:
        self.calls.append(("set", key, value, ex))
        return self

    def sadd(self, key: str, value: str) -> _Pipeline:
        self.calls.append(("sadd", key, value))
        return self

    def persist(self, key: str) -> _Pipeline:
        self.calls.append(("persist", key))
        return self

    def expire(self, key: str, ttl: int) -> _Pipeline:
        self.calls.append(("expire", key, ttl))
        return self

    async def execute(self) -> list[int]:
        for call in self.calls:
            if call[0] == "set":
                self._redis.data[call[1]] = call[2]
        return [1 for _ in self.calls]


class _Redis:
    def __init__(self) -> None:
        self.last_pipeline: _Pipeline | None = None
        self.data: dict[str, str] = {}
        self.revisions: dict[str, int] = {}

    def pipeline(self, *, transaction: bool) -> _Pipeline:
        assert transaction is True
        self.last_pipeline = _Pipeline(self)
        return self.last_pipeline

    async def eval(
        self, _script: str, _key_count: int, key: str, minimum: int
    ) -> int:
        revision = max(self.revisions.get(key, 0), int(minimum)) + 1
        self.revisions[key] = revision
        return revision


def _scope() -> Scope:
    return Scope(tenant_id="tenant-1", user_id="user-1", agent_id="agent-1")


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["resource", "skill"])
async def test_context_facts_are_persistent_by_default(kind: str) -> None:
    redis = _Redis()
    if kind == "resource":
        store = RedisResourceStore("redis://unused", client=redis)
        await store.upsert(ResourceRecord(resource_id="r1", name="R1", scope=_scope()))
    else:
        store = RedisSkillStore("redis://unused", client=redis)
        await store.upsert(SkillRecord(skill_id="s1", name="S1", scope=_scope()))

    assert redis.last_pipeline is not None
    calls = redis.last_pipeline.calls
    assert calls[0][0] == "set" and calls[0][3] is None
    assert any(call[0] == "persist" for call in calls)
    assert not any(call[0] == "expire" for call in calls)


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["resource", "skill"])
async def test_context_fact_ttl_is_explicit_and_applies_to_scope_index(kind: str) -> None:
    redis = _Redis()
    if kind == "resource":
        store = RedisResourceStore("redis://unused", client=redis, ttl_seconds=60)
        await store.upsert(ResourceRecord(resource_id="r1", name="R1", scope=_scope()))
    else:
        store = RedisSkillStore("redis://unused", client=redis, ttl_seconds=60)
        await store.upsert(SkillRecord(skill_id="s1", name="S1", scope=_scope()))

    assert redis.last_pipeline is not None
    calls = redis.last_pipeline.calls
    assert calls[0][0] == "set" and calls[0][3] == 60
    assert any(call[0] == "expire" and call[2] == 60 for call in calls)
    assert not any(call[0] == "persist" for call in calls)


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["resource", "skill"])
async def test_context_revision_survives_delete_and_recreate(kind: str) -> None:
    scope = _scope()
    if kind == "resource":
        store = InMemoryResourceStore()
        first = await store.reserve_revision(scope, "item-1")
        await store.upsert(
            ResourceRecord(
                resource_id="item-1", name="First", scope=scope, revision=first
            )
        )
        assert await store.delete(scope, "item-1") is True
        second = await store.reserve_revision(scope, "item-1")
    else:
        store = InMemorySkillStore()
        first = await store.reserve_revision(scope, "item-1")
        await store.upsert(
            SkillRecord(skill_id="item-1", name="First", scope=scope, revision=first)
        )
        assert await store.delete(scope, "item-1") is True
        second = await store.reserve_revision(scope, "item-1")

    assert first == 1
    assert second == 2


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["resource", "skill"])
async def test_context_store_rejects_stale_revision_overwrite(kind: str) -> None:
    scope = _scope()
    if kind == "resource":
        store = InMemoryResourceStore(
            [ResourceRecord(resource_id="item-1", name="New", scope=scope, revision=2)]
        )
        stale = ResourceRecord(
            resource_id="item-1", name="Old", scope=scope, revision=1
        )
    else:
        store = InMemorySkillStore(
            [SkillRecord(skill_id="item-1", name="New", scope=scope, revision=2)]
        )
        stale = SkillRecord(skill_id="item-1", name="Old", scope=scope, revision=1)

    with pytest.raises(ContextRevisionConflictError):
        await store.upsert(stale)


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["resource", "skill"])
async def test_redis_revision_allocator_is_monotonic(kind: str) -> None:
    redis = _Redis()
    scope = _scope()
    if kind == "resource":
        store = RedisResourceStore("redis://unused", client=redis)
    else:
        store = RedisSkillStore("redis://unused", client=redis)

    first = await store.reserve_revision(scope, "item-1", minimum=4)
    second = await store.reserve_revision(scope, "item-1")

    assert (first, second) == (5, 6)
