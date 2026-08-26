"""Persistent idempotency records with an explicit lifecycle.

Lifecycle: ``PROCESSING -> SUCCEEDED (cached response) | FAILED (retry allowed)``.

Keys are namespaced by tenant and operation type.  A repeated submission with
the same payload hash and a ``SUCCEEDED`` record returns the cached response; a
``FAILED`` record allows a fresh attempt instead of poisoning the key for the
whole TTL after a transient downstream failure.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, cast


class IdempotencyState(StrEnum):
    PROCESSING = "PROCESSING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"


class ClaimOutcome(StrEnum):
    CLAIMED = "claimed"
    CACHED = "cached"
    CONFLICT = "conflict"


def payload_hash(payload: dict[str, Any]) -> str:
    normalized = json.dumps(
        payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")
    )
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


class RedisIdempotencyStore:
    """Atomic idempotency lifecycle backed by Redis keys with a TTL."""

    def __init__(
        self,
        redis_url: str,
        *,
        key_prefix: str = "p3:idempotency:",
        ttl_seconds: int = 24 * 60 * 60,
        client: Any | None = None,
    ) -> None:
        from redis import Redis

        if ttl_seconds <= 0:
            raise ValueError("idempotency TTL must be positive")
        self._redis = client or Redis.from_url(redis_url, decode_responses=True)
        self._prefix = key_prefix
        self._ttl_seconds = ttl_seconds

    def _key(self, *, key: str, tenant_id: str | None, op_type: str) -> str:
        return f"{self._prefix}{tenant_id or '-'}:{op_type}:{key}"

    def _get(self, full_key: str) -> dict[str, Any] | None:
        value = cast(str | bytes | bytearray | None, self._redis.get(full_key))
        return json.loads(value) if value else None

    def claim(
        self,
        *,
        key: str,
        tenant_id: str | None,
        op_type: str,
        payload_hash: str,
    ) -> tuple[str, dict[str, Any] | None]:
        """Claim an idempotency key.

        Returns ``(ClaimOutcome, cached_response)``:
        - ``claimed``: this call owns the operation (first attempt, or a retry
          after a FAILED record);
        - ``cached``: a SUCCEEDED record with the same payload hash exists;
        - ``conflict``: the key is PROCESSING, or the payload hash differs.
        """
        full_key = self._key(key=key, tenant_id=tenant_id, op_type=op_type)
        record = {
            "state": IdempotencyState.PROCESSING.value,
            "payload_hash": payload_hash,
            "claimed_at": datetime.now(UTC).isoformat(),
        }
        created = self._redis.set(
            full_key, json.dumps(record), nx=True, ex=self._ttl_seconds
        )
        if created:
            return ClaimOutcome.CLAIMED.value, None
        existing = self._get(full_key)
        if existing is None:  # record expired between set and read
            self._redis.set(full_key, json.dumps(record), ex=self._ttl_seconds)
            return ClaimOutcome.CLAIMED.value, None
        state = existing.get("state")
        if state == IdempotencyState.SUCCEEDED.value:
            if (
                existing.get("payload_hash") == payload_hash
                and existing.get("response") is not None
            ):
                return ClaimOutcome.CACHED.value, existing.get("response")
            return ClaimOutcome.CONFLICT.value, None
        if state == IdempotencyState.FAILED.value:
            self._redis.set(full_key, json.dumps(record), ex=self._ttl_seconds)
            return ClaimOutcome.CLAIMED.value, None
        return ClaimOutcome.CONFLICT.value, None  # PROCESSING

    def complete(
        self,
        *,
        key: str,
        tenant_id: str | None,
        op_type: str,
        response: dict[str, Any],
    ) -> None:
        full_key = self._key(key=key, tenant_id=tenant_id, op_type=op_type)
        existing = self._get(full_key)
        if existing is None:
            return
        existing["state"] = IdempotencyState.SUCCEEDED.value
        existing["response"] = response
        self._redis.set(full_key, json.dumps(existing), ex=self._ttl_seconds)

    def fail(self, *, key: str, tenant_id: str | None, op_type: str) -> None:
        full_key = self._key(key=key, tenant_id=tenant_id, op_type=op_type)
        existing = self._get(full_key)
        if existing is None:
            return
        existing["state"] = IdempotencyState.FAILED.value
        self._redis.set(full_key, json.dumps(existing), ex=self._ttl_seconds)

    def get(
        self, *, key: str, tenant_id: str | None, op_type: str
    ) -> dict[str, Any] | None:
        return self._get(self._key(key=key, tenant_id=tenant_id, op_type=op_type))
