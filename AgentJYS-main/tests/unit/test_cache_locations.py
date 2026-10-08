"""Address-only contract tests: no Redis server, I/O or admission is implied."""

import json
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from aether_agent_memory.operate.basic.body_cache import TieredBodyCache
from aether_agent_memory.operate.basic.record_sync import MemoryRecordSync
from aether_agent_memory.operate.contracts.models import PlacementObservation
from aether_agent_memory.remember.basic.pipeline import RememberPipeline
from aether_agent_memory.remember.basic.policy import RememberPolicy
from aether_agent_memory.remember.basic.service import memory_ref
from aether_agent_memory.remember.contracts.foundation import MemoryRecord
from aether_agent_memory.remember.contracts.models import MemoryRef, SourceRef
from aether_agent_memory.runtime.contracts.foundation import ResourceLocation
from aether_agent_memory.runtime.contracts.models import Scope
from aether_agent_memory.runtime.foundation.common import FoundationError
from aether_agent_memory.runtime.foundation.requests import text_hash
from aether_agent_memory.runtime.foundation.transactions import StorageTransaction
from aether_agent_memory.runtime.storage.cache import describe_cache_location
from aether_agent_memory.runtime.storage.redis_cache import RedisCache
from recall_records import InMemoryRecallRecords

SCOPE = Scope(tenant_id="tenant", application_id="app", user_id="alice", agent_id="agent")
DIGEST = text_hash("body")


def cache(*, namespace="test", **options):
    client = SimpleNamespace(
        connection_pool=SimpleNamespace(
            connection_kwargs={"host": "redis.example", "port": 6379, "db": 0, **options}
        )
    )
    return RedisCache(client, RememberPolicy(), namespace=namespace)


def test_shared_and_tiered_cache_return_identical_canonical_resource_locations():
    redis = cache()
    bridge = TieredBodyCache(SimpleNamespace(), redis)
    expected = redis.describe_location(SCOPE, DIGEST, generation="generation-2")
    assert describe_cache_location(redis, SCOPE, DIGEST, generation="generation-2") == expected
    assert describe_cache_location(bridge, SCOPE, DIGEST, generation="generation-2") == expected
    key, field, expiry = redis.keys(SCOPE, DIGEST)
    assert expected.model_dump(mode="json") == {
        "kind": "cache",
        "provider_id": "redis",
        "provider_instance_id": redis.resource_id,
        "namespace": "test",
        "generation": "generation-2",
        "content_hash": DIGEST,
        "object_key": json.dumps(
            {"encoding": "redis_hash_v1", "key": key, "field": field, "expiry_field": expiry},
            sort_keys=True,
            separators=(",", ":"),
        ),
    }
    assert expected.provider_instance_id != expected.namespace


@pytest.mark.parametrize(
    "field", ["tenant_id", "application_id", "user_id", "agent_id", "session_id", "task_id"]
)
def test_cache_address_respects_every_scope_dimension(field):
    redis = cache()
    first = redis.describe_location(SCOPE, DIGEST, generation="g")
    other = redis.describe_location(
        SCOPE.model_copy(update={field: "other"}), DIGEST, generation="g"
    )
    assert first.object_key != other.object_key


def test_instance_environment_generation_and_hash_are_not_conflated():
    first = cache().describe_location(SCOPE, DIGEST, generation="g")
    endpoint = cache(host="other.example").describe_location(SCOPE, DIGEST, generation="g")
    database = cache(db=1).describe_location(SCOPE, DIGEST, generation="g")
    environment = cache(namespace="other").describe_location(SCOPE, DIGEST, generation="g")
    assert (
        len(
            {
                first.provider_instance_id,
                endpoint.provider_instance_id,
                database.provider_instance_id,
            }
        )
        == 3
    )
    assert environment.provider_instance_id == first.provider_instance_id
    assert environment.object_key != first.object_key
    new = cache().describe_location(SCOPE, text_hash("changed"), generation="next")
    assert new.generation == "next" and new.content_hash == text_hash("changed")
    assert new.object_key != first.object_key


@pytest.mark.parametrize("provider", [None, SimpleNamespace(), TieredBodyCache(SimpleNamespace())])
def test_non_addressable_cache_never_fabricates_a_redis_location(provider):
    assert describe_cache_location(provider, SCOPE, DIGEST, generation="g") is None


def test_description_rejects_invalid_digest_without_accessing_redis():
    with pytest.raises(FoundationError, match="invalid cached content hash"):
        cache().describe_location(SCOPE, "bad-digest", generation="g")


@pytest.fixture
def publication():
    """Real publication code, explicitly fake admission/registration evidence and I/O."""
    records = InMemoryRecallRecords()

    @contextmanager
    def transaction():
        with records.transaction() as raw:
            yield StorageTransaction(raw, "test-cursor")

    redis = cache()
    memory = MemoryRef(scope=SCOPE, memory_id="memory", version=1)
    record = MemoryRecord(
        ref=memory,
        revision=1,
        relations_revision=1,
        kind="working",
        status="active",
        body_location=ResourceLocation(
            kind="body",
            provider_id="ceph",
            provider_instance_id="ceph",
            namespace="test",
            object_key="body-key",
            generation="g2",
            content_hash=DIGEST,
        ),
        body_chars=4,
        sources=(
            SourceRef(
                source_id="source", source_version=1, content_hash=DIGEST, locator="source-key"
            ),
        ),
        projection_state="pending",
        created_at="2026-10-08T10:00:00.000Z",
    )
    ref = memory_ref(memory, versioned=True)
    with transaction() as tx:
        tx.put_if_revision(ref, record.model_dump(mode="json"), None)
        tx.write("remember_current", memory.memory_id, ref.model_dump(mode="json"))
    reader = object.__new__(RememberPipeline)
    reader.uow = SimpleNamespace(transaction=transaction)
    reader.identity = SimpleNamespace(clock=lambda: record.created_at)
    reader.policy = RememberPolicy()
    reader.final_guard = lambda *args: SimpleNamespace(items=(SimpleNamespace(decision="allowed"),))
    executor = SimpleNamespace(
        provider_id="redis_hot_cache",
        mode="real",
        policy_managed=True,
        instance_id="executor",
        cache=redis,
        registration_current=lambda *args: True,
        key=lambda ref: MemoryRecordSync.key(ref),
        table=lambda name: "unit_" + name,
    )
    reader.bodies = SimpleNamespace(
        cache=TieredBodyCache(executor, redis), admit=AsyncMock(return_value="cached")
    )
    with transaction() as tx:
        tx.write("unit_settings", "instance", "executor")
        tx.write(
            "unit_copies",
            executor.key(memory),
            {"memory": memory.model_dump(mode="json"), "hash": DIGEST, "tier": "hot"},
        )
    return SimpleNamespace(
        reader=reader,
        executor=executor,
        transaction=transaction,
        memory=memory,
        ref=ref,
        original=record,
    )


@pytest.mark.parametrize("bridge", [False, True])
async def test_real_remember_and_operate_publication_share_exact_location(publication, bridge):
    p = publication
    if not bridge:
        p.reader.bodies.cache = p.executor.cache
    await p.reader.admit_verified_cache(None, p.memory, "body")
    sync = MemoryRecordSync(p.executor)
    with p.transaction() as tx:
        remembered = tx.get(p.ref)
        revision = tx.revision(p.ref)
        assert remembered["cache_location"] == p.executor.cache.describe_location(
            SCOPE, DIGEST, generation="g2"
        ).model_dump(mode="json")
        observed = PlacementObservation(
            memory=p.memory,
            representation_id="original",
            provider_instance_id="executor",
            tier="hot",
            epoch=1,
            readable=True,
            content_hash=DIGEST,
            observed_at=p.original.created_at,
        )
        assert sync.publish(tx, observed, sync.evidence(tx, p.memory)) == "synced"
        assert tx.get(p.ref) == remembered
        assert tx.revision(p.ref) == revision  # Operate does not churn an identical address.


async def test_remember_cannot_publish_a_descriptor_after_registration_is_revoked(publication):
    p = publication
    p.executor.registration_current = lambda *args: False
    await p.reader.admit_verified_cache(None, p.memory, "body")
    with p.transaction() as tx:
        assert tx.get(p.ref)["cache_location"] is None
        assert tx.read("remember_cache_admission", p.memory.memory_id)["state"] == "not_admitted"


async def test_unadmitted_copy_does_not_publish_an_address(publication):
    p = publication
    p.reader.bodies.admit.return_value = "not_admitted"
    await p.reader.admit_verified_cache(None, p.memory, "body")
    with p.transaction() as tx:
        assert tx.get(p.ref)["cache_location"] is None
