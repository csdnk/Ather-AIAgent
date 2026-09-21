from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from typing import Any
from uuid import uuid4

from aether_agent_memory.context_store.models import (
    ContextProjectionWorkItem,
    ContextProjectionWorkStatus,
)
from aether_agent_memory.context_store.ports import ContextProjectionQueuePort


class RedisContextProjectionQueue(ContextProjectionQueuePort):
    """Redis-backed lease queue for Context-derived projections."""

    def __init__(
        self,
        redis_url: str,
        *,
        namespace: str = "aether:p3:context-projection-work",
        ttl_seconds: int = 7 * 24 * 60 * 60,
        lease_seconds: float = 60.0,
        operation_timeout_seconds: float = 1.0,
    ) -> None:
        if ttl_seconds <= 0 or lease_seconds <= 0 or operation_timeout_seconds <= 0:
            raise ValueError("context projection queue settings must be positive")
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

    async def enqueue(self, item: ContextProjectionWorkItem) -> ContextProjectionWorkItem:
        from redis.exceptions import WatchError

        # Dedupe, durable payload and due index are committed together.
        async with asyncio.timeout(self._operation_timeout_seconds):
            for _ in range(5):
                async with self._redis.pipeline(transaction=True) as pipe:
                    try:
                        dedupe = self._dedupe_key(item)
                        await pipe.watch(dedupe)
                        existing_id = await pipe.get(dedupe)
                        key = self._key(str(existing_id or item.work_id))
                        await pipe.watch(key)
                        payload = await pipe.get(key)
                        if existing_id and payload:
                            existing = ContextProjectionWorkItem.model_validate_json(payload)
                            pipe.multi()
                            self._schedule(pipe, existing)
                            await pipe.execute()
                            return existing
                        if payload:
                            raise ValueError("work_id already belongs to another job")
                        pipe.multi()
                        pipe.set(dedupe, item.work_id)
                        pipe.set(self._key(item.work_id), item.model_dump_json())
                        self._schedule(pipe, item)
                        await pipe.execute()
                        return item.model_copy(deep=True)
                    except WatchError:
                        continue
        raise RuntimeError("queue enqueue conflicted after retries")

    async def claim(self, work_id: str) -> ContextProjectionWorkItem | None:
        now = datetime.now(UTC)
        return await self._transition(
            work_id,
            lambda item: (
                item.status == ContextProjectionWorkStatus.PENDING or _lease_expired(item, now)
            ),
            lambda item: item.model_copy(
                update={
                    "status": ContextProjectionWorkStatus.CLAIMED,
                    "attempts": item.attempts + 1,
                    "claim_token": uuid4().hex,
                    "claimed_at": now,
                    "lease_until": now + timedelta(seconds=self._lease_seconds),
                }
            ),
        )

    async def complete(self, work_id: str, *, claim_token: str) -> ContextProjectionWorkItem | None:
        now = datetime.now(UTC)
        return await self._transition(
            work_id,
            lambda item: (
                _lease_is_valid(item, now) and bool(claim_token) and item.claim_token == claim_token
            ),
            lambda item: item.model_copy(
                update={
                    "status": ContextProjectionWorkStatus.SUCCEEDED,
                    "last_error": None,
                    "lease_until": None,
                }
            ),
        )

    async def fail(
        self, work_id: str, error: str, *, claim_token: str
    ) -> ContextProjectionWorkItem | None:
        now = datetime.now(UTC)
        return await self._transition(
            work_id,
            lambda item: (
                _lease_is_valid(item, now) and bool(claim_token) and item.claim_token == claim_token
            ),
            lambda item: item.model_copy(
                update={
                    "status": ContextProjectionWorkStatus.FAILED,
                    "last_error": error,
                    "lease_until": None,
                }
            ),
        )

    async def supersede(
        self, work_id: str, *, claim_token: str
    ) -> ContextProjectionWorkItem | None:
        now = datetime.now(UTC)
        return await self._transition(
            work_id,
            lambda item: (
                _lease_is_valid(item, now) and bool(claim_token) and item.claim_token == claim_token
            ),
            lambda item: item.model_copy(
                update={
                    "status": ContextProjectionWorkStatus.SUPERSEDED,
                    "last_error": "superseded by a newer context revision",
                    "lease_until": None,
                }
            ),
        )

    async def retry(self, work_id: str) -> ContextProjectionWorkItem | None:
        return await self._transition(
            work_id,
            lambda item: item.status == ContextProjectionWorkStatus.FAILED,
            lambda item: item.model_copy(
                update={
                    "status": ContextProjectionWorkStatus.PENDING,
                    "lease_until": None,
                }
            ),
        )

    async def pending(self, *, limit: int = 100) -> list[ContextProjectionWorkItem]:
        if limit < 1:
            raise ValueError("queue limit must be positive")
        async with asyncio.timeout(self._operation_timeout_seconds):
            ids = await self._redis.zrangebyscore(
                self._due_key(),
                "-inf",
                datetime.now(UTC).timestamp(),
                start=0,
                num=limit,
            )
            if not ids:
                return []
            payloads = await self._redis.mget([self._key(str(work_id)) for work_id in ids])
            result = []
            for work_id, payload in zip(ids, payloads, strict=True):
                if payload is None:
                    await self._redis.zrem(self._due_key(), work_id)
                    continue
                item = ContextProjectionWorkItem.model_validate_json(payload)
                if item.status == ContextProjectionWorkStatus.PENDING or (
                    item.status == ContextProjectionWorkStatus.CLAIMED
                    and item.lease_until is not None
                    and item.lease_until <= datetime.now(UTC)
                ):
                    result.append(item)
            return result

    def _due_key(self) -> str:
        return f"{self._namespace}:due"

    def _schedule(self, pipe: Any, item: ContextProjectionWorkItem) -> None:
        if item.status == ContextProjectionWorkStatus.PENDING:
            pipe.zadd(self._due_key(), {item.work_id: datetime.now(UTC).timestamp()})
        elif item.status == ContextProjectionWorkStatus.CLAIMED and item.lease_until is not None:
            pipe.zadd(self._due_key(), {item.work_id: item.lease_until.timestamp()})
        else:
            pipe.zrem(self._due_key(), item.work_id)

    async def migrate_legacy_index(self, cursor: int = 0, *, limit: int = 100) -> int:
        """Explicit upgrade-only bounded scan; never used by the hot poll path."""
        async with asyncio.timeout(self._operation_timeout_seconds):
            cursor, ids = await self._redis.sscan(self._index_key(), cursor=cursor, count=limit)
            for work_id in ids:
                # WATCH ensures an old snapshot cannot overwrite a newer due score.
                await self._transition(str(work_id), lambda item: True, lambda item: item)
            return int(cursor)

    async def close(self) -> None:
        await self._redis.aclose()

    async def _transition(
        self,
        work_id: str,
        predicate: Callable[[ContextProjectionWorkItem], bool],
        update: Callable[[ContextProjectionWorkItem], ContextProjectionWorkItem],
    ) -> ContextProjectionWorkItem | None:
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
                    item = ContextProjectionWorkItem.model_validate_json(payload)
                    if not predicate(item):
                        await pipeline.unwatch()
                        return None
                    updated = update(item)
                    pipeline.multi()
                    pipeline.set(
                        key,
                        updated.model_dump_json(),
                        ex=(
                            self._ttl_seconds
                            if updated.status
                            in {
                                ContextProjectionWorkStatus.SUCCEEDED,
                                ContextProjectionWorkStatus.SUPERSEDED,
                            }
                            else None
                        ),
                    )
                    self._schedule(pipeline, updated)
                    await pipeline.execute()
                    return updated
                except WatchError:
                    return None

    def _key(self, work_id: str) -> str:
        return f"{self._namespace}:{sha256(work_id.encode()).hexdigest()}"

    def _dedupe_key(self, item: ContextProjectionWorkItem) -> str:
        raw = ":".join(
            [
                str(item.uri),
                str(item.source_revision),
                ",".join(sorted(layer.value for layer in item.layers)),
            ]
        )
        return f"{self._namespace}:dedupe:{sha256(raw.encode()).hexdigest()}"

    def _index_key(self) -> str:
        return f"{self._namespace}:index"


def _lease_is_valid(item: ContextProjectionWorkItem, now: datetime) -> bool:
    return (
        item.status == ContextProjectionWorkStatus.CLAIMED
        and item.lease_until is not None
        and item.lease_until > now
    )


def _lease_expired(item: ContextProjectionWorkItem, now: datetime) -> bool:
    return (
        item.status == ContextProjectionWorkStatus.CLAIMED
        and item.lease_until is not None
        and item.lease_until <= now
    )


__all__ = ["RedisContextProjectionQueue"]
