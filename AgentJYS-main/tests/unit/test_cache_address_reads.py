"""Exercise the real address reader; only the Redis wire driver is replaced."""

import json
import threading
from types import SimpleNamespace

import pytest
from redis.exceptions import ConnectionError, ResponseError, TimeoutError

from aether_agent_memory.remember.basic.policy import RememberPolicy
from aether_agent_memory.runtime.contracts.foundation import ResourceLocation
from aether_agent_memory.runtime.contracts.models import ErrorCode, Scope
from aether_agent_memory.runtime.foundation.common import FoundationError
from aether_agent_memory.runtime.storage import cache as cache_contracts
from aether_agent_memory.runtime.storage.redis_cache import RedisCache

SCOPE = Scope(tenant_id="tenant", application_id="app", user_id="alice", agent_id="agent")
DIGEST = "230d8358dc8e8890b4c58deeb62912ee2f20357ae92a5cc861b98e68fe31acb5"
KEY = "aether:test:body:{test:7a69c0b0830befbb919668bf5d4911fc2b209628ae18bb619989668723b3598c}"
AUTHORITY = ResourceLocation(
    kind="body",
    provider_id="ceph",
    provider_instance_id="cold-instance",
    namespace="test",
    object_key="cold-body-key",
    generation="generation-2",
    content_hash=DIGEST,
)


class RedisDriver:
    """Return server replies without replacing address, decode or error handling."""

    def __init__(self, reply=b"body", *, timeout=2.5):
        self.reply = reply
        self.calls = []
        self.thread_id = None
        self.connection_pool = SimpleNamespace(
            connection_kwargs={
                "host": "redis.example",
                "port": 6379,
                "db": 0,
                "socket_timeout": timeout,
            }
        )

    def eval(self, *args):
        self.calls.append(args)
        self.thread_id = threading.get_ident()
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply


@pytest.fixture
def address_reader():
    driver = RedisDriver()
    cache = RedisCache(driver, RememberPolicy(), namespace="test")
    location = cache.describe_location(SCOPE, DIGEST, generation=AUTHORITY.generation)
    return cache, driver, location


def test_redis_exposes_independent_runtime_checkable_read_capability(address_reader):
    cache, _, _ = address_reader
    protocol = getattr(cache_contracts, "CacheLocationReader", None)
    assert protocol is not None, "address-only cache read capability is missing"
    assert isinstance(cache, protocol)


@pytest.mark.parametrize("reply", [b"body", "body"])
async def test_valid_address_reads_verified_body_using_ttl_script_in_worker(address_reader, reply):
    cache, driver, location = address_reader
    driver.reply = reply
    assert await cache.read_location(SCOPE, location, AUTHORITY) == "body"
    assert driver.calls == [(RedisCache._READ, 1, KEY, "b:" + DIGEST, "e:" + DIGEST)]
    assert driver.thread_id != threading.get_ident()


async def test_json_field_order_and_whitespace_do_not_change_valid_address(address_reader):
    cache, driver, location = address_reader
    fields = json.loads(location.object_key)
    location = location.model_copy(
        update={"object_key": json.dumps(dict(reversed(list(fields.items()))), indent=2)}
    )
    assert await cache.read_location(SCOPE, location, AUTHORITY) == "body"
    assert len(driver.calls) == 1


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("kind", "body"),
        ("provider_id", "unknown"),
        ("provider_instance_id", "other-instance"),
        ("namespace", "other-environment"),
        ("generation", "generation-1"),
        ("content_hash", "0" * 64),
    ],
)
async def test_untrusted_address_binding_is_rejected_before_redis(address_reader, field, value):
    cache, driver, location = address_reader
    location = location.model_copy(update={field: value})
    with pytest.raises(FoundationError) as failure:
        await cache.read_location(SCOPE, location, AUTHORITY)
    assert failure.value.code == ErrorCode.CONTRACT_VIOLATION
    assert driver.calls == []


@pytest.mark.parametrize(
    "field", ["tenant_id", "application_id", "user_id", "agent_id", "session_id", "task_id"]
)
async def test_address_for_other_scope_is_rejected_before_redis(address_reader, field):
    cache, driver, location = address_reader
    scope = SCOPE.model_copy(update={field: "other"})
    with pytest.raises(FoundationError) as failure:
        await cache.read_location(scope, location, AUTHORITY)
    assert failure.value.code == ErrorCode.CONTRACT_VIOLATION
    assert driver.calls == []


@pytest.mark.parametrize(
    ("field", "value"),
    [("generation", "generation-3"), ("content_hash", "f" * 64)],
)
async def test_address_is_bound_to_authority_version_and_hash(address_reader, field, value):
    cache, driver, location = address_reader
    authority = AUTHORITY.model_copy(update={field: value})
    with pytest.raises(FoundationError) as failure:
        await cache.read_location(SCOPE, location, authority)
    assert failure.value.code == ErrorCode.CONTRACT_VIOLATION
    assert driver.calls == []


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("key", "other-hash"),
        ("field", "b:" + "0" * 64),
        ("expiry_field", "e:" + "0" * 64),
        ("encoding", "redis_hash_v2"),
        ("encoding", None),
        ("key", [KEY]),
        ("endpoint", "redis://other.example/1"),
        ("password", "must-never-be-used"),
    ],
)
async def test_address_fields_require_exact_known_schema(address_reader, field, value):
    cache, driver, location = address_reader
    fields = json.loads(location.object_key)
    fields[field] = value
    location = location.model_copy(update={"object_key": json.dumps(fields)})
    with pytest.raises(FoundationError) as failure:
        await cache.read_location(SCOPE, location, AUTHORITY)
    assert failure.value.code == ErrorCode.CONTRACT_VIOLATION
    assert driver.calls == []


@pytest.mark.parametrize("field", ["key", "field", "expiry_field", "encoding"])
async def test_incomplete_address_is_rejected_before_redis(address_reader, field):
    cache, driver, location = address_reader
    fields = json.loads(location.object_key)
    del fields[field]
    location = location.model_copy(update={"object_key": json.dumps(fields)})
    with pytest.raises(FoundationError) as failure:
        await cache.read_location(SCOPE, location, AUTHORITY)
    assert failure.value.code == ErrorCode.CONTRACT_VIOLATION
    assert driver.calls == []


@pytest.mark.parametrize("object_key", ["bucket/field", "{", "null", "[]", '"body-key"'])
async def test_legacy_and_non_object_addresses_are_rejected_before_redis(
    address_reader, object_key
):
    cache, driver, location = address_reader
    location = location.model_copy(update={"object_key": object_key})
    with pytest.raises(FoundationError) as failure:
        await cache.read_location(SCOPE, location, AUTHORITY)
    assert failure.value.code == ErrorCode.CONTRACT_VIOLATION
    assert driver.calls == []


async def test_duplicate_address_fields_are_rejected_before_redis(address_reader):
    cache, driver, location = address_reader
    location = location.model_copy(
        update={"object_key": '{"encoding":"old",' + location.object_key[1:]}
    )
    with pytest.raises(FoundationError) as failure:
        await cache.read_location(SCOPE, location, AUTHORITY)
    assert failure.value.code == ErrorCode.CONTRACT_VIOLATION
    assert driver.calls == []


async def test_nil_from_ttl_script_is_a_cache_miss(address_reader):
    cache, driver, location = address_reader
    driver.reply = None
    assert await cache.read_location(SCOPE, location, AUTHORITY) is None
    assert driver.calls == [(RedisCache._READ, 1, KEY, "b:" + DIGEST, "e:" + DIGEST)]


@pytest.mark.parametrize(
    "reply",
    [b"wrong-body", b"\xff", ResponseError("CACHE_CORRUPT expiry"), ResponseError("WRONGTYPE")],
)
async def test_corruption_is_distinct_from_cache_miss(address_reader, reply):
    cache, driver, location = address_reader
    driver.reply = reply
    with pytest.raises(FoundationError) as failure:
        await cache.read_location(SCOPE, location, AUTHORITY)
    assert failure.value.code == ErrorCode.CONTRACT_VIOLATION
    assert len(driver.calls) == 1


@pytest.mark.parametrize("reply", [ConnectionError("offline"), TimeoutError("timed out")])
async def test_dependency_failure_is_distinct_from_cache_miss(address_reader, reply):
    cache, driver, location = address_reader
    driver.reply = reply
    with pytest.raises(FoundationError) as failure:
        await cache.read_location(SCOPE, location, AUTHORITY)
    assert failure.value.code == ErrorCode.DEPENDENCY_UNAVAILABLE
    assert len(driver.calls) == 1


@pytest.mark.parametrize(
    ("configured", "expected"),
    [
        (2.5, 2.5),
        (3, 3.0),
        (None, None),
        (0, None),
        (-1, None),
        (float("inf"), None),
        (float("nan"), None),
        (True, None),
        ("2", None),
    ],
)
def test_cache_wait_budget_uses_only_finite_positive_client_timeout(configured, expected):
    cache = RedisCache(RedisDriver(timeout=configured), RememberPolicy(), namespace="test")
    assert cache.read_timeout_seconds == expected
