"""Persistent action-log sink for B3 scheduling decisions."""

from __future__ import annotations

from hashlib import sha256
from time import time
from typing import Any
from urllib.parse import quote

from aether_agent_memory.b3.models import ActionLogEntry, ScheduleAction


class RedisActionLogStore:
    """Persist B3 ``ActionLogEntry`` records in Redis sorted sets.

    Scoped actions use a hashed tenant/user/agent namespace so equal object ids
    cannot share an audit bucket across tenants. Legacy unscoped actions retain
    their original key for compatibility. Writes are best-effort audit records,
    not part of the decision path.
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

    def append_entries(self, entries: list[ActionLogEntry]) -> None:
        for entry in entries:
            key = self._key_for_action(entry.action)
            self._redis.zadd(key, {entry.model_dump_json(): time()})
            self._redis.expire(key, self._ttl_seconds)

    def _key_for_action(self, action: ScheduleAction) -> str:
        scope = (action.tenant_id, action.user_id, action.agent_id)
        if not any(scope):
            return f"{self._prefix}{action.object_id}"
        scope_material = "\0".join(value or "<none>" for value in scope)
        scope_hash = sha256(scope_material.encode("utf-8")).hexdigest()[:24]
        object_id = quote(action.object_id, safe="")
        return f"{self._prefix}v2:{scope_hash}:{object_id}"

    def close(self) -> None:
        self._redis.close()
