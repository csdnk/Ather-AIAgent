"""Initial Remember admission uses real PG/Redis and the production tier boundary."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

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
from aether_agent_memory.runtime.contracts.models import ErrorCode
from aether_agent_memory.runtime.foundation.common import FoundationError
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
    "text",
    ["修正后的偏好是清淡食物。", "\n".join(f"第{i}个项目的负责人为成员{i}。" for i in range(600))],
    ids=["short", "long"],
)
@pytest.mark.parametrize("entrypoint", ["direct", "prepare", "commit"])
async def test_working_correction_rejects_before_body_cache_or_version_changes(
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
    before, objects = read_record(p), dict(p.objects)
    persist = AsyncMock(side_effect=AssertionError("rejected correction must not persist a body"))
    admission = AsyncMock(side_effect=AssertionError("rejected correction must not admit cache"))
    monkeypatch.setattr(p.reader.bodies, "persist", persist)
    monkeypatch.setattr(p.reader.bodies, "admit_initial", admission)
    for _ in range(2):
        with pytest.raises(FoundationError, match="Working originals are immutable") as error:
            if entrypoint == "direct":
                await p.reader.correct_async(ctx, old_ref.memory_id, request)
            elif entrypoint == "prepare":
                p.reader.prepare_correction(ctx, old_ref.memory_id, request)
            else:
                # A historical worker may already have completed preparation;
                # the commit entry point independently rejects Working updates.
                p.reader.correct(ctx, old_ref.memory_id, request)
        assert error.value.code == ErrorCode.INVALID_ARGUMENT
    persist.assert_not_awaited()
    admission.assert_not_awaited()
    assert p.objects == objects and read_record(p) == before
    with p.host.uow.transaction() as tx:
        assert p.reader.current(tx, old_ref.memory_id).ref == old_ref
        next_ref = old_ref.model_copy(update={"version": old_ref.version + 1})
        assert tx.get(memory_ref(next_ref, versioned=True)) is None
    assert p.cache.get_sync(old_ref.scope, p.item.content_hash) == p.item.content
    assert p.executor.inspect(old_ref, p.item.content_hash)
    # The old persisted Temporal terminal stage remains a no-op. Even a stale
    # Working correction receipt cannot reheat content after Operate cooling.
    await p.service.execute(ctx, intent(p.executor, ctx, p.item, Tier.COLD, "cool-original"))
    assert read_record(p)["cache_location"] is None
    stage = CorrectionStages(None, p.reader)
    monkeypatch.setattr(StageContext, "current", staticmethod(lambda: SimpleNamespace(context=ctx)))
    monkeypatch.setattr(stage, "ref", lambda name: memory_ref(old_ref, versioned=True))
    await stage.admit_cache(None)
    assert p.cache.get_sync(old_ref.scope, p.item.content_hash) is None
    assert read_record(p)["cache_location"] is None
    admission.assert_not_awaited()


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
