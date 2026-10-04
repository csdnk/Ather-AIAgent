"""Real PG/Redis executor tests; Remember fixtures do not claim Ceph acceptance."""

import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager

import pytest
from test_postgres_observability import dsns as dsns
from test_postgres_observability import open_host, people

from aether_agent_memory.operate.contracts.models import ActionIntent, PlacementDecision, Tier
from aether_agent_memory.remember.basic.policy import RememberPolicy
from aether_agent_memory.remember.basic.service import Remember
from aether_agent_memory.runtime.contracts.models import ErrorCode
from aether_agent_memory.runtime.foundation.common import FoundationError, now
from aether_agent_memory.runtime.storage.redis_cache import RedisCache
from azure_storage_support import azure_redis as azure_redis


@pytest.fixture
def execution(tmp_path, dsns, azure_redis):
    client, namespace = azure_redis
    host = open_host(tmp_path, dsns)
    people(host)
    # Real Remember authorization/lifecycle code, with explicitly seeded PG facts.
    memories = Remember(host.uow, host.identity, host.tasks, host.events, None, None, None, "test")
    ctx = host.identity.context("alice", timeout_seconds=300)
    with host.uow.transaction() as tx:
        source = memories.new_source(tx, "source", ctx.principal.home_scope, "original bytes")
        item = memories.new_memory(
            tx, "memory", ctx.principal.home_scope, "original bytes", (source,), "episodic"
        )
    cache = RedisCache(client, RememberPolicy(), namespace=namespace)

    def make():
        from aether_agent_memory.runtime.storage.redis_executor import RedisExecutor

        return RedisExecutor(host.uow, host.identity, memories, cache)

    yield host, memories, ctx, item, cache, make
    host.close()


async def test_redis_copy_and_pg_receipt_survive_executor_reconstruction(execution):
    host, memories, ctx, item, cache, make = execution
    first = make()
    await asyncio.to_thread(first.ensure, item, ctx)
    assert cache.get_sync(item.ref.scope, item.content_hash) == "original bytes"
    assert first.inspect(item.ref, item.content_hash)
    rebuilt = make()
    assert rebuilt.instance_id == first.instance_id
    assert rebuilt.copies()[0].memory == item.ref
    observation = await rebuilt.observe(ctx, item.ref, "original")
    assert observation.readable and observation.tier == Tier.HOT
    assert (await rebuilt.resources(ctx)).supported_moves == ()


async def test_configured_capacity_limits_executor_and_direct_cache_admission(execution):
    from aether_agent_memory.operate.basic.cache_port import CacheCapacityError

    host, memories, ctx, item, cache, make = execution
    executor = make()
    executor.capacity = 8
    with pytest.raises(CacheCapacityError):
        await asyncio.to_thread(executor.ensure, item, ctx)
    assert await cache.put(item.ref.scope, "original bytes") is False
    assert cache.usage_sync(item.ref.scope) == 0
    assert (await executor.resources(ctx)).available_bytes == 8


async def test_configured_capacity_is_atomic_across_cache_writers(execution):
    host, memories, ctx, item, cache, make = execution
    executor = make()
    executor.capacity = 20
    results = await asyncio.gather(
        cache.put(item.ref.scope, "original bytes"),
        cache.put(item.ref.scope, "second original"),
    )
    assert sorted(results) == [False, True]
    assert cache.usage_sync(item.ref.scope) in {14, 15}
    assert (await executor.resources(ctx)).available_bytes in {5, 6}


async def test_repair_lost_response_can_be_queried_with_original_receipt(execution, monkeypatch):
    host, memories, ctx, item, cache, make = execution
    executor = make()
    await asyncio.to_thread(executor.ensure, item, ctx)
    key, field, _ = cache.keys(item.ref.scope, item.content_hash)
    cache.client.hset(key, field, b"corrupt")
    assert not executor.inspect(item.ref, item.content_hash)
    original = cache.put_sync

    def lost(scope, text):
        assert original(scope, text)
        raise ConnectionError("response lost after Redis write")

    monkeypatch.setattr(cache, "put_sync", lost)
    with pytest.raises(FoundationError) as error:
        await asyncio.to_thread(executor.repair, item, "repair-original", ctx)
    assert error.value.code == ErrorCode.COMMIT_UNCONFIRMED
    rebuilt = make()
    assert rebuilt.repair_record("repair-original") == (
        item.ref.model_dump_json(),
        item.content_hash,
    )
    assert rebuilt.inspect(item.ref, item.content_hash)
    monkeypatch.setattr(cache, "put_sync", original)
    await asyncio.to_thread(rebuilt.repair, item, "repair-original", ctx)
    assert rebuilt.inspect(item.ref, item.content_hash)


async def test_purge_during_remote_copy_prevents_late_publication(execution, monkeypatch):
    host, memories, ctx, item, cache, make = execution
    executor = make()
    original = cache.put_sync

    def purge_before_write(scope, text):
        executor.purge(item.ref, permanent=True, ctx=ctx)
        return original(scope, text)

    monkeypatch.setattr(cache, "put_sync", purge_before_write)
    with pytest.raises(FoundationError):
        await asyncio.to_thread(executor.ensure, item, ctx)
    assert executor.copies() == []
    assert executor.cleanup_complete(item.ref, permanent=True)
    assert cache.get_sync(item.ref.scope, item.content_hash) is None
    with pytest.raises(FoundationError) as error:
        await asyncio.to_thread(make().ensure, item, ctx)
    assert error.value.code == ErrorCode.MEMORY_GONE


async def test_unsupported_movement_has_stable_failed_receipt_without_changing_bytes(execution):
    host, memories, ctx, item, cache, make = execution
    executor = make()
    await asyncio.to_thread(executor.ensure, item, ctx)
    intent = ActionIntent(
        action_id="unsupported-move",
        decision=PlacementDecision(
            decision_id="decision",
            memory=item.ref,
            outcome="demote",
            current_tier="hot",
            target_tier="warm",
            reason="policy",
            policy_version="test",
            storage_watermark=1,
            access_watermark=1,
        ),
        representation_id="original",
        content_hash=item.content_hash,
        provider_id=executor.provider_id,
        provider_instance_id=executor.instance_id,
        expected_epoch=0,
        provider_mode="real",
        created_at=now(),
    )
    result = await executor.submit(ctx, intent)
    assert result.state == "failed" and result.reason == "unsupported_tier_transition"
    assert await make().query(ctx, intent.action_id) == result
    assert cache.get_sync(item.ref.scope, item.content_hash) == "original bytes"
    changed = intent.model_copy(update={"content_hash": "0" * 64})
    with pytest.raises(FoundationError) as error:
        await executor.submit(ctx, changed)
    assert error.value.code == ErrorCode.IDEMPOTENCY_CONFLICT


@pytest.mark.parametrize("operation", ["resources", "observe", "submit", "query"])
async def test_slow_metadata_keeps_cache_transport_responsive(execution, monkeypatch, operation):
    host, memories, ctx, item, cache, make = execution
    executor = make()
    await asyncio.to_thread(executor.ensure, item, ctx)
    intent = ActionIntent(
        action_id="slow-metadata-action",
        decision=PlacementDecision(
            decision_id="slow-decision",
            memory=item.ref,
            outcome="demote",
            current_tier="hot",
            target_tier="warm",
            reason="policy",
            policy_version="test",
            storage_watermark=1,
            access_watermark=1,
        ),
        representation_id="original",
        content_hash=item.content_hash,
        provider_id=executor.provider_id,
        provider_instance_id=executor.instance_id,
        expected_epoch=0,
        provider_mode="real",
        created_at=now(),
    )
    original = host.uow.transaction
    loop = asyncio.get_running_loop()
    responsive = []

    @contextmanager
    def slow_transaction():
        released = threading.Event()
        loop.call_soon_threadsafe(released.set)
        responsive.append(released.wait(0.15))
        with original() as tx:
            yield tx

    monkeypatch.setattr(host.uow, "transaction", slow_transaction)
    if operation == "resources":
        assert (await executor.resources(ctx)).supported_moves == ()
    elif operation == "observe":
        assert (await executor.observe(ctx, item.ref, "original")).readable
    elif operation == "submit":
        assert (await executor.submit(ctx, intent)).reason == "unsupported_tier_transition"
    else:
        assert (await executor.query(ctx, intent.action_id)).state == "not_found"
    assert responsive and all(responsive), "metadata waits blocked transport callbacks"


@pytest.mark.parametrize("phase", ["prepare", "submit", "reconcile", "commit"])
async def test_operate_stages_keep_transport_responsive_on_real_pg(execution, monkeypatch, phase):
    from aether_agent_memory.operate.basic.service import Operate
    from aether_agent_memory.operate.basic.temporal_stages import OperateStages
    from aether_agent_memory.recall.basic.service import Recall
    from aether_agent_memory.runtime.contracts.models import Permission
    from aether_agent_memory.runtime.foundation.common import fingerprint
    from aether_agent_memory.runtime.temporal.activities import StageContext, _context
    from aether_agent_memory.runtime.temporal.config import TemporalConfiguration
    from aether_agent_memory.runtime.temporal.ledger import ExecutionLedger
    from aether_agent_memory.runtime.temporal.models import ExecutionRef, StepRequest

    host, memories, ctx, item, cache, make = execution
    host.events.register_type("recall.access", Recall.validate_event, permission=Permission.READ)
    controller = Operate(
        host.uow, host.identity, host.tasks, host.events, memories, memories, make()
    )
    ledger = ExecutionLedger(
        host.tasks, TemporalConfiguration(deployment_id="stage-test", endpoint="127.0.0.1:7233")
    )
    with host.uow.transaction() as tx:
        key = fingerprint([item.ref.scope.model_dump(mode="json"), item.ref.memory_id])
        tx.write(
            "operate_views",
            key,
            {
                "memory": item.ref.model_dump(mode="json"),
                "storage_watermark": 1,
                "access_watermark": 0,
                "successful_reads": 0,
            },
        )
        task_id = controller.enqueue(tx, ctx, item.ref, "test", cleanup=False, permanent=False)
        job = ledger.bind_admitted(tx, host.tasks.load(tx, task_id)[1])
        step = StepRequest(job=job, stage="prepare", ordinal=0, mode="execute")
        delivery = ExecutionRef(
            namespace="default",
            workflow_id=f"p3/stage-test/{job.kind}/{job.job_id}",
            run_id="run1",
            activity_id="1",
            delivery_attempt=1,
            epoch=0,
        )
        ledger.begin(tx, step, delivery)
        task = host.tasks.load(tx, task_id)[1]
    stages = OperateStages(controller)
    loop = asyncio.get_running_loop()
    original, responsive = host.uow.transaction, []

    @contextmanager
    def slow_transaction():
        released = threading.Event()
        loop.call_soon_threadsafe(released.set)
        responsive.append(released.wait(0.15))
        with original() as tx:
            yield tx

    with ThreadPoolExecutor(2) as pool:
        token = _context.set(StageContext(ledger, task, ctx, task.execution, pool))
        try:
            sequence = ["prepare", "submit", "reconcile", "commit"]
            for preceding in sequence[: sequence.index(phase)]:
                await getattr(stages, preceding)(step)
            monkeypatch.setattr(host.uow, "transaction", slow_transaction)
            assert (await getattr(stages, phase)(step)).outcome == "done"
        finally:
            _context.reset(token)
    assert responsive and all(responsive), "Operate stage blocked transport callbacks"


@pytest.mark.parametrize("continuous", [False, True])
async def test_controller_does_not_reserve_unsupported_redis_tier_moves(execution, continuous):
    from aether_agent_memory.operate.basic.continuous import ContinuousOperate
    from aether_agent_memory.operate.basic.service import Operate
    from aether_agent_memory.recall.basic.service import Recall
    from aether_agent_memory.runtime.contracts.models import Permission
    from aether_agent_memory.runtime.foundation.common import fingerprint
    from aether_agent_memory.runtime.temporal.config import TemporalConfiguration
    from aether_agent_memory.runtime.temporal.ledger import ExecutionLedger
    from aether_agent_memory.runtime.temporal.models import ExecutionRef, StepRequest

    host, memories, ctx, item, cache, make = execution
    executor = make()
    host.events.register_type("recall.access", Recall.validate_event, permission=Permission.READ)
    controller = (ContinuousOperate if continuous else Operate)(
        host.uow, host.identity, host.tasks, host.events, memories, memories, executor
    )
    ledger = ExecutionLedger(
        host.tasks, TemporalConfiguration(deployment_id="redis-test", endpoint="127.0.0.1:7233")
    )
    key = fingerprint([item.ref.scope.model_dump(mode="json"), item.ref.memory_id])
    with host.uow.transaction() as tx:
        tx.write(
            "operate_views",
            key,
            {
                "memory": item.ref.model_dump(mode="json"),
                "storage_watermark": 1,
                "access_watermark": 0,
                "successful_reads": 0,
            },
        )
        task_id = controller.enqueue(tx, ctx, item.ref, "test", cleanup=False, permanent=False)
        job = ledger.bind_admitted(tx, host.tasks.load(tx, task_id)[1])
        ledger.begin(
            tx,
            StepRequest(job=job, stage="prepare", ordinal=0, mode="execute"),
            ExecutionRef(
                namespace="default",
                workflow_id=f"p3/redis-test/{job.kind}/{job.job_id}",
                run_id="run1",
                activity_id="1",
                delivery_attempt=1,
                epoch=0,
            ),
        )
        task = host.tasks.load(tx, task_id)[1]
    prepared = await controller.prepare_evaluation(ctx, task)
    assert "intent" not in prepared
    decision = prepared["value"]["decision"]
    assert decision["outcome"] == "defer"
    assert decision["reason"] == "unsupported_tier_transition"
    assert decision["current_tier"] == decision["target_tier"] == "hot"
    with host.uow.transaction() as tx:
        assert tx.rows("operate_actions") == []
        assert tx.read("operate_task_actions", task_id) is None
    assert cache.get_sync(item.ref.scope, item.content_hash) == "original bytes"
