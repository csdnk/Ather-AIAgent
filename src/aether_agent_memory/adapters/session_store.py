from __future__ import annotations

import asyncio
from hashlib import sha256
from typing import Any

from aether_agent_memory.core.scope import Scope
from aether_agent_memory.session.models import SessionRecord


class InMemorySessionStore:
    def __init__(self) -> None:
        self._records: dict[str, SessionRecord] = {}
        self._lock = asyncio.Lock()

    async def get(self, scope: Scope) -> SessionRecord | None:
        async with self._lock:
            record = self._records.get(_scope_key(scope))
            return record.model_copy(deep=True) if record is not None else None

    async def save(self, record: SessionRecord, *, expected_revision: int) -> bool:
        async with self._lock:
            key = _scope_key(record.scope)
            current = self._records.get(key)
            current_revision = current.revision if current is not None else 0
            if current_revision != expected_revision:
                return False
            if record.revision != expected_revision + 1:
                raise ValueError("session revision must increment by one")
            self._records[key] = record.model_copy(deep=True)
            return True

    async def close(self) -> None:
        async with self._lock:
            self._records.clear()

    async def scan(self, cursor: int = 0, *, limit: int = 100) -> tuple[int, list[SessionRecord]]:
        if limit < 1 or cursor < 0:
            raise ValueError("invalid session scan bounds")
        async with self._lock:
            records = list(self._records.values())
            page = records[cursor : cursor + limit]
            next_cursor = cursor + limit if cursor + limit < len(records) else 0
            return next_cursor, [record.model_copy(deep=True) for record in page]


class RedisSessionStore:
    def __init__(
        self,
        redis_url: str,
        *,
        namespace: str = "aether:p3:session",
        ttl_seconds: int = 30 * 24 * 60 * 60,
        operation_timeout_seconds: float = 1.0,
    ) -> None:
        from redis.asyncio import Redis

        if ttl_seconds < 1 or operation_timeout_seconds <= 0:
            raise ValueError("session TTL and operation timeout must be positive")
        self._redis: Any = Redis.from_url(
            redis_url,
            decode_responses=True,
            socket_connect_timeout=operation_timeout_seconds,
            socket_timeout=operation_timeout_seconds,
        )
        self._namespace = namespace.rstrip(":")
        self._ttl_seconds = ttl_seconds

    async def get(self, scope: Scope) -> SessionRecord | None:
        payload = await self._redis.get(self._key(scope))
        return SessionRecord.model_validate_json(payload) if payload else None

    async def save(self, record: SessionRecord, *, expected_revision: int) -> bool:
        from redis.exceptions import WatchError

        key = self._key(record.scope)
        async with self._redis.pipeline(transaction=True) as pipeline:
            try:
                await pipeline.watch(key)
                payload = await pipeline.get(key)
                current = SessionRecord.model_validate_json(payload) if payload else None
                current_revision = current.revision if current is not None else 0
                if current_revision != expected_revision:
                    await pipeline.unwatch()
                    return False
                if record.revision != expected_revision + 1:
                    await pipeline.unwatch()
                    raise ValueError("session revision must increment by one")
                pipeline.multi()
                pipeline.set(key, record.model_dump_json(), ex=self._ttl_seconds)
                await pipeline.execute()
                return True
            except WatchError:
                return False

    async def close(self) -> None:
        await self._redis.aclose()

    async def scan(self, cursor: int = 0, *, limit: int = 100) -> tuple[int, list[SessionRecord]]:
        if limit < 1 or cursor < 0:
            raise ValueError("invalid session scan bounds")
        cursor, keys = await self._redis.scan(cursor, match=f"{self._namespace}:*", count=limit)
        payloads = await self._redis.mget(keys) if keys else []
        return int(cursor), [SessionRecord.model_validate_json(p) for p in payloads if p]

    def _key(self, scope: Scope) -> str:
        return f"{self._namespace}:{_scope_key(scope)}"


def _scope_key(scope: Scope) -> str:
    parts = (scope.tenant_id, scope.user_id, scope.agent_id, scope.session_id)
    if any(part is None for part in parts):
        raise ValueError("session store requires tenant/user/agent/session scope")
    return sha256("\x1f".join(str(part) for part in parts).encode()).hexdigest()
