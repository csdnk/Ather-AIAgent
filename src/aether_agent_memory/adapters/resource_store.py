from __future__ import annotations

import asyncio
from hashlib import sha256
from typing import Any

from aether_agent_memory.context_store.errors import ContextRevisionConflictError
from aether_agent_memory.core.scope import Scope
from aether_agent_memory.resource.models import ResourceRecord


class InMemoryResourceStore:
    """Reference Resource adapter for tests and local development."""

    def __init__(self, resources: list[ResourceRecord] | None = None) -> None:
        self._resources: dict[tuple[str | None, str | None, str | None, str], ResourceRecord] = {
            (
                resource.scope.tenant_id,
                resource.scope.user_id,
                resource.scope.agent_id,
                resource.resource_id,
            ): resource.model_copy(deep=True)
            for resource in resources or []
        }
        self._revisions = {
            key: resource.revision for key, resource in self._resources.items()
        }
        self._lock = asyncio.Lock()

    async def get(self, scope: Scope, resource_id: str) -> ResourceRecord | None:
        key = _key(scope, resource_id)
        async with self._lock:
            resource = self._resources.get(key)
            return resource.model_copy(deep=True) if resource is not None else None

    async def reserve_revision(
        self, scope: Scope, resource_id: str, *, minimum: int = 0
    ) -> int:
        key = _key(scope, resource_id)
        async with self._lock:
            current = max(self._revisions.get(key, 0), minimum)
            self._revisions[key] = current + 1
            return current + 1

    async def upsert(self, resource: ResourceRecord) -> None:
        async with self._lock:
            key = _key(resource.scope, resource.resource_id)
            existing = self._resources.get(key)
            if existing is not None and existing.revision >= resource.revision:
                raise ContextRevisionConflictError(
                    f"resource revision {resource.revision} does not follow "
                    f"stored revision {existing.revision}"
                )
            self._resources[key] = resource.model_copy(deep=True)
            self._revisions[key] = max(self._revisions.get(key, 0), resource.revision)

    async def delete(self, scope: Scope, resource_id: str) -> bool:
        async with self._lock:
            return self._resources.pop(_key(scope, resource_id), None) is not None

    async def list_scoped(
        self,
        *,
        tenant_id: str | None,
        user_id: str | None,
        agent_id: str | None,
        category: str | None = None,
    ) -> list[ResourceRecord]:
        async with self._lock:
            return [
                resource.model_copy(deep=True)
                for (tenant, user, agent, _), resource in self._resources.items()
                if tenant == tenant_id
                and user == user_id
                and agent == agent_id
                and (category is None or resource.category == category)
            ]


class RedisResourceStore:
    """Durable Resource descriptor store with a scope-local index."""

    def __init__(
        self,
        redis_url: str,
        *,
        namespace: str = "aether:p3:resource",
        ttl_seconds: int | None = None,
        operation_timeout_seconds: float = 1.0,
        client: Any | None = None,
    ) -> None:
        if (
            ttl_seconds is not None and ttl_seconds < 1
        ) or operation_timeout_seconds <= 0:
            raise ValueError("resource TTL and operation timeout must be positive")
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

    async def get(self, scope: Scope, resource_id: str) -> ResourceRecord | None:
        payload = await self._redis.get(self._item_key(scope, resource_id))
        return ResourceRecord.model_validate_json(payload) if payload else None

    async def reserve_revision(
        self, scope: Scope, resource_id: str, *, minimum: int = 0
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
            self._revision_key(scope, resource_id),
            minimum,
        )
        return int(value)

    async def upsert(self, resource: ResourceRecord) -> None:
        item_key = self._item_key(resource.scope, resource.resource_id)
        index_key = self._index_key(resource.scope)
        from redis.exceptions import WatchError

        for _ in range(3):
            async with self._redis.pipeline(transaction=True) as pipeline:
                try:
                    await pipeline.watch(item_key)
                    payload = await pipeline.get(item_key)
                    if payload:
                        existing = ResourceRecord.model_validate_json(payload)
                        if existing.revision >= resource.revision:
                            await pipeline.unwatch()
                            raise ContextRevisionConflictError(
                                f"resource revision {resource.revision} does not follow "
                                f"stored revision {existing.revision}"
                            )
                    pipeline.multi()
                    pipeline.set(item_key, resource.model_dump_json(), ex=self._ttl_seconds)
                    pipeline.sadd(index_key, item_key)
                    if self._ttl_seconds is None:
                        pipeline.persist(index_key)
                    else:
                        pipeline.expire(index_key, self._ttl_seconds)
                    await pipeline.execute()
                    return
                except WatchError:
                    continue
        raise ContextRevisionConflictError("resource changed during persistence")

    async def delete(self, scope: Scope, resource_id: str) -> bool:
        item_key = self._item_key(scope, resource_id)
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
        category: str | None = None,
    ) -> list[ResourceRecord]:
        index_key = self._index_key_values(tenant_id, user_id, agent_id)
        item_keys = sorted(await self._redis.smembers(index_key))
        if not item_keys:
            return []
        payloads = await self._redis.mget(item_keys)
        resources = [
            ResourceRecord.model_validate_json(payload)
            for payload in payloads
            if payload
        ]
        return [
            resource
            for resource in resources
            if category is None or resource.category == category
        ]

    async def close(self) -> None:
        await self._redis.aclose()

    def _item_key(self, scope: Scope, resource_id: str) -> str:
        return f"{self._namespace}:item:{_agent_scope_key(scope)}:{_id_key(resource_id)}"

    def _index_key(self, scope: Scope) -> str:
        return self._index_key_values(scope.tenant_id, scope.user_id, scope.agent_id)

    def _revision_key(self, scope: Scope, resource_id: str) -> str:
        return f"{self._namespace}:revision:{_agent_scope_key(scope)}:{_id_key(resource_id)}"

    def _index_key_values(
        self,
        tenant_id: str | None,
        user_id: str | None,
        agent_id: str | None,
    ) -> str:
        return f"{self._namespace}:index:{_agent_scope_key_values(tenant_id, user_id, agent_id)}"


def _key(scope: Scope, resource_id: str) -> tuple[str | None, str | None, str | None, str]:
    return (
        scope.tenant_id,
        scope.user_id,
        scope.agent_id,
        resource_id,
    )


def _agent_scope_key(scope: Scope) -> str:
    return _agent_scope_key_values(scope.tenant_id, scope.user_id, scope.agent_id)


def _agent_scope_key_values(
    tenant_id: str | None,
    user_id: str | None,
    agent_id: str | None,
) -> str:
    scope = "\x1f".join(
        value or "_" for value in (tenant_id, user_id, agent_id)
    )
    return sha256(scope.encode()).hexdigest()


def _id_key(value: str) -> str:
    return sha256(value.encode()).hexdigest()
