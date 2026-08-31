from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from typing import Any

from aether_agent_memory.memory.projection import (
    ProjectionQueuePort,
    ProjectionWorkItem,
    ProjectionWorkStatus,
)


class RedisProjectionQueue(ProjectionQueuePort):
    """Redis-backed durable queue for P3 derived projection work.

    The queue stores the typed work item as the durable record and uses a lease
    on claims so a crashed worker can be reclaimed without changing Memory.
    Execution remains outside this adapter.
    """

    def __init__(
        self,
        redis_url: str,
        *,
        namespace: str = "aether:p3:projection-work",
        ttl_seconds: int = 7 * 24 * 60 * 60,
        lease_seconds: float = 60.0,
        operation_timeout_seconds: float = 1.0,
    ) -> None:
        if ttl_seconds <= 0 or lease_seconds <= 0 or operation_timeout_seconds <= 0:
            raise ValueError("projection queue timeouts and TTL must be positive")
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

    async def enqueue(self, item: ProjectionWorkItem) -> ProjectionWorkItem:
        payload = item.model_dump_json()
        async with asyncio.timeout(self._operation_timeout_seconds):
            dedupe_key = self._dedupe_key(item)
            existing_work_id = await self._redis.get(dedupe_key)
            if existing_work_id:
                existing = await self._redis.get(self._key(str(existing_work_id)))
                if existing is not None:
                    return ProjectionWorkItem.model_validate_json(existing)
            created = await self._redis.set(
                dedupe_key,
                item.work_id,
                nx=True,
                ex=self._ttl_seconds,
            )
            if not created:
                existing_work_id = await self._redis.get(dedupe_key)
                existing = (
                    await self._redis.get(self._key(str(existing_work_id)))
                    if existing_work_id
                    else None
                )
                if existing is not None:
                    return ProjectionWorkItem.model_validate_json(existing)
                await self._redis.delete(dedupe_key)
                return await self.enqueue(item)
            key = self._key(item.work_id)
            await self._redis.set(key, payload, ex=self._ttl_seconds)
            await self._redis.sadd(self._index_key(), item.work_id)
        return item.model_copy(deep=True)

    async def claim(self, work_id: str) -> ProjectionWorkItem | None:
        now = datetime.now(UTC)

        def can_claim(item: ProjectionWorkItem) -> bool:
            return item.status == ProjectionWorkStatus.PENDING or (
                item.status == ProjectionWorkStatus.CLAIMED
                and item.lease_until is not None
                and item.lease_until <= now
            )

        return await self._transition(
            work_id,
            can_claim,
            lambda item: item.model_copy(
                update={
                    "status": ProjectionWorkStatus.CLAIMED,
                    "attempts": item.attempts + 1,
                    "claimed_at": now,
                    "lease_until": now
                    + timedelta(seconds=self._lease_seconds),
                }
            ),
        )

    async def complete(self, work_id: str) -> ProjectionWorkItem | None:
        now = datetime.now(UTC)
        return await self._transition(
            work_id,
            lambda item: _lease_is_valid(item, now),
            lambda item: item.model_copy(
                update={
                    "status": ProjectionWorkStatus.SUCCEEDED,
                    "last_error": None,
                    "lease_until": None,
                }
            ),
        )

    async def fail(self, work_id: str, error: str) -> ProjectionWorkItem | None:
        now = datetime.now(UTC)
        return await self._transition(
            work_id,
            lambda item: _lease_is_valid(item, now),
            lambda item: item.model_copy(
                update={
                    "status": ProjectionWorkStatus.FAILED,
                    "last_error": error,
                    "lease_until": None,
                }
            ),
        )

    async def supersede(self, work_id: str) -> ProjectionWorkItem | None:
        now = datetime.now(UTC)
        return await self._transition(
            work_id,
            lambda item: _lease_is_valid(item, now),
            lambda item: item.model_copy(
                update={
                    "status": ProjectionWorkStatus.SUPERSEDED,
                    "last_error": "superseded by a newer memory revision",
                    "lease_until": None,
                }
            ),
        )

    async def retry(self, work_id: str) -> ProjectionWorkItem | None:
        return await self._transition(
            work_id,
            lambda item: item.status == ProjectionWorkStatus.FAILED,
            lambda item: item.model_copy(
                update={"status": ProjectionWorkStatus.PENDING, "lease_until": None}
            ),
        )

    async def pending(self) -> list[ProjectionWorkItem]:
        async with asyncio.timeout(self._operation_timeout_seconds):
            work_ids = await self._redis.smembers(self._index_key())
            payloads = await self._redis.mget(
                [self._key(str(work_id)) for work_id in work_ids]
            )
        result: list[ProjectionWorkItem] = []
        for payload in payloads:
            if payload is None:
                continue
            item = ProjectionWorkItem.model_validate_json(payload)
            if item.status == ProjectionWorkStatus.PENDING or _lease_expired(item):
                result.append(item)
        return result

    async def close(self) -> None:
        await self._redis.aclose()

    async def _transition(
        self,
        work_id: str,
        predicate: Callable[[ProjectionWorkItem], bool],
        update: Callable[[ProjectionWorkItem], ProjectionWorkItem],
    ) -> ProjectionWorkItem | None:
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
                    item = ProjectionWorkItem.model_validate_json(payload)
                    if not predicate(item):
                        await pipeline.unwatch()
                        return None
                    updated = update(item)
                    pipeline.multi()
                    pipeline.set(
                        key,
                        updated.model_dump_json(),
                        ex=self._ttl_seconds,
                    )
                    await pipeline.execute()
                    return updated
                except WatchError:
                    return None

    def _key(self, work_id: str) -> str:
        digest = sha256(work_id.encode("utf-8")).hexdigest()
        return f"{self._namespace}:{digest}"

    def _dedupe_key(self, item: ProjectionWorkItem) -> str:
        raw = f"{item.memory_id}:{item.revision}:{item.kind.value}"
        digest = sha256(raw.encode("utf-8")).hexdigest()
        return f"{self._namespace}:dedupe:{digest}"

    def _index_key(self) -> str:
        return f"{self._namespace}:index"


def _lease_is_valid(item: ProjectionWorkItem, now: datetime) -> bool:
    return (
        item.status == ProjectionWorkStatus.CLAIMED
        and (item.lease_until is None or item.lease_until > now)
    )


def _lease_expired(item: ProjectionWorkItem) -> bool:
    return (
        item.status == ProjectionWorkStatus.CLAIMED
        and item.lease_until is not None
        and item.lease_until <= datetime.now(UTC)
    )
