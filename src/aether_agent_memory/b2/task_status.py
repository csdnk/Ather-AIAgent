"""Redis-backed progress records for asynchronous B2 ingestion."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, cast


class TaskState(StrEnum):
    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"


class RedisTaskStatusStore:
    def __init__(
        self,
        redis_url: str,
        *,
        key_prefix: str = "b2:task:",
        ttl_seconds: int = 7 * 24 * 60 * 60,
    ) -> None:
        from redis import Redis

        if ttl_seconds <= 0:
            raise ValueError("task status TTL must be positive")
        self._redis = Redis.from_url(redis_url, decode_responses=True)
        self._prefix = key_prefix
        self._ttl_seconds = ttl_seconds

    def set(self, task_id: str, state: TaskState, **details: Any) -> dict[str, Any]:
        record = {
            "task_id": task_id,
            "state": state.value,
            "updated_at": datetime.now(UTC).isoformat(),
            **details,
        }
        self._redis.set(
            f"{self._prefix}{task_id}",
            json.dumps(record, ensure_ascii=False),
            ex=self._ttl_seconds,
        )
        return record

    def get(self, task_id: str) -> dict[str, Any] | None:
        value = cast(str | None, self._redis.get(f"{self._prefix}{task_id}"))
        return json.loads(value) if value else None
