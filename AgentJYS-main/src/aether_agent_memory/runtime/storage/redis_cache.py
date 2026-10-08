"""Environment-isolated Redis replicas, with atomic per-scope byte quotas.

Bodies and expiry accounting share one bounded hash per scope. Redis eviction
therefore removes both; it cannot leave unaccounted bodies or phantom quota.
Logical per-body TTL is checked using Redis time on every read and admission.
The outer hash expires at the latest member expiry. This is a cache only: PG
owns action receipts and Ceph owns original bytes.
"""

import asyncio
import json
import re
from collections.abc import Callable
from typing import Any

from redis.exceptions import ResponseError

from aether_agent_memory.remember.basic.policy import RememberPolicy
from aether_agent_memory.remember.contracts.models import MemoryRef
from aether_agent_memory.runtime.contracts.foundation import ResourceLocation
from aether_agent_memory.runtime.contracts.models import ErrorCode, Scope
from aether_agent_memory.runtime.foundation.common import FoundationError, fingerprint
from aether_agent_memory.runtime.foundation.requests import text_hash


class RedisCache:
    provider_id = "redis"

    _READ = """
local body = redis.call('HGET', KEYS[1], ARGV[1])
if not body then return false end
local expires = tonumber(redis.call('HGET', KEYS[1], ARGV[2]))
if not expires then return redis.error_reply('CACHE_CORRUPT expiry') end
local now = tonumber(redis.call('TIME')[1])
if expires <= now then
  redis.call('HDEL', KEYS[1], ARGV[1], ARGV[2])
  return false
end
return body
"""

    _ADMIT = """
local now = tonumber(redis.call('TIME')[1])
local expires = now + tonumber(ARGV[3])
local latest = expires
local total, count, old = 0, 0, 0
local values = redis.call('HGETALL', KEYS[1])
-- Validate before mutation: Redis Lua errors do not roll back earlier writes.
for i=1,#values,2 do
  local field = values[i]
  if string.sub(field,1,2) == 'b:' then
    local expiry = tonumber(redis.call('HGET', KEYS[1], 'e:'..string.sub(field,3)))
    if not expiry then return redis.error_reply('CACHE_CORRUPT expiry') end
  end
end
for i=1,#values,2 do
  local field, value = values[i], values[i+1]
  if string.sub(field,1,2) == 'b:' then
    local ef = 'e:'..string.sub(field,3)
    local expiry = tonumber(redis.call('HGET', KEYS[1], ef))
    if expiry <= now then
      redis.call('HDEL', KEYS[1], field, ef)
    else
      total = total + string.len(value)
      count = count + 1
      if field == ARGV[1] then old = string.len(value); count = count - 1 end
      latest = math.max(latest, expiry)
    end
  end
end
if total - old + string.len(ARGV[5]) > tonumber(ARGV[4]) then return 0 end
if count >= tonumber(ARGV[6]) then return 0 end
redis.call('HSET', KEYS[1], ARGV[1], ARGV[5], ARGV[2], expires)
redis.call('EXPIREAT', KEYS[1], latest)
return 1
"""

    _USAGE = """
local now = tonumber(redis.call('TIME')[1])
local total = 0
local values = redis.call('HGETALL', KEYS[1])
for i=1,#values,2 do
  if string.sub(values[i],1,2) == 'b:' then
    local expires = tonumber(redis.call('HGET', KEYS[1], 'e:'..string.sub(values[i],3)))
    if not expires then return redis.error_reply('CACHE_CORRUPT expiry') end
    if expires > now then total = total + string.len(values[i+1]) end
  end
end
return total
"""

    def __init__(
        self,
        client: Any,
        policy: RememberPolicy,
        *,
        namespace: str,
        max_scope_entries: int = 1024,
    ) -> None:
        if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", namespace):
            raise ValueError("Redis namespace must be a bounded environment identifier")
        if type(max_scope_entries) is not int or max_scope_entries < 1:
            raise ValueError("Redis cache entry limit must be positive")
        self.client, self.policy, self.namespace = client, policy, namespace
        self.capacity_bytes = policy.cache_scope_bytes
        self.max_scope_entries = max_scope_entries
        options = client.connection_pool.connection_kwargs
        self.resource_id = fingerprint(
            [options.get("host"), options.get("port"), options.get("db", 0)]
        )

    def keys(self, scope: Scope, digest: str) -> tuple[str, str, str]:
        """Return the hash key, body field and expiry field, all in one hash slot."""
        if not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise FoundationError(ErrorCode.INVALID_ARGUMENT, "invalid cached content hash")
        tag = self.namespace + ":" + fingerprint(scope.model_dump(mode="json"))
        return f"aether:{self.namespace}:body:{{{tag}}}", "b:" + digest, "e:" + digest

    def describe_location(self, scope: Scope, digest: str, *, generation: str) -> ResourceLocation:
        """Canonical Redis address shared by Remember and Operate; no Redis I/O."""
        key, field, expiry = self.keys(scope, digest)
        return ResourceLocation(
            kind="cache",
            provider_id=self.provider_id,
            provider_instance_id=self.resource_id,
            namespace=self.namespace,
            object_key=json.dumps(
                {
                    "encoding": "redis_hash_v1",
                    "key": key,
                    "field": field,
                    "expiry_field": expiry,
                },
                sort_keys=True,
                separators=(",", ":"),
            ),
            generation=generation,
            content_hash=digest,
        )

    @staticmethod
    def _call[T](operation: Callable[[], T]) -> T:
        try:
            return operation()
        except FoundationError:
            raise
        except ResponseError as exc:
            if "CACHE_CORRUPT" in str(exc) or "WRONGTYPE" in str(exc):
                raise FoundationError(
                    ErrorCode.CONTRACT_VIOLATION, "Redis cache structure is corrupt"
                ) from None
            raise FoundationError(
                ErrorCode.DEPENDENCY_UNAVAILABLE, "Redis cache operation unavailable"
            ) from None
        except Exception:
            raise FoundationError(
                ErrorCode.DEPENDENCY_UNAVAILABLE, "Redis cache unavailable"
            ) from None

    def raw_sync(self, scope: Scope, digest: str) -> bytes | str | None:
        key, body, expires = self.keys(scope, digest)
        raw: bytes | str | None = self._call(
            lambda: self.client.eval(self._READ, 1, key, body, expires)
        )
        return raw

    def get_sync(self, scope: Scope, digest: str) -> str | None:
        raw = self.raw_sync(scope, digest)
        if raw is None:
            return None
        try:
            value = raw.decode("utf8") if isinstance(raw, bytes) else raw
        except UnicodeDecodeError:
            raise FoundationError(
                ErrorCode.CONTRACT_VIOLATION, "cached body is not valid UTF-8"
            ) from None
        if text_hash(value) != digest:
            raise FoundationError(ErrorCode.CONTRACT_VIOLATION, "cached body hash differs")
        return value

    def put_sync(self, scope: Scope, text: str) -> bool:
        if len(text.encode("utf8")) > self.policy.cache_max_body_bytes:
            return False
        key, body, expires = self.keys(scope, text_hash(text))
        return bool(
            self._call(
                lambda: self.client.eval(
                    self._ADMIT,
                    1,
                    key,
                    body,
                    expires,
                    self.policy.cache_ttl_seconds,
                    min(self.capacity_bytes, self.policy.cache_scope_bytes),
                    text,
                    self.max_scope_entries,
                )
            )
        )

    def delete_sync(self, scope: Scope, digest: str) -> None:
        key, body, expires = self.keys(scope, digest)
        self._call(lambda: self.client.hdel(key, body, expires))

    async def get(self, scope: Scope, digest: str) -> str | None:
        return await asyncio.to_thread(self.get_sync, scope, digest)

    async def put(self, scope: Scope, text: str) -> bool:
        return await asyncio.to_thread(self.put_sync, scope, text)

    async def delete(self, scope: Scope, digest: str) -> None:
        await asyncio.to_thread(self.delete_sync, scope, digest)

    async def cleanup_complete(self, memory: MemoryRef, digest: str) -> bool:
        return await asyncio.to_thread(self.raw_sync, memory.scope, digest) is None

    def usage_sync(self, scope: Scope) -> int:
        key = self.keys(scope, "0" * 64)[0]
        return int(self._call(lambda: self.client.eval(self._USAGE, 1, key)))

    def check_connection(self) -> None:
        if not self._call(self.client.ping):
            raise FoundationError(ErrorCode.DEPENDENCY_UNAVAILABLE, "Redis cache unavailable")
