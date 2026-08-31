from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from typing import Any

from aether_agent_memory.session.models import (
    SessionExtractionWorkItem,
    SessionExtractionWorkStatus,
)
from aether_agent_memory.session.ports import SessionExtractionQueuePort


class RedisSessionExtractionQueue(SessionExtractionQueuePort):
    """Durable Redis queue for Archive-to-Memory extraction work."""

    def __init__(
        self,
        redis_url: str,
        *,
        namespace: str = "aether:p3:session-extraction",
        ttl_seconds: int = 7 * 24 * 60 * 60,
        lease_seconds: float = 60.0,
        operation_timeout_seconds: float = 1.0,
    ) -> None:
        if ttl_seconds <= 0 or lease_seconds <= 0 or operation_timeout_seconds <= 0:
            raise ValueError("session extraction queue settings must be positive")
        from redis.asyncio import Redis

        self._redis: Any = Redis.from_url(
            redis_url,
            decode_responses=True,
            socket_connect_timeout=operation_timeout_seconds,
            socket_timeout=operation_timeout_seconds,
        )
        self._namespace = namespace.rstrip(":")
        self._ttl_seconds = ttl_seconds
        self._lease_seconds = lease_seconds
        self._operation_timeout_seconds = operation_timeout_seconds

    async def enqueue(self, item: SessionExtractionWorkItem) -> SessionExtractionWorkItem:
        if item.scope.session_id is None:
            raise ValueError("session extraction queue requires session scope")
        async with asyncio.timeout(self._operation_timeout_seconds):
            dedupe_key = self._dedupe_key(item)
            existing_id = await self._redis.get(dedupe_key)
            if existing_id:
                existing = await self._redis.get(self._key(str(existing_id)))
                if existing is not None:
                    return SessionExtractionWorkItem.model_validate_json(existing)
            if not await self._redis.set(
                dedupe_key, item.work_id, nx=True, ex=self._ttl_seconds
            ):
                existing_id = await self._redis.get(dedupe_key)
                existing = (
                    await self._redis.get(self._key(str(existing_id)))
                    if existing_id
                    else None
                )
                if existing is not None:
                    return SessionExtractionWorkItem.model_validate_json(existing)
                await self._redis.delete(dedupe_key)
                return await self.enqueue(item)
            await self._redis.set(
                self._key(item.work_id),
                item.model_dump_json(),
                ex=self._ttl_seconds,
            )
            await self._redis.sadd(self._index_key(), item.work_id)
        return item.model_copy(deep=True)

    async def claim(self, work_id: str) -> SessionExtractionWorkItem | None:
        now = datetime.now(UTC)
        return await self._transition(
            work_id,
            lambda item: item.status == SessionExtractionWorkStatus.PENDING
            or (
                item.status == SessionExtractionWorkStatus.CLAIMED
                and item.lease_until is not None
                and item.lease_until <= now
            ),
            lambda item: item.model_copy(
                update={
                    "status": SessionExtractionWorkStatus.CLAIMED,
                    "attempts": item.attempts + 1,
                    "claimed_at": now,
                    "lease_until": now + timedelta(seconds=self._lease_seconds),
                }
            ),
        )

    async def complete(self, work_id: str) -> SessionExtractionWorkItem | None:
        now = datetime.now(UTC)
        return await self._transition(
            work_id,
            lambda item: _lease_is_valid(item, now),
            lambda item: item.model_copy(
                update={
                    "status": SessionExtractionWorkStatus.SUCCEEDED,
                    "last_error": None,
                    "lease_until": None,
                }
            ),
        )

    async def fail(self, work_id: str, error: str) -> SessionExtractionWorkItem | None:
        now = datetime.now(UTC)
        return await self._transition(
            work_id,
            lambda item: _lease_is_valid(item, now),
            lambda item: item.model_copy(
                update={
                    "status": SessionExtractionWorkStatus.FAILED,
                    "last_error": error,
                    "lease_until": None,
                }
            ),
        )

    async def retry(self, work_id: str) -> SessionExtractionWorkItem | None:
        return await self._transition(
            work_id,
            lambda item: item.status == SessionExtractionWorkStatus.FAILED,
            lambda item: item.model_copy(
                update={
                    "status": SessionExtractionWorkStatus.PENDING,
                    "lease_until": None,
                }
            ),
        )

    async def pending(self) -> list[SessionExtractionWorkItem]:
        async with asyncio.timeout(self._operation_timeout_seconds):
            work_ids = await self._redis.smembers(self._index_key())
            payloads = await self._redis.mget(
                [self._key(str(work_id)) for work_id in work_ids]
            )
        result: list[SessionExtractionWorkItem] = []
        for payload in payloads:
            if payload is None:
                continue
            item = SessionExtractionWorkItem.model_validate_json(payload)
            if item.status == SessionExtractionWorkStatus.PENDING or _lease_expired(item):
                result.append(item)
        return result

    async def close(self) -> None:
        await self._redis.aclose()

    async def _transition(
        self,
        work_id: str,
        predicate: Callable[[SessionExtractionWorkItem], bool],
        update: Callable[[SessionExtractionWorkItem], SessionExtractionWorkItem],
    ) -> SessionExtractionWorkItem | None:
        from redis.exceptions import WatchError

        key = self._key(work_id)
        async with asyncio.timeout(self._operation_timeout_seconds):
            async with self._redis.pipeline(transaction=True) as pipeline:
                try:
                    await pipeline.watch(key)
                    payload = await pipeline.get(key)
                    if payload is None:
                        await pipeline.unwatch()
                        return None
                    item = SessionExtractionWorkItem.model_validate_json(payload)
                    if not predicate(item):
                        await pipeline.unwatch()
                        return None
                    updated = update(item)
                    pipeline.multi()
                    pipeline.set(key, updated.model_dump_json(), ex=self._ttl_seconds)
                    await pipeline.execute()
                    return updated
                except WatchError:
                    return None

    def _key(self, work_id: str) -> str:
        return f"{self._namespace}:{sha256(work_id.encode()).hexdigest()}"

    def _dedupe_key(self, item: SessionExtractionWorkItem) -> str:
        raw = ":".join(
            str(value)
            for value in (
                item.scope.tenant_id,
                item.scope.user_id,
                item.scope.agent_id,
                item.scope.session_id,
                item.archive_id,
            )
        )
        return f"{self._namespace}:dedupe:{sha256(raw.encode()).hexdigest()}"

    def _index_key(self) -> str:
        return f"{self._namespace}:index"


def _lease_is_valid(item: SessionExtractionWorkItem, now: datetime) -> bool:
    return (
        item.status == SessionExtractionWorkStatus.CLAIMED
        and (item.lease_until is None or item.lease_until > now)
    )


def _lease_expired(item: SessionExtractionWorkItem) -> bool:
    return (
        item.status == SessionExtractionWorkStatus.CLAIMED
        and item.lease_until is not None
        and item.lease_until <= datetime.now(UTC)
    )
