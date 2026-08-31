from __future__ import annotations

import asyncio
from hashlib import sha256
from typing import Any

from aether_agent_memory.context_store.errors import ContextRevisionConflictError
from aether_agent_memory.core.scope import Scope
from aether_agent_memory.skill.models import SkillRecord


class InMemorySkillStore:
    """Small reference adapter for built-in/demo skills.

    A persistent enterprise Skill registry is intentionally outside this
    milestone; the catalog contract is stable so one can be added later.
    """

    def __init__(self, skills: list[SkillRecord] | None = None) -> None:
        self._skills = {
            (
                skill.scope.tenant_id,
                skill.scope.user_id,
                skill.scope.agent_id,
                skill.skill_id,
            ): skill
            for skill in skills or []
        }
        self._revisions = {key: skill.revision for key, skill in self._skills.items()}
        self._lock = asyncio.Lock()

    async def reserve_revision(
        self, scope: Scope, skill_id: str, *, minimum: int = 0
    ) -> int:
        key = (scope.tenant_id, scope.user_id, scope.agent_id, skill_id)
        async with self._lock:
            current = max(self._revisions.get(key, 0), minimum)
            self._revisions[key] = current + 1
            return current + 1

    async def upsert(self, skill: SkillRecord) -> None:
        key = (
            skill.scope.tenant_id,
            skill.scope.user_id,
            skill.scope.agent_id,
            skill.skill_id,
        )
        async with self._lock:
            existing = self._skills.get(key)
            if existing is not None and existing.revision >= skill.revision:
                raise ContextRevisionConflictError(
                    f"skill revision {skill.revision} does not follow "
                    f"stored revision {existing.revision}"
                )
            self._skills[key] = skill.model_copy(deep=True)
            self._revisions[key] = max(self._revisions.get(key, 0), skill.revision)

    async def get(self, scope: Scope, skill_id: str) -> SkillRecord | None:
        async with self._lock:
            skill = self._skills.get(
                (scope.tenant_id, scope.user_id, scope.agent_id, skill_id)
            )
            return skill.model_copy(deep=True) if skill is not None else None

    async def delete(self, scope: Scope, skill_id: str) -> bool:
        key = (scope.tenant_id, scope.user_id, scope.agent_id, skill_id)
        async with self._lock:
            return self._skills.pop(key, None) is not None

    async def list_scoped(
        self,
        *,
        tenant_id: str | None,
        user_id: str | None,
        agent_id: str | None,
    ) -> list[SkillRecord]:
        async with self._lock:
            return [
                skill.model_copy(deep=True)
                for (tenant, user, agent, _), skill in self._skills.items()
                if tenant == tenant_id and user == user_id and agent == agent_id
            ]


class RedisSkillStore:
    """Durable agent Skill registry with a scope-local index."""

    def __init__(
        self,
        redis_url: str,
        *,
        namespace: str = "aether:p3:skill",
        ttl_seconds: int | None = None,
        operation_timeout_seconds: float = 1.0,
        client: Any | None = None,
    ) -> None:
        if (
            ttl_seconds is not None and ttl_seconds < 1
        ) or operation_timeout_seconds <= 0:
            raise ValueError("skill TTL and operation timeout must be positive")
        if client is None:
            from redis.asyncio import Redis

            client = Redis.from_url(
                redis_url,
                decode_responses=True,
                socket_connect_timeout=operation_timeout_seconds,
                socket_timeout=operation_timeout_seconds,
            )
        self._redis = client
        self._namespace = namespace.rstrip(":")
        self._ttl_seconds = ttl_seconds

    async def get(self, scope: Scope, skill_id: str) -> SkillRecord | None:
        payload = await self._redis.get(self._item_key(scope, skill_id))
        return SkillRecord.model_validate_json(payload) if payload else None

    async def reserve_revision(
        self, scope: Scope, skill_id: str, *, minimum: int = 0
    ) -> int:
        script = """
        local current = tonumber(redis.call('GET', KEYS[1]) or '0')
        local minimum = tonumber(ARGV[1])
        if current < minimum then
            redis.call('SET', KEYS[1], minimum)
        end
        return redis.call('INCR', KEYS[1])
        """
        value = await self._redis.eval(
            script,
            1,
            self._revision_key(scope, skill_id),
            minimum,
        )
        return int(value)

    async def upsert(self, skill: SkillRecord) -> None:
        item_key = self._item_key(skill.scope, skill.skill_id)
        index_key = self._index_key(skill.scope)
        from redis.exceptions import WatchError

        for _ in range(3):
            async with self._redis.pipeline(transaction=True) as pipeline:
                try:
                    await pipeline.watch(item_key)
                    payload = await pipeline.get(item_key)
                    if payload:
                        existing = SkillRecord.model_validate_json(payload)
                        if existing.revision >= skill.revision:
                            await pipeline.unwatch()
                            raise ContextRevisionConflictError(
                                f"skill revision {skill.revision} does not follow "
                                f"stored revision {existing.revision}"
                            )
                    pipeline.multi()
                    pipeline.set(item_key, skill.model_dump_json(), ex=self._ttl_seconds)
                    pipeline.sadd(index_key, item_key)
                    if self._ttl_seconds is None:
                        pipeline.persist(index_key)
                    else:
                        pipeline.expire(index_key, self._ttl_seconds)
                    await pipeline.execute()
                    return
                except WatchError:
                    continue
        raise ContextRevisionConflictError("skill changed during persistence")

    async def delete(self, scope: Scope, skill_id: str) -> bool:
        item_key = self._item_key(scope, skill_id)
        index_key = self._index_key(scope)
        async with self._redis.pipeline(transaction=True) as pipeline:
            pipeline.delete(item_key)
            pipeline.srem(index_key, item_key)
            results = await pipeline.execute()
        return bool(results[0])

    async def list_scoped(
        self,
        *,
        tenant_id: str | None,
        user_id: str | None,
        agent_id: str | None,
    ) -> list[SkillRecord]:
        index_key = self._index_key_values(tenant_id, user_id, agent_id)
        item_keys = sorted(await self._redis.smembers(index_key))
        if not item_keys:
            return []
        payloads = await self._redis.mget(item_keys)
        return [
            SkillRecord.model_validate_json(payload)
            for payload in payloads
            if payload
        ]

    async def close(self) -> None:
        await self._redis.aclose()

    def _item_key(self, scope: Scope, skill_id: str) -> str:
        return f"{self._namespace}:item:{_scope_key(scope)}:{_id_key(skill_id)}"

    def _index_key(self, scope: Scope) -> str:
        return self._index_key_values(scope.tenant_id, scope.user_id, scope.agent_id)

    def _revision_key(self, scope: Scope, skill_id: str) -> str:
        return f"{self._namespace}:revision:{_scope_key(scope)}:{_id_key(skill_id)}"

    def _index_key_values(
        self,
        tenant_id: str | None,
        user_id: str | None,
        agent_id: str | None,
    ) -> str:
        return f"{self._namespace}:index:{_scope_key_values(tenant_id, user_id, agent_id)}"


def _scope_key(scope: Scope) -> str:
    return _scope_key_values(scope.tenant_id, scope.user_id, scope.agent_id)


def _scope_key_values(
    tenant_id: str | None,
    user_id: str | None,
    agent_id: str | None,
) -> str:
    return sha256(
        "\x1f".join(value or "_" for value in (tenant_id, user_id, agent_id)).encode()
    ).hexdigest()


def _id_key(value: str) -> str:
    return sha256(value.encode()).hexdigest()
