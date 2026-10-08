"""Initial Remember admission uses real PG/Redis and the production tier boundary."""

from types import SimpleNamespace

import pytest
from test_operate_memory_record import publication as publication
from test_operate_memory_record import read_record
from test_two_tier_operate import azure_redis as azure_redis
from test_two_tier_operate import dsns as dsns
from test_two_tier_operate import execution as execution
from test_two_tier_operate import intent

from aether_agent_memory.operate.basic.body_cache import TieredBodyCache
from aether_agent_memory.operate.contracts.models import Tier
from aether_agent_memory.remember.basic.service import memory_ref
from aether_agent_memory.remember.basic.sources import SourceAccess
from aether_agent_memory.remember.basic.temporal_stages import CorrectionStages
from aether_agent_memory.remember.contracts.models import CorrectionRequest, MemoryKind, SourceInput
from aether_agent_memory.runtime.temporal.activities import StageContext


async def save_cache(p):
    p.reader.bodies.cache = TieredBodyCache(p.executor, p.cache)
    await p.reader.admit_save_cache(
        p.ctx,
        {"working_text": p.item.content},
        SimpleNamespace(memories=(p.item.ref,)),
    )


async def test_initial_working_cache_registers_replica_and_memory_location(publication):
    p = publication
    await save_cache(p)
    assert p.cache.get_sync(p.item.ref.scope, p.item.content_hash) == p.item.content
    assert p.executor.inspect(p.item.ref, p.item.content_hash)
    assert p.executor.observe_sync(p.ctx, p.item.ref, "original").tier == Tier.HOT
    location = read_record(p)["cache_location"]
    assert location == p.cache.describe_location(
        p.item.ref.scope,
        p.item.content_hash,
        generation=read_record(p)["body_location"]["generation"],
    ).model_dump(mode="json")
    assert location["provider_instance_id"] == p.cache.resource_id
    assert location["content_hash"] == p.item.content_hash
    assert location["namespace"] == p.cache.namespace


async def test_save_replay_and_authority_read_do_not_reverse_operate_cooling(publication):
    p = publication
    await save_cache(p)
    await p.executor.submit(p.ctx, intent(p.executor, p.ctx, p.item, Tier.COLD, "cool"))
    assert not await p.reader.bodies.cache.put(p.item.ref.scope, p.item.content)
    await save_cache(p)
    assert p.cache.get_sync(p.item.ref.scope, p.item.content_hash) is None
    assert not p.executor.inspect(p.item.ref, p.item.content_hash)
    assert read_record(p)["cache_location"] is None

    async def get_object(key):
        return p.objects.get(key)

    p.reader.bodies.p2.get_object = get_object
    body = await p.reader.read_body(p.ctx, p.item.ref)
    assert body.content == p.item.content and body.path == "authority"
    assert p.cache.get_sync(p.item.ref.scope, p.item.content_hash) is None


async def test_failed_initial_cache_can_retry_when_capacity_recovers(publication):
    p = publication
    original_capacity = p.executor.capacity
    p.executor.capacity = 1
    await save_cache(p)
    assert p.cache.get_sync(p.item.ref.scope, p.item.content_hash) is None
    assert read_record(p)["cache_location"] is None
    p.executor.capacity = original_capacity
    await save_cache(p)
    assert p.executor.inspect(p.item.ref, p.item.content_hash)
    assert read_record(p)["cache_location"] is not None


async def test_cooling_during_initial_write_fences_late_write_and_replay(publication, monkeypatch):
    p = publication
    original = p.cache.put_sync

    def cool_during_write(scope, text):
        p.executor.purge(p.item.ref, permanent=False, ctx=p.ctx)
        return original(scope, text)

    monkeypatch.setattr(p.cache, "put_sync", cool_during_write)
    await save_cache(p)
    assert not p.executor.inspect(p.item.ref, p.item.content_hash)
    assert p.cache.get_sync(p.item.ref.scope, p.item.content_hash) is None
    monkeypatch.setattr(p.cache, "put_sync", original)
    await save_cache(p)
    assert p.cache.get_sync(p.item.ref.scope, p.item.content_hash) is None
    assert read_record(p)["cache_location"] is None


async def test_cooling_before_memory_record_publication_cannot_restore_cache_pointer(
    publication, monkeypatch
):
    p = publication
    original = p.reader.bodies.admit_initial

    async def cool_before_record(memory, ctx):
        result = await original(memory, ctx)
        p.executor.purge(memory.ref, permanent=False, ctx=ctx)
        return result

    monkeypatch.setattr(p.reader.bodies, "admit_initial", cool_before_record)
    await save_cache(p)
    assert p.cache.get_sync(p.item.ref.scope, p.item.content_hash) is None
    assert read_record(p)["cache_location"] is None
    with p.host.uow.transaction() as tx:
        current = tx.read("remember_cache_admission", p.item.ref.memory_id)
        history = tx.read("remember_cache_admission_history", p.reader.refkey(p.item.ref))
    assert current["state"] == history["state"] == "not_admitted"


@pytest.mark.parametrize("permanent", [False, True])
async def test_initial_admission_does_not_override_existing_executor_fence(publication, permanent):
    p = publication
    p.executor.ensure(p.item, p.ctx)
    p.executor.purge(p.item.ref, permanent=permanent, ctx=p.ctx)
    await save_cache(p)
    assert p.cache.get_sync(p.item.ref.scope, p.item.content_hash) is None
    assert read_record(p)["cache_location"] is None


@pytest.fixture
def working_publication(publication):
    p = publication
    p.reader.tokenizer = SimpleNamespace(count=len, identifier="test_chars")
    p.reader.source_access = SourceAccess(p.reader)
    p.reader.max_input_bytes = p.reader.policy.max_input_bytes
    p.reader.processing_seconds = p.reader.policy.processing_seconds
    p.item = p.item.model_copy(update={"kind": MemoryKind.WORKING})
    with p.host.uow.transaction() as tx:
        p.reader.put(tx, p.item)

    async def get_object(key):
        return p.objects.get(key)

    async def put_object(key, content):
        p.objects[key] = content

    p.reader.bodies.p2.get_object = get_object
    p.reader.bodies.p2.put_object = put_object
    return p


@pytest.mark.parametrize(
    "text", ["修正后的偏好是清淡食物。", "完整的长Working更正。" * 1000], ids=["short", "long"]
)
@pytest.mark.parametrize("entrypoint", ["direct", "temporal_stage"])
async def test_working_correction_admits_exact_new_version_and_replay_cannot_reheat(
    working_publication, text, entrypoint, monkeypatch
):
    p = working_publication
    await save_cache(p)
    old_ref = p.item.ref
    ctx = p.host.identity.context("alice", operation_id="cache-correction", timeout_seconds=300)
    request = CorrectionRequest(
        expected_version=1,
        content=text,
        reason="test correction",
        source=SourceInput(
            kind="conversation",
            external_id="corrected",
            external_version="1",
            occurred_at=p.host.identity.clock(),
        ),
    )
    if entrypoint == "direct":
        receipt = await p.reader.correct_async(ctx, old_ref.memory_id, request)

        async def replay():
            return await p.reader.correct_async(ctx, old_ref.memory_id, request)
    else:
        await p.reader.bodies.persist(ctx, old_ref.scope, text)
        receipt = p.reader.correct(ctx, old_ref.memory_id, request)
        stage = CorrectionStages(None, p.reader)
        # This test checks the real command-stage -> domain handoff; Azure covers
        # Temporal dispatch itself. Receipts contain the real committed PG version.
        monkeypatch.setattr(
            StageContext, "current", staticmethod(lambda: SimpleNamespace(context=ctx))
        )
        monkeypatch.setattr(stage, "load", lambda name: receipt.model_dump(mode="json"))
        monkeypatch.setattr(
            stage, "ref", lambda name: memory_ref(receipt.memories[0], versioned=True)
        )
        await stage.admit_cache(None)

        async def replay():
            return await stage.admit_cache(None)

    new_ref = receipt.memories[0]
    assert new_ref.version == old_ref.version + 1
    with p.host.uow.transaction() as tx:
        updated = p.reader.current(tx, new_ref.memory_id)
        record = tx.get(memory_ref(new_ref, versioned=True))
    assert updated.content == text
    assert p.cache.get_sync(new_ref.scope, updated.content_hash) == text
    assert p.executor.inspect(new_ref, updated.content_hash)
    assert record["cache_location"]["content_hash"] == updated.content_hash
    old = await p.reader.read_body(ctx, old_ref)
    assert old.outcome != "read" and old.content is None
    # Existing cleanup is version-bounded: purging v1 cannot remove v2's copy.
    p.executor.purge(old_ref, permanent=False, ctx=ctx)
    assert p.executor.inspect(new_ref, updated.content_hash)
    p.executor.purge(new_ref, permanent=False, ctx=ctx)
    await replay()
    assert p.cache.get_sync(new_ref.scope, updated.content_hash) is None
    with p.host.uow.transaction() as tx:
        assert tx.get(memory_ref(new_ref, versioned=True))["cache_location"] is None


async def test_remember_and_operate_publish_identical_cache_locations(publication):
    p = publication
    await save_cache(p)
    remembered = read_record(p)
    evidence = p.service.capture_evidence(p.item.ref)
    observed = await p.executor.observe(p.ctx, p.item.ref, "original")
    assert p.service.sync_observation(p.ctx, observed, evidence) == "synced"
    assert read_record(p) == remembered
    # Cold -> hot publication also uses exactly the same public address contract.
    await p.service.execute(p.ctx, intent(p.executor, p.ctx, p.item, Tier.COLD, "unify-cold"))
    assert read_record(p)["cache_location"] is None
    await p.service.execute(p.ctx, intent(p.executor, p.ctx, p.item, Tier.HOT, "unify-hot"))
    assert read_record(p) == remembered
