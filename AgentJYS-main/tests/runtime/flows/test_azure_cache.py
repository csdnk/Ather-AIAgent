"""Run the body-cache contract against real, isolated Azure Redis keys."""

import asyncio
import socket
import time

import pytest

from aether_agent_memory.remember.basic.policy import RememberPolicy
from aether_agent_memory.remember.contracts.models import MemoryRef
from aether_agent_memory.runtime.contracts.models import ErrorCode, Scope
from aether_agent_memory.runtime.foundation.common import FoundationError
from aether_agent_memory.runtime.foundation.requests import text_hash
from azure_storage_support import azure_redis as azure_redis

SCOPE = Scope(tenant_id="tenant", application_id="app", user_id="alice", agent_id="agent")


def make_cache(client, namespace, **policy):
    from aether_agent_memory.runtime.storage.redis_cache import RedisCache

    return RedisCache(client, RememberPolicy(**policy), namespace=namespace)


async def test_same_scope_and_digest_are_isolated_between_environments(azure_redis):
    client, namespace = azure_redis
    first = make_cache(client, namespace + "-a")
    second = make_cache(client, namespace + "-b")
    assert await first.put(SCOPE, "shared bytes")
    digest = text_hash("shared bytes")
    assert await second.get(SCOPE, digest) is None
    assert await second.put(SCOPE, "shared bytes")
    await first.delete(SCOPE, digest)
    assert await first.get(SCOPE, digest) is None
    assert await second.get(SCOPE, digest) == "shared bytes"


async def test_cache_does_not_cross_scope(azure_redis):
    client, namespace = azure_redis
    cache = make_cache(client, namespace)
    assert await cache.put(SCOPE, "private bytes")
    other = SCOPE.model_copy(update={"user_id": "bob"})
    assert await cache.get(other, text_hash("private bytes")) is None
    assert await cache.get(SCOPE, text_hash("private bytes")) == "private bytes"


@pytest.mark.parametrize("corrupt", [b"changed", b"\xff\xfe"])
async def test_corruption_is_not_a_miss_or_successful_cleanup(azure_redis, corrupt):
    client, namespace = azure_redis
    cache = make_cache(client, namespace)
    assert await cache.put(SCOPE, "original")
    digest = text_hash("original")
    key, body_field, _ = cache.keys(SCOPE, digest)
    client.hset(key, body_field, corrupt)
    with pytest.raises(FoundationError) as error:
        await cache.get(SCOPE, digest)
    assert error.value.code == ErrorCode.CONTRACT_VIOLATION
    memory = MemoryRef(scope=SCOPE, memory_id="m1", version=1)
    assert not await cache.cleanup_complete(memory, digest)
    await cache.delete(SCOPE, digest)
    assert await cache.cleanup_complete(memory, digest)


async def test_quota_is_atomic_under_competing_writers(azure_redis):
    client, namespace = azure_redis
    cache = make_cache(client, namespace, cache_scope_bytes=24, cache_max_body_bytes=12)
    values = [str(index) * 12 for index in range(8)]
    accepted = await asyncio.gather(*(cache.put(SCOPE, body) for body in values))
    assert sum(accepted) == 2
    present = [await cache.get(SCOPE, text_hash(body)) for body in values]
    assert sum(body is not None for body in present) == 2
    assert not await cache.put(SCOPE, "too-large-body")


async def test_expiry_reclaims_quota_for_later_content(azure_redis):
    client, namespace = azure_redis
    cache = make_cache(client, namespace, cache_scope_bytes=8, cache_ttl_seconds=1)
    assert await cache.put(SCOPE, "12345678")
    assert not await cache.put(SCOPE, "abcdefgh")
    until = time.monotonic() + 5
    while await cache.get(SCOPE, text_hash("12345678")) is not None and time.monotonic() < until:
        await asyncio.sleep(0.1)
    assert await cache.get(SCOPE, text_hash("12345678")) is None
    assert await cache.put(SCOPE, "abcdefgh")


async def test_evicted_body_does_not_hold_phantom_quota(azure_redis):
    client, namespace = azure_redis
    cache = make_cache(client, namespace, cache_scope_bytes=8)
    assert await cache.put(SCOPE, "12345678")
    client.delete(cache.keys(SCOPE, text_hash("12345678"))[0])
    assert await cache.put(SCOPE, "abcdefgh")


async def test_shorter_policy_does_not_expire_an_existing_longer_entry(azure_redis):
    client, namespace = azure_redis
    long = make_cache(client, namespace, cache_ttl_seconds=30)
    short = make_cache(client, namespace, cache_ttl_seconds=1)
    assert await long.put(SCOPE, "long-lived")
    assert await short.put(SCOPE, "short-lived")
    await asyncio.sleep(1.2)
    assert await short.get(SCOPE, text_hash("short-lived")) is None
    assert await long.get(SCOPE, text_hash("long-lived")) == "long-lived"


async def test_corrupt_expiry_is_explicit_and_does_not_erase_body(azure_redis):
    client, namespace = azure_redis
    cache = make_cache(client, namespace)
    assert await cache.put(SCOPE, "original")
    key, body_field, expiry_field = cache.keys(SCOPE, text_hash("original"))
    client.hdel(key, expiry_field)
    with pytest.raises(FoundationError) as error:
        await cache.get(SCOPE, text_hash("original"))
    assert error.value.code == ErrorCode.CONTRACT_VIOLATION
    assert client.hget(key, body_field) == b"original"


async def test_dependency_failure_is_not_reported_as_cache_miss():
    import redis

    with socket.socket() as unavailable:
        unavailable.bind(("127.0.0.1", 0))
        client = redis.Redis(
            host="127.0.0.1",
            port=unavailable.getsockname()[1],
            socket_timeout=0.1,
            socket_connect_timeout=0.1,
        )
        try:
            cache = make_cache(client, "test-unavailable")
            with pytest.raises(FoundationError) as error:
                await cache.get(SCOPE, text_hash("original"))
            assert error.value.code == ErrorCode.DEPENDENCY_UNAVAILABLE
        finally:
            client.close()
