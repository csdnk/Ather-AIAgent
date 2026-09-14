from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from itertools import islice
from uuid import uuid4

from aether_agent_memory.context_store.models import (
    ContextProjectionWorkItem,
    ContextProjectionWorkStatus,
)
from aether_agent_memory.context_store.ports import ContextProjectionQueuePort


class InMemoryContextProjectionQueue(ContextProjectionQueuePort):
    """Reference queue for tests and integration profiles."""

    def __init__(self, *, lease_seconds: float = 60.0) -> None:
        if lease_seconds <= 0:
            raise ValueError("context projection lease must be positive")
        self._items: dict[str, ContextProjectionWorkItem] = {}
        self._keys: dict[tuple[str, int, tuple[str, ...]], str] = {}
        self._lease_seconds = lease_seconds
        self._lock = asyncio.Lock()

    async def enqueue(self, item: ContextProjectionWorkItem) -> ContextProjectionWorkItem:
        key = _dedupe_key(item)
        async with self._lock:
            existing_id = self._keys.get(key)
            if existing_id is not None:
                return self._items[existing_id].model_copy(deep=True)
            self._keys[key] = item.work_id
            self._items[item.work_id] = item.model_copy(deep=True)
            return item.model_copy(deep=True)

    async def claim(self, work_id: str) -> ContextProjectionWorkItem | None:
        async with self._lock:
            item = self._items.get(work_id)
            now = datetime.now(UTC)
            reclaimable = (
                item is not None
                and item.status == ContextProjectionWorkStatus.CLAIMED
                and item.lease_until is not None
                and item.lease_until <= now
            )
            if item is None or (
                item.status != ContextProjectionWorkStatus.PENDING and not reclaimable
            ):
                return None
            item.status = ContextProjectionWorkStatus.CLAIMED
            item.claim_token = uuid4().hex
            item.attempts += 1
            item.claimed_at = now
            item.lease_until = now + timedelta(seconds=self._lease_seconds)
            return item.model_copy(deep=True)

    async def complete(self, work_id: str, *, claim_token: str) -> ContextProjectionWorkItem | None:
        async with self._lock:
            item = self._items.get(work_id)
            if (
                item is None
                or item.status != ContextProjectionWorkStatus.CLAIMED
                or not claim_token
                or item.claim_token != claim_token
                or item.lease_until is None
                or item.lease_until <= datetime.now(UTC)
            ):
                return None
            item.status = ContextProjectionWorkStatus.SUCCEEDED
            item.last_error = None
            item.lease_until = None
            return item.model_copy(deep=True)

    async def fail(
        self, work_id: str, error: str, *, claim_token: str
    ) -> ContextProjectionWorkItem | None:
        async with self._lock:
            item = self._items.get(work_id)
            if (
                item is None
                or item.status != ContextProjectionWorkStatus.CLAIMED
                or not claim_token
                or item.claim_token != claim_token
                or item.lease_until is None
                or item.lease_until <= datetime.now(UTC)
            ):
                return None
            item.status = ContextProjectionWorkStatus.FAILED
            item.last_error = error
            item.lease_until = None
            return item.model_copy(deep=True)

    async def supersede(
        self, work_id: str, *, claim_token: str
    ) -> ContextProjectionWorkItem | None:
        async with self._lock:
            item = self._items.get(work_id)
            if (
                item is None
                or item.status != ContextProjectionWorkStatus.CLAIMED
                or not claim_token
                or item.claim_token != claim_token
                or item.lease_until is None
                or item.lease_until <= datetime.now(UTC)
            ):
                return None
            item.status = ContextProjectionWorkStatus.SUPERSEDED
            item.last_error = "superseded by a newer context revision"
            item.lease_until = None
            return item.model_copy(deep=True)

    async def retry(self, work_id: str) -> ContextProjectionWorkItem | None:
        async with self._lock:
            item = self._items.get(work_id)
            if item is None or item.status != ContextProjectionWorkStatus.FAILED:
                return None
            item.status = ContextProjectionWorkStatus.PENDING
            item.lease_until = None
            return item.model_copy(deep=True)

    async def pending(self, *, limit: int = 100) -> list[ContextProjectionWorkItem]:
        if limit < 1:
            raise ValueError("queue limit must be positive")
        async with self._lock:
            now = datetime.now(UTC)
            return list(
                islice(
                    (
                        item.model_copy(deep=True)
                        for item in self._items.values()
                        if item.status == ContextProjectionWorkStatus.PENDING
                        or (
                            item.status == ContextProjectionWorkStatus.CLAIMED
                            and item.lease_until is not None
                            and item.lease_until <= now
                        )
                    ),
                    limit,
                )
            )

    async def close(self) -> None:
        async with self._lock:
            self._items.clear()
            self._keys.clear()


def _dedupe_key(item: ContextProjectionWorkItem) -> tuple[str, int, tuple[str, ...]]:
    return (
        str(item.uri),
        item.source_revision,
        tuple(sorted(layer.value for layer in item.layers)),
    )


def _lease_expired(item: ContextProjectionWorkItem) -> bool:
    return (
        item.status == ContextProjectionWorkStatus.CLAIMED
        and item.lease_until is not None
        and item.lease_until <= datetime.now(UTC)
    )


__all__ = ["InMemoryContextProjectionQueue"]
