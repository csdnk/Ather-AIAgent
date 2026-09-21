from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from itertools import islice
from uuid import uuid4

from aether_agent_memory.session.models import (
    SessionExtractionWorkItem,
    SessionExtractionWorkStatus,
)
from aether_agent_memory.session.ports import SessionExtractionQueuePort


class InMemorySessionExtractionQueue(SessionExtractionQueuePort):
    """Reference queue for local runs; production uses the Redis sibling."""

    def __init__(self, *, lease_seconds: float = 60.0) -> None:
        if lease_seconds <= 0:
            raise ValueError("session extraction lease must be positive")
        self._items: dict[str, SessionExtractionWorkItem] = {}
        self._keys: dict[tuple[str, str], str] = {}
        self._lease_seconds = lease_seconds
        self._lock = asyncio.Lock()

    async def enqueue(self, item: SessionExtractionWorkItem) -> SessionExtractionWorkItem:
        if item.scope.session_id is None:
            raise ValueError("session extraction queue requires session scope")
        key = (repr(item.scope), item.archive_id)
        async with self._lock:
            existing_id = self._keys.get(key)
            if existing_id is not None:
                return self._items[existing_id].model_copy(deep=True)
            self._keys[key] = item.work_id
            self._items[item.work_id] = item.model_copy(deep=True)
            return item.model_copy(deep=True)

    async def claim(self, work_id: str) -> SessionExtractionWorkItem | None:
        async with self._lock:
            item = self._items.get(work_id)
            now = datetime.now(UTC)
            reclaimable = (
                item is not None
                and item.status == SessionExtractionWorkStatus.CLAIMED
                and item.lease_until is not None
                and item.lease_until <= now
            )
            if item is None or (
                item.status != SessionExtractionWorkStatus.PENDING and not reclaimable
            ):
                return None
            item.status = SessionExtractionWorkStatus.CLAIMED
            item.claim_token = uuid4().hex
            item.attempts += 1
            item.claimed_at = now
            item.lease_until = now + timedelta(seconds=self._lease_seconds)
            return item.model_copy(deep=True)

    async def complete(self, work_id: str, *, claim_token: str) -> SessionExtractionWorkItem | None:
        async with self._lock:
            item = self._items.get(work_id)
            if (
                item is None
                or item.status != SessionExtractionWorkStatus.CLAIMED
                or not claim_token
                or item.claim_token != claim_token
                or item.lease_until is None
                or item.lease_until <= datetime.now(UTC)
            ):
                return None
            item.status = SessionExtractionWorkStatus.SUCCEEDED
            item.last_error = None
            item.lease_until = None
            return item.model_copy(deep=True)

    async def fail(
        self, work_id: str, error: str, *, claim_token: str
    ) -> SessionExtractionWorkItem | None:
        async with self._lock:
            item = self._items.get(work_id)
            if (
                item is None
                or item.status != SessionExtractionWorkStatus.CLAIMED
                or not claim_token
                or item.claim_token != claim_token
                or item.lease_until is None
                or item.lease_until <= datetime.now(UTC)
            ):
                return None
            item.status = SessionExtractionWorkStatus.FAILED
            item.last_error = error
            item.lease_until = None
            return item.model_copy(deep=True)

    async def retry(self, work_id: str) -> SessionExtractionWorkItem | None:
        async with self._lock:
            item = self._items.get(work_id)
            if item is None or item.status != SessionExtractionWorkStatus.FAILED:
                return None
            item.status = SessionExtractionWorkStatus.PENDING
            item.lease_until = None
            return item.model_copy(deep=True)

    async def supersede(
        self, work_id: str, *, claim_token: str
    ) -> SessionExtractionWorkItem | None:
        async with self._lock:
            item = self._items.get(work_id)
            if (
                item is None
                or item.status != SessionExtractionWorkStatus.CLAIMED
                or not claim_token
                or item.claim_token != claim_token
                or item.lease_until is None
                or item.lease_until <= datetime.now(UTC)
            ):
                return None
            item.status = SessionExtractionWorkStatus.SUPERSEDED
            item.last_error = "superseded by newer extraction work"
            item.lease_until = None
            return item.model_copy(deep=True)

    async def pending(self, *, limit: int = 100) -> list[SessionExtractionWorkItem]:
        if limit < 1:
            raise ValueError("queue limit must be positive")
        async with self._lock:
            now = datetime.now(UTC)
            return list(
                islice(
                    (
                        item.model_copy(deep=True)
                        for item in self._items.values()
                        if item.status == SessionExtractionWorkStatus.PENDING
                        or (
                            item.status == SessionExtractionWorkStatus.CLAIMED
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


def _lease_expired(item: SessionExtractionWorkItem) -> bool:
    return (
        item.status == SessionExtractionWorkStatus.CLAIMED
        and item.lease_until is not None
        and item.lease_until <= datetime.now(UTC)
    )
