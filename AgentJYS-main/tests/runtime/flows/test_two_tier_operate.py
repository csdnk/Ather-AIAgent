"""Two-tier behavior: real PostgreSQL/Redis, explicitly stubbed Ceph authority.

These tests verify orchestration/receipts; they are not live Ceph acceptance.
"""

from types import SimpleNamespace

import pytest
from test_azure_cache_executor import azure_redis as azure_redis
from test_azure_cache_executor import dsns as dsns
from test_azure_cache_executor import execution as execution

from aether_agent_memory.operate.basic.body_cache import TieredBodyCache
from aether_agent_memory.operate.basic.continuous import ContinuousOperate, seconds
from aether_agent_memory.operate.contracts.models import (
    ActionIntent,
    PlacementDecision,
    SchedulingInput,
    Tier,
)
from aether_agent_memory.operate.standalone.policy import Settings, accumulate, evaluate, next_delay
from aether_agent_memory.recall.basic.service import Recall
from aether_agent_memory.remember.basic.content import Bodies
from aether_agent_memory.remember.basic.pipeline import RememberPipeline
from aether_agent_memory.remember.basic.policy import RememberPolicy
from aether_agent_memory.remember.basic.service import memory_ref
from aether_agent_memory.remember.contracts.foundation import MemoryRecord
from aether_agent_memory.runtime.contracts.models import ErrorCode, EventEnvelope, Permission
from aether_agent_memory.runtime.foundation.common import FoundationError, now
from aether_agent_memory.runtime.storage.redis_executor import RedisExecutor


@pytest.fixture
def two_tier(execution):
    host, memories, ctx, item, cache, _ = execution
    host.events.register_type("recall.access", Recall.validate_event, permission=Permission.READ)
    objects = {item.content_hash: item.content}
    calls = []

    def authority(context, ref):
        calls.append(ref)
        text = objects.get(item.content_hash)
        if text is None:
            raise FoundationError(ErrorCode.DEPENDENCY_UNAVAILABLE, "Ceph test fault")
        return item.model_copy(update={"content": text})

    def make():
        return RedisExecutor(host.uow, host.identity, memories, cache, authority_reader=authority)

    return host, memories, ctx, item, cache, make, objects, calls


def intent(executor, ctx, item, target, action_id):
    observed = executor.observe_sync(ctx, item.ref, "original")
    return ActionIntent(
        action_id=action_id,
        decision=PlacementDecision(
            decision_id=action_id,
            memory=item.ref,
            current_tier=observed.tier,
            target_tier=target,
            outcome="promote" if target == Tier.HOT else "demote",
            reason="two-tier test",
            policy_version="ceph_redis_heat_v2",
            storage_watermark=1,
            access_watermark=12,
        ),
        representation_id="original",
        content_hash=item.content_hash,
        provider_id=executor.provider_id,
        provider_instance_id=executor.instance_id,
        expected_epoch=observed.epoch,
        provider_mode="real",
        created_at=now(),
    )


async def test_cold_hot_cold_keeps_original_and_has_complete_evidence(two_tier):
    host, memories, ctx, item, cache, make, objects, _ = two_tier
    executor = make()
    service = ContinuousOperate(
        host.uow, host.identity, host.tasks, host.events, memories, memories, executor
    )
    assert executor.observe_sync(ctx, item.ref, "original").tier == Tier.COLD
    assert cache.get_sync(item.ref.scope, item.content_hash) is None
    assert executor.supported_moves == ("promote", "demote")
    for target, name in [(Tier.HOT, "heat"), (Tier.COLD, "cool")]:
        action = intent(executor, ctx, item, target, name)
        record = await service.execute(ctx, action)
        assert record.state == "succeeded"
        assert record.feedback.read_proof.readable
        assert (await service.reconcile(ctx, action.action_id)).state == "succeeded"
        assert record.feedback.observation.tier == target
        assert objects == {item.content_hash: item.content}
    assert cache.get_sync(item.ref.scope, item.content_hash) is None
    assert make().query_sync(ctx, "cool").state == "succeeded"
    with host.uow.transaction() as tx:
        assert len(tx.rows(executor.table("actions"))) == 2
        assert len(tx.rows("operate_actions")) == 2


@pytest.mark.parametrize("fault", ["missing", "corrupt"])
async def test_ceph_failure_does_not_evict_hot_replica(two_tier, fault):
    _, _, ctx, item, cache, make, objects, _ = two_tier
    executor = make()
    await executor.submit(ctx, intent(executor, ctx, item, Tier.HOT, "heat"))
    cooling = intent(executor, ctx, item, Tier.COLD, "cool")
    if fault == "missing":
        objects.clear()
    else:
        objects[item.content_hash] = "wrong bytes"
    with pytest.raises(FoundationError):
        await executor.submit(ctx, cooling)
    assert cache.get_sync(item.ref.scope, item.content_hash) == item.content
    # The same durable intent resumes after the dependency is repaired.
    objects[item.content_hash] = item.content
    assert (await make().query(ctx, "cool")).state == "succeeded"
    assert cache.get_sync(item.ref.scope, item.content_hash) is None


@pytest.mark.parametrize("target", [Tier.HOT, Tier.COLD])
async def test_lost_redis_reply_recovers_original_action_after_restart(
    two_tier, monkeypatch, target
):
    host, _, ctx, item, cache, make, _, _ = two_tier
    executor = make()
    if target == Tier.COLD:
        await executor.submit(ctx, intent(executor, ctx, item, Tier.HOT, "setup"))
    action = intent(executor, ctx, item, target, "lost-reply")
    method = "put_sync" if target == Tier.HOT else "delete_sync"
    original = getattr(cache, method)

    def lost(*args):
        original(*args)
        raise ConnectionError("response lost after applying the Redis operation")

    monkeypatch.setattr(cache, method, lost)
    with pytest.raises((FoundationError, ConnectionError)):
        await executor.submit(ctx, action)
    monkeypatch.setattr(cache, method, original)
    rebuilt = make()
    result = await rebuilt.query(ctx, action.action_id)
    assert result.state == "succeeded" and result.action_id == action.action_id
    assert (await rebuilt.verify_read(ctx, action)).readable
    with host.uow.transaction() as tx:
        assert len([k for k, _ in tx.rows(rebuilt.table("actions")) if k == "lost-reply"]) == 1


async def test_duplicate_action_does_not_write_twice_and_conflicting_binding_is_rejected(
    two_tier, monkeypatch
):
    _, _, ctx, item, cache, make, _, _ = two_tier
    executor = make()
    action = intent(executor, ctx, item, Tier.HOT, "same-action")
    calls = []
    put = cache.put_sync

    def counted(*args):
        calls.append(args)
        return put(*args)

    monkeypatch.setattr(cache, "put_sync", counted)
    assert (await executor.submit(ctx, action)).state == "succeeded"
    assert (await make().submit(ctx, action)).state == "succeeded"
    assert len(calls) == 1
    with pytest.raises(FoundationError) as error:
        await executor.submit(ctx, action.model_copy(update={"content_hash": "a" * 64}))
    assert error.value.code == ErrorCode.IDEMPOTENCY_CONFLICT


async def test_full_redis_defers_heating_but_allows_cooling(two_tier):
    _, _, ctx, item, _, make, _, _ = two_tier
    executor = make()
    action = intent(executor, ctx, item, Tier.HOT, "full")
    executor.capacity = 1
    assert (await executor.submit(ctx, action)).reason == "cache_capacity_unavailable"
    executor.capacity = len(item.content.encode())
    assert (
        await executor.submit(ctx, intent(executor, ctx, item, Tier.HOT, "heat"))
    ).state == "succeeded"
    assert (await executor.resources(ctx)).available_bytes == 0
    assert (
        await executor.submit(ctx, intent(executor, ctx, item, Tier.COLD, "cool"))
    ).state == "succeeded"


async def test_other_tenant_cannot_observe_submit_or_query(two_tier):
    host, _, ctx, item, _, make, _, calls = two_tier
    executor = make()
    action = intent(executor, ctx, item, Tier.HOT, "alice-heat")
    await executor.submit(ctx, action)
    calls.clear()
    other = host.identity.context("bob", timeout_seconds=60)
    for operation in (
        lambda: executor.observe_sync(other, item.ref, "original"),
        lambda: executor.submit_sync(other, action),
        lambda: executor.query_sync(other, action.action_id),
    ):
        with pytest.raises(FoundationError):
            operation()
    assert not calls  # Rejected before any Ceph read.


async def test_read_through_does_not_recreate_a_cooled_replica(two_tier):
    _, _, ctx, item, cache, make, _, _ = two_tier
    executor = make()
    boundary = TieredBodyCache(executor, cache)
    assert not await boundary.put(item.ref.scope, item.content)
    assert await boundary.get(item.ref.scope, item.content_hash) is None
    await executor.submit(ctx, intent(executor, ctx, item, Tier.HOT, "heat"))
    assert await boundary.get(item.ref.scope, item.content_hash) == item.content
    await executor.submit(ctx, intent(executor, ctx, item, Tier.COLD, "cool"))
    assert not await boundary.put(item.ref.scope, item.content)
    assert await boundary.get(item.ref.scope, item.content_hash) is None


def test_policy_cools_without_free_cache_capacity_and_never_targets_warm(two_tier):
    host, memories, ctx, item, _, make, _, _ = two_tier
    service = ContinuousOperate(
        host.uow, host.identity, host.tasks, host.events, memories, memories, make()
    )
    sample = SchedulingInput(
        memory=item.ref,
        object_revision=1,
        storage_watermark=1,
        access_watermark=0,
        successful_reads=0,
        current_tier="hot",
        importance=0.2,
        available_bytes=0,
        content_bytes=len(item.content),
        coverage="complete",
        observed_at=now(),
        policy_version="test",
    )
    decision = service.decide(ctx, sample)
    assert (decision.outcome, decision.target_tier) == ("demote", Tier.COLD)
    legacy = service.decide(ctx, sample.model_copy(update={"current_tier": Tier.WARM}))
    assert legacy.outcome == "defer"  # Never silently relabel old warm data as Ceph.
    stats = service.stats(item.ref, item.content_hash, len(item.content), 0.2, None)
    start = seconds(now())
    for _ in range(16):
        accumulate(stats, start, start, service.settings)
    assert evaluate(stats, start, service.settings) == "hot"
    assert evaluate(stats, start + 36000, service.settings) == "cold"
    assert next_delay(stats, start + 36000, service.settings) is None
    assert not hasattr(Settings(), "warm_up")


def test_repeated_notifications_coalesce_while_new_version_and_cleanup_are_not_hidden(two_tier):
    host, memories, ctx, item, _, make, _, _ = two_tier
    service = ContinuousOperate(
        host.uow, host.identity, host.tasks, host.events, memories, memories, make()
    )
    with host.uow.transaction() as tx:
        first = service.enqueue(tx, ctx, item.ref, "one", cleanup=False, permanent=False)
        repeated = [
            service.enqueue(tx, ctx, item.ref, str(i), cleanup=False, permanent=False)
            for i in range(12)
        ]
        assert set(repeated) == {first}
        cleanup = service.enqueue(tx, ctx, item.ref, "delete", cleanup=True, permanent=True)
        assert cleanup != first
        revised = service.enqueue(
            tx,
            ctx,
            item.ref.model_copy(update={"version": 2}),
            "v2",
            cleanup=False,
            permanent=False,
        )
        assert revised != first
        row = tx.read("tasks", revised)
        row["record"]["state"] = "succeeded"
        tx.write("tasks", revised, row)
        next_task = service.enqueue(
            tx,
            ctx,
            item.ref.model_copy(update={"version": 2}),
            "after-completion",
            cleanup=False,
            permanent=False,
        )
        assert next_task != revised


def test_access_stats_survive_coalescing_and_duplicate_delivery(two_tier):
    host, memories, ctx, item, _, make, _, _ = two_tier
    service = ContinuousOperate(
        host.uow, host.identity, host.tasks, host.events, memories, memories, make()
    )
    # Use Recall's production event producer and the transactional event inbox.
    producer = SimpleNamespace(events=host.events, identity=host.identity)
    for index in range(13):
        with host.uow.transaction() as tx:
            Recall.access(producer, tx, ctx, f"recall-{index}", item.ref, "read")
    with host.uow.transaction() as tx:
        events = [EventEnvelope.model_validate(row["event"]) for _, row in tx.rows("outbox")]
    for event in events:
        with host.uow.transaction() as tx:
            assert host.events.consume(tx, "operate", event, service.consume)
    assert service.periodic("access-burst") == 1
    with host.uow.transaction() as tx:
        key = service.key(item.ref)
        view = tx.read("operate_views", key)
        heat = tx.read("operate_heat", key)
        assert view["successful_reads"] == view["access_watermark"] == 13
        assert heat["access_count"] == 13
        tasks = [row for _, row in tx.rows("tasks")]
        evaluations = [row for row in tasks if row["record"]["kind"] == "operate.evaluate"]
        assert len(evaluations) == 1
        # Re-publishing the same access must not count it again.
        Recall.access(producer, tx, ctx, "recall-12", item.ref, "read")
    for event in events:
        with host.uow.transaction() as tx:
            assert not host.events.consume(tx, "operate", event, service.consume)
    with host.uow.transaction() as tx:
        assert tx.read("operate_heat", key)["access_count"] == 13


async def test_legacy_running_warm_action_is_rejected_without_eviction(two_tier):
    host, _, ctx, item, cache, make, _, calls = two_tier
    executor = make()
    await executor.submit(ctx, intent(executor, ctx, item, Tier.HOT, "heat-before-legacy"))
    legacy = intent(executor, ctx, item, Tier.COLD, "legacy-warm")
    legacy = legacy.model_copy(
        update={
            "decision": legacy.decision.model_copy(
                update={
                    "target_tier": Tier.WARM,
                }
            )
        }
    )
    with host.uow.transaction() as tx:
        tx.write(
            executor.table("actions"),
            legacy.action_id,
            {
                "intent": legacy.model_dump(mode="json"),
                "feedback": {
                    "action_id": legacy.action_id,
                    "provider_instance_id": executor.instance_id,
                    "provider_operation_id": legacy.action_id,
                    "state": "running",
                    "observed_at": now(),
                    "reason": "legacy_pending",
                },
            },
        )
    calls.clear()
    receipt = await make().query(ctx, legacy.action_id)
    assert receipt.state == "failed"
    assert receipt.reason == "unsupported_tier_transition"
    assert calls == []
    assert cache.get_sync(item.ref.scope, item.content_hash) == item.content


def test_authority_boundary_bypasses_verified_cache_and_checks_actual_object(execution, tmp_path):
    host, memories, ctx, item, _, _ = execution
    objects = {}
    reads = []

    def get(key):
        reads.append(key)
        return objects.get(key)

    ceph = SimpleNamespace(
        binding=lambda: {
            "provider": "ceph",
            "resource": "test-only",
            "namespace": "test",
            "contract_version": 1,
        },
        get_object_sync=get,
    )
    bodies = Bodies(tmp_path / "bodies", RememberPolicy(), p2=ceph)
    location = bodies.location(item.ref.scope, item.content)
    record = MemoryRecord(
        ref=item.ref,
        revision=item.revision,
        relations_revision=item.revision,
        object_revision=item.object_revision,
        kind=item.kind,
        status=item.status,
        body_location=location,
        body_chars=len(item.content),
        sources=item.sources,
        projection_state=item.projection_state,
        created_at=item.created_at,
    )
    with host.uow.transaction() as tx:
        ref = memory_ref(item.ref, versioned=True)
        tx.put_if_revision(ref, record.model_dump(mode="json"), tx.revision(ref))
    reader = object.__new__(RememberPipeline)
    reader.uow, reader.identity, reader.bodies = host.uow, host.identity, bodies
    bodies.remember_verified(location, item.content)
    with pytest.raises(FoundationError):
        reader.read_authority(ctx, item.ref)
    objects[location.object_key] = item.content.encode()
    assert reader.read_authority(ctx, item.ref).content == item.content
    objects[location.object_key] = b"corrupt"
    with pytest.raises(FoundationError):
        reader.read_authority(ctx, item.ref)
    assert len(reads) == 3
