"""Immutable complete bodies; P2 authority and optional bounded Redis replicas.

SQLite/local deployments use the same hash-checked address protocol. The local
body spool is a durable replica, never a field inside MemoryRecord.
"""

import asyncio
import os
import secrets
from pathlib import Path
from typing import Any

from aether_agent_memory.runtime.contracts.foundation import ResourceLocation
from aether_agent_memory.runtime.contracts.models import (
    ErrorCode,
    Scope,
    TrustedContext,
)
from aether_agent_memory.runtime.foundation.common import FoundationError, fingerprint
from aether_agent_memory.runtime.foundation.requests import text_hash

from .policy import RememberPolicy


class RedisBodyCache:
    """Admission and per-scope byte accounting are atomic with TTL eviction."""

    _ADMIT = """
local expired = redis.call('ZRANGEBYSCORE', KEYS[2], '-inf', ARGV[1])
for _, k in ipairs(expired) do
  redis.call('HDEL', KEYS[3], k)
  redis.call('ZREM', KEYS[2], k)
end
local total = 0
for _, n in ipairs(redis.call('HVALS', KEYS[3])) do total = total + tonumber(n) end
local old = tonumber(redis.call('HGET', KEYS[3], KEYS[1]) or '0')
if total - old + tonumber(ARGV[3]) > tonumber(ARGV[4]) then return 0 end
redis.call('SET', KEYS[1], ARGV[5], 'EX', ARGV[2])
redis.call('HSET', KEYS[3], KEYS[1], ARGV[3])
redis.call('ZADD', KEYS[2], tonumber(ARGV[1]) + tonumber(ARGV[2]), KEYS[1])
redis.call('EXPIRE', KEYS[2], ARGV[2])
redis.call('EXPIRE', KEYS[3], ARGV[2])
return 1
"""

    def __init__(self, client: Any, policy: RememberPolicy) -> None:
        self.client, self.policy = client, policy

    @staticmethod
    def keys(scope: Scope, digest: str) -> tuple[str, str, str]:
        prefix = "remember:body:{" + fingerprint(scope.model_dump(mode="json")) + "}:"
        return prefix + digest, prefix + "expiry", prefix + "sizes"

    async def get(self, scope: Scope, digest: str) -> str | None:
        raw = await self.client.get(self.keys(scope, digest)[0])
        if raw is None:
            return None
        value = raw.decode("utf-8") if isinstance(raw, bytes) else str(raw)
        return value if text_hash(value) == digest else None

    async def put(self, scope: Scope, text: str) -> bool:
        import time

        size = len(text.encode("utf-8"))
        if size > self.policy.cache_max_body_bytes:
            return False
        return bool(
            await self.client.eval(
                self._ADMIT,
                3,
                *self.keys(scope, text_hash(text)),
                int(time.time()),
                self.policy.cache_ttl_seconds,
                size,
                self.policy.cache_scope_bytes,
                text,
            )
        )

    async def delete(self, scope: Scope, digest: str) -> None:
        key, expiry, sizes = self.keys(scope, digest)
        async with self.client.pipeline(transaction=True) as pipe:
            pipe.delete(key).zrem(expiry, key).hdel(sizes, key)
            await pipe.execute()


class Bodies:
    def __init__(
        self,
        root: Path,
        policy: RememberPolicy,
        *,
        p2: Any = None,
        cache: RedisBodyCache | None = None,
    ) -> None:
        self.root, self.policy, self.p2, self.cache = root, policy, p2, cache
        self.root.mkdir(parents=True, exist_ok=True)
        self.prepared: dict[str, ResourceLocation] = {}
        self.require_prepared = False

    def location(self, scope: Scope, text: str, kind: str = "body") -> ResourceLocation:
        digest = text_hash(text)
        key = fingerprint([scope.model_dump(mode="json"), digest])
        return ResourceLocation(
            kind=kind,
            provider_id="p2" if self.p2 else "local",
            provider_instance_id="remember",
            namespace="remember_bodies",
            object_key="remember/bodies/" + key,
            generation=digest,
            content_hash=digest,
        )

    def path(self, location: ResourceLocation) -> Path:
        # Provider keys are never interpreted as local paths.
        return self.root / fingerprint(location.model_dump(mode="json", exclude={"kind"}))

    def stage(self, scope: Scope, text: str) -> ResourceLocation:
        location = self.location(scope, text)
        key = location.object_key
        if self.require_prepared and self.p2 and key not in self.prepared:
            raise FoundationError(ErrorCode.COMMIT_UNCONFIRMED, "P2 body needs stage verification")
        if self.p2 and hasattr(self.p2, "put_object_sync") and key not in self.prepared:
            self.p2.put_object_sync(key, text.encode("utf-8"))
            self.prepared[key] = location
        if self.p2 and key not in self.prepared:
            raise FoundationError(ErrorCode.COMMIT_UNCONFIRMED, "P2 body not verified")
        self._spool(location, text)
        return location

    def _spool(self, location: ResourceLocation, text: str) -> None:
        path = self.path(location)
        if path.exists():
            try:
                if self.read_local(location) == text:
                    return
            except FoundationError:
                pass
        if text_hash(text) != location.content_hash:
            raise FoundationError(ErrorCode.CONTRACT_VIOLATION, "immutable body changed")
        temporary = path.with_suffix("." + secrets.token_hex(8) + ".tmp")
        data = text.encode("utf-8")
        try:
            with temporary.open("xb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            try:
                os.replace(temporary, path)
            except PermissionError:
                # Windows may deny replacement while another delivery reads the
                # same immutable file. Only an exact already-published copy is success.
                if not path.is_file() or path.read_bytes() != data:
                    raise
        finally:
            temporary.unlink(missing_ok=True)

    async def persist(self, ctx: TrustedContext, scope: Scope, text: str) -> ResourceLocation:
        location = self.location(scope, text)
        if self.p2:
            raw = await self.p2_call("get_object", location.object_key)
            if raw is None:
                await self.p2_call("put_object", location.object_key, text.encode("utf-8"))
                raw = await self.p2_call("get_object", location.object_key)
            if raw != text.encode("utf-8"):
                raise FoundationError(ErrorCode.COMMIT_UNCONFIRMED, "P2 exact body not readable")
        self.prepared[location.object_key] = location
        await asyncio.to_thread(self._spool, location, text)
        return location

    async def p2_call(self, method: str, *args: Any) -> Any:
        try:
            return await getattr(self.p2, method)(*args)
        except FoundationError:
            raise
        except Exception as exc:
            raise FoundationError(
                ErrorCode.DEPENDENCY_UNAVAILABLE, "P2 body operation unavailable"
            ) from exc

    def read_local(self, location: ResourceLocation) -> str:
        try:
            if not self.path(location).exists() and hasattr(self.p2, "get_object_sync"):
                raw = self.p2.get_object_sync(location.object_key)
                if raw is None:
                    raise OSError("P2 body missing")
                text = bytes(raw).decode("utf-8")
            else:
                text = self.path(location).read_bytes().decode("utf-8")
        except (OSError, UnicodeError) as exc:
            raise FoundationError(
                ErrorCode.DEPENDENCY_UNAVAILABLE, "body replica unavailable"
            ) from exc
        if text_hash(text) != location.content_hash:
            raise FoundationError(ErrorCode.CONTRACT_VIOLATION, "body hash mismatch")
        return text

    async def read(self, scope: Scope, location: ResourceLocation) -> tuple[str, str]:
        if self.cache:
            try:
                value = await self.cache.get(scope, location.content_hash)
                if value is not None:
                    await asyncio.to_thread(self._spool, location, value)
                    return value, "cache"
            except Exception:
                pass  # Cache loss cannot erase a durably saved source.
        if location.provider_id == "p2":
            if self.p2 is None:
                raise FoundationError(ErrorCode.DEPENDENCY_UNAVAILABLE, "P2 body reader missing")
            raw = await self.p2_call("get_object", location.object_key)
            if raw is None:
                raise FoundationError(ErrorCode.NOT_FOUND, "P2 body missing")
            value = raw.decode("utf-8")
            if text_hash(value) != location.content_hash:
                raise FoundationError(ErrorCode.CONTRACT_VIOLATION, "P2 body hash mismatch")
            await asyncio.to_thread(self._spool, location, value)
            return value, "p2"
        return await asyncio.to_thread(self.read_local, location), "authority"

    async def admit(self, scope: Scope, text: str) -> str:
        if not self.cache:
            return "disabled"
        try:
            return "cached" if await self.cache.put(scope, text) else "not_admitted"
        except Exception:
            return "unavailable"
