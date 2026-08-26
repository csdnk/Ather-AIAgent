"""Persistent action-log sink for B3 scheduling decisions."""

from __future__ import annotations

from time import time
from typing import Any


class RedisActionLogStore:
    """Persist B3 ``ActionLogEntry`` records in Redis sorted sets.

    Each object id gets a sorted set of decision entries (score = timestamp),
    so the scheduling history survives process restarts.  Writes are
    best-effort audit records, not part of the decision path.
    """

    def __init__(
        self,
        redis_url: str,
        *,
        key_prefix: str = "p3:actionlog:",
        ttl_seconds: int = 7 * 24 * 60 * 60,
        client: Any | None = None,
    ) -> None:
        from redis import Redis

        if ttl_seconds <= 0:
            raise ValueError("action log TTL must be positive")
        self._redis = client or Redis.from_url(redis_url, decode_responses=True)
        self._prefix = key_prefix
        self._ttl_seconds = ttl_seconds

    def append_entries(self, entries: list[Any]) -> None:
        for entry in entries:
            key = f"{self._prefix}{entry.action.object_id}"
            self._redis.zadd(key, {entry.model_dump_json(): time()})
            self._redis.expire(key, self._ttl_seconds)

    def close(self) -> None:
        self._redis.close()
