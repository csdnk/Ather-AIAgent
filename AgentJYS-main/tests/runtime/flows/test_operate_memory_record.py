"""Operate publication: real PG/Redis, stub Ceph; Recall remains unchanged."""

import asyncio
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from test_azure_cache_executor import azure_redis as azure_redis
from test_azure_cache_executor import dsns as dsns
from test_azure_cache_executor import execution as execution
from test_two_tier_operate import intent

from aether_agent_memory.operate.basic.continuous import ContinuousOperate
from aether_agent_memory.operate.basic.temporal_stages import OperateStages
from aether_agent_memory.operate.contracts.models import ActionState, Tier
from aether_agent_memory.recall.basic.service import Recall
from aether_agent_memory.remember.basic.content import Bodies
from aether_agent_memory.remember.basic.pipeline import RememberPipeline
from aether_agent_memory.remember.basic.policy import RememberPolicy
from aether_agent_memory.remember.basic.service import memory_ref
from aether_agent_memory.remember.contracts.foundation import MemoryRecord
from aether_agent_memory.runtime.contracts.models import Permission
from aether_agent_memory.runtime.foundation.common import FoundationError, fingerprint, later
from aether_agent_memory.runtime.storage.redis_executor import RedisExecutor
from aether_agent_memory.runtime.temporal.activities import StageContext, _context
from aether_agent_memory.runtime.temporal.config import TemporalConfiguration
from aether_agent_memory.runtime.temporal.ledger import ExecutionLedger
from aether_agent_memory.runtime.temporal.models import ExecutionRef, StepRequest


@pytest.fixture
def publication(execution, tmp_path):
    host, memories, ctx, item, cache, _ = execution
    objects = {}
    ceph = SimpleNamespace(
        binding=lambda: {
            "provider": "ceph",
            "resource": "stub",
            "namespace": "test",
            "contract_version": 1,
        },
        get_object_sync=lambda key: objects.get(key),
    )
    bodies = Bodies(tmp_path / "bodies", RememberPolicy(), p2=ceph)
    location = bodies.location(item.ref.scope, item.content)
    # Use a real location-based record, not the legacy inline test DTO.
    item = item.model_copy(update={"projection_state": "pending"})
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
    reader.__dict__.update(memories.__dict__)
    reader.bodies, reader.policy = bodies, RememberPolicy()
    bodies.remember_verified(location, item.content)
    bodies.prepared[location.object_key] = location
    objects[location.object_key] = item.content.encode()
    executor = RedisExecutor(
        host.uow, host.identity, reader, cache, authority_reader=reader.read_authority
    )
    host.events.register_type("recall.access", Recall.validate_event, permission=Permission.READ)
    service = ContinuousOperate(
        host.uow, host.identity, host.tasks, host.events, reader, reader, executor
    )
    return SimpleNamespace(
        host=host,
        reader=reader,
        ctx=ctx,
        item=item,
        cache=cache,
        executor=executor,
        service=service,
        objects=objects,
    )


def read_record(p):
    with p.host.uow.transaction() as tx:
        return tx.get(memory_ref(p.item.ref, versioned=True))


def record_revision(p):
    with p.host.uow.transaction() as tx:
        return tx.revision(memory_ref(p.item.ref, versioned=True))


async def act(p, tier, name):
    return await p.service.execute(p.ctx, intent(p.executor, p.ctx, p.item, tier, name))


async def test_hot_cold_publication_preserves_body_and_remember_fields(publication):
    p = publication
    before = read_record(p)
    hot = await act(p, Tier.HOT, "promote")
    assert hot.state == ActionState.SUCCEEDED
    updated = read_record(p)
    location = updated["cache_location"]
    assert location["provider_id"] == "redis"
    assert location["generation"] == before["body_location"]["generation"]
    assert location["content_hash"] == p.item.content_hash
    locator = json.loads(location["object_key"])
    key, field, expiry = p.cache.keys(p.item.ref.scope, p.item.content_hash)
    assert locator == {
        "encoding": "redis_hash_v1",
        "key": key,
        "field": field,
        "expiry_field": expiry,
    }
    assert p.cache.client.hget(key, field) == p.item.content.encode()
    assert {**updated, "cache_location": None} == before
    cooled = await act(p, Tier.COLD, "demote")
    assert cooled.state == ActionState.SUCCEEDED
    assert read_record(p) == before
    assert p.cache.get_sync(p.item.ref.scope, p.item.content_hash) is None
    assert p.objects[before["body_location"]["object_key"]] == p.item.content.encode()
    # Old success and duplicate recovery cannot resurrect a removed hot pointer.
    revision = record_revision(p)
    assert await p.service.accept_feedback(p.ctx, hot.intent, hot.feedback) == hot
    assert (await p.service.reconcile(p.ctx, hot.intent.action_id)).state == ActionState.SUCCEEDED
    assert read_record(p) == before
    assert record_revision(p) == revision


async def test_writeback_and_action_commit_roll_back_together_and_recover(publication, monkeypatch):
    p = publication
    original_save, original_submit = p.service.save_action, p.executor.submit
    submitted = []
    failed = False

    def save(tx, ctx, action):
        nonlocal failed
        if action.state == ActionState.SUCCEEDED and not failed:
            failed = True
            assert tx.get(memory_ref(p.item.ref, versioned=True))["cache_location"] is not None
            raise RuntimeError("injected failure after patch, before action commit")
        return original_save(tx, ctx, action)

    async def submit(ctx, action):
        submitted.append(action.action_id)
        return await original_submit(ctx, action)

    monkeypatch.setattr(p.service, "save_action", save)
    monkeypatch.setattr(p.executor, "submit", submit)
    result = await act(p, Tier.HOT, "publish-retry")
    assert result.state == ActionState.UNKNOWN
    assert result.reason == "memory_record_sync_pending"
    assert read_record(p)["cache_location"] is None
    assert p.cache.get_sync(p.item.ref.scope, p.item.content_hash) == p.item.content
    # A second action cannot pass admission while publication of the first is pending.
    with pytest.raises(FoundationError):
        await act(p, Tier.COLD, "too-early")
    assert submitted == ["publish-retry"]
    # Reconstruct controller/executor to prove recovery uses persisted state.
    executor = RedisExecutor(
        p.host.uow, p.host.identity, p.reader, p.cache, authority_reader=p.reader.read_authority
    )
    p.service.executor = executor
    p.service.record_sync = type(p.service.record_sync)(executor)
    recovered = await p.service.reconcile(p.ctx, "publish-retry")
    assert recovered.state == ActionState.SUCCEEDED
    assert read_record(p)["cache_location"] is not None
    with p.host.uow.transaction() as tx:
        binding = tx.read("operate_cache_bindings", p.service.record_sync.key(p.item.ref))
        assert binding["action_id"] == "publish-retry"
        assert tx.read("operate_actions", "publish-retry")["state"] == "succeeded"
    assert submitted == ["publish-retry"]


@pytest.mark.parametrize("feedback_state", ["accepted", "running", "unknown", "failed"])
async def test_non_success_receipt_does_not_publish(publication, monkeypatch, feedback_state):
    p = publication
    original = p.executor.submit

    async def submit(ctx, action):
        feedback = await original(ctx, action)
        return feedback.model_copy(update={"state": feedback_state})

    monkeypatch.setattr(p.executor, "submit", submit)
    result = await act(p, Tier.HOT, "not-success")
    assert result.state == (
        ActionState.FAILED if feedback_state == "failed" else ActionState.UNKNOWN
    )
    assert read_record(p)["cache_location"] is None


async def test_failed_verification_keeps_original_action_for_recovery(publication, monkeypatch):
    p = publication
    verify = p.executor.verify_read

    async def unavailable(*args):
        raise TimeoutError("read unavailable")

    monkeypatch.setattr(p.executor, "verify_read", unavailable)
    result = await act(p, Tier.HOT, "verify-retry")
    assert result.state == ActionState.UNKNOWN
    assert read_record(p)["cache_location"] is None
    monkeypatch.setattr(p.executor, "verify_read", verify)
    assert (await p.service.reconcile(p.ctx, "verify-retry")).state == ActionState.SUCCEEDED
    assert read_record(p)["cache_location"] is not None


async def test_latest_row_patch_preserves_concurrent_business_metadata(publication, monkeypatch):
    p = publication
    original = p.executor.verify_read

    async def verify(*args):
        proof = await original(*args)
        with p.host.uow.transaction() as tx:
            ref = memory_ref(p.item.ref, versioned=True)
            raw = tx.get(ref)
            tx.put_if_revision(
                ref, {**raw, "importance": 0.91, "importance_reason": "updated"}, tx.revision(ref)
            )
        return proof

    monkeypatch.setattr(p.executor, "verify_read", verify)
    assert (await act(p, Tier.HOT, "metadata-race")).state == ActionState.SUCCEEDED
    raw = read_record(p)
    assert raw["importance"] == 0.91 and raw["importance_reason"] == "updated"
    assert raw["cache_location"] is not None


async def test_changed_executor_fence_does_not_publish_stale_evidence(publication, monkeypatch):
    p = publication
    observe = p.executor.observe

    async def racing_observe(*args):
        observation = await observe(*args)
        with p.host.uow.transaction() as tx:
            key = p.executor.key(p.item.ref)
            fence = tx.read(p.executor.table("fences"), key)
            tx.write(p.executor.table("fences"), key, {**fence, "fence": fence["fence"] + 1})
        return observation

    monkeypatch.setattr(p.executor, "observe", racing_observe)
    assert (await act(p, Tier.HOT, "fence-race")).state == ActionState.UNKNOWN
    assert read_record(p)["cache_location"] is None
    monkeypatch.setattr(p.executor, "observe", observe)
    assert (await p.service.reconcile(p.ctx, "fence-race")).state == ActionState.SUCCEEDED


def evaluation_task(p, *, cleanup=False):
    ledger = ExecutionLedger(
        p.host.tasks, TemporalConfiguration(deployment_id="sync-test", endpoint="127.0.0.1:7233")
    )
    key = fingerprint([p.item.ref.scope.model_dump(mode="json"), p.item.ref.memory_id])
    with p.host.uow.transaction() as tx:
        tx.write(
            "operate_views",
            key,
            {
                "memory": p.item.ref.model_dump(mode="json"),
                "storage_watermark": 1,
                "access_watermark": 0,
                "successful_reads": 0,
            },
        )
        task_id = p.service.enqueue(
            tx, p.ctx, p.item.ref, "sync-test", cleanup=cleanup, permanent=False
        )
        job = ledger.bind_admitted(tx, p.host.tasks.load(tx, task_id)[1])
        step = StepRequest(job=job, stage="prepare", ordinal=0, mode="execute")
        ledger.begin(
            tx,
            step,
            ExecutionRef(
                namespace="default",
                workflow_id=f"p3/sync-test/{job.kind}/{job.job_id}",
                run_id="run1",
                activity_id="1",
                delivery_attempt=1,
                epoch=0,
            ),
        )
        p.ledger, p.step = ledger, step
        return p.host.tasks.load(tx, task_id)[1]


async def test_prepare_repairs_overwritten_pointer_without_migration_or_access(publication):
    p = publication
    assert (await act(p, Tier.HOT, "heat-first")).state == ActionState.SUCCEEDED
    # Existing Remember writer rebuilds the row without cache_location.
    with p.host.uow.transaction() as tx:
        p.reader.put(tx, p.item)
    assert read_record(p)["cache_location"] is None
    task = evaluation_task(p)
    await p.service.prepare_evaluation(p.ctx, task)
    assert read_record(p)["cache_location"] is not None
    with p.host.uow.transaction() as tx:
        actions = tx.rows("operate_actions")
        view = tx.read("operate_views", p.service.record_sync.key(p.item.ref))
        # Preparation can reserve the next cooling action but cannot submit it.
        assert len([a for _, a in actions if a["state"] != "generated"]) == 1
        assert view["successful_reads"] == 0
        assert view["access_watermark"] == 0


async def test_expired_hot_copy_clears_pointer_and_idempotent_sync_does_not_rewrite(publication):
    p = publication
    assert (await act(p, Tier.HOT, "expire-hot")).state == ActionState.SUCCEEDED
    evidence = p.service.capture_evidence(p.item.ref)
    observation = await p.executor.observe(p.ctx, p.item.ref, "original")
    revision = record_revision(p)
    assert p.service.sync_observation(p.ctx, observation, evidence) == "synced"
    assert record_revision(p) == revision
    key, field, expiry = p.cache.keys(p.item.ref.scope, p.item.content_hash)
    p.cache.client.hdel(key, field, expiry)
    await p.service.prepare_evaluation(p.ctx, evaluation_task(p))
    assert read_record(p)["cache_location"] is None


async def test_observation_read_failure_retains_pointer(publication, monkeypatch):
    p = publication
    assert (await act(p, Tier.HOT, "unavailable-hot")).state == ActionState.SUCCEEDED
    before = read_record(p)

    async def unavailable(*args):
        raise TimeoutError("Redis query unavailable")

    monkeypatch.setattr(p.executor, "observe", unavailable)
    with pytest.raises(TimeoutError):
        await p.service.prepare_evaluation(p.ctx, evaluation_task(p))
    assert read_record(p) == before


async def test_new_memory_version_prevents_old_callback_publication(publication, monkeypatch):
    p = publication
    verify = p.executor.verify_read
    old = read_record(p)
    new_memory = p.item.ref.model_copy(update={"version": p.item.ref.version + 1})
    new_ref = memory_ref(new_memory, versioned=True)
    newer = {**old, "ref": new_memory.model_dump(mode="json")}

    async def publish_new_version(*args):
        proof = await verify(*args)
        with p.host.uow.transaction() as tx:
            tx.put_if_revision(new_ref, newer, None)
            tx.write("remember_current", new_memory.memory_id, new_ref.model_dump(mode="json"))
        return proof

    monkeypatch.setattr(p.executor, "verify_read", publish_new_version)
    action = await act(p, Tier.HOT, "stale-version")
    assert action.state == ActionState.CANCELLED
    assert action.cleanup_state == "pending"
    assert read_record(p) == old
    with p.host.uow.transaction() as tx:
        assert tx.get(new_ref) == newer
        assert (
            tx.read("operate_cache_sync_heads", p.service.record_sync.key(p.item.ref))["state"]
            == "cancelled"
        )


async def test_other_tenant_cannot_reconcile_or_patch(publication, monkeypatch):
    p = publication
    submit = p.executor.submit

    async def pending(ctx, action):
        return (await submit(ctx, action)).model_copy(update={"state": "running"})

    monkeypatch.setattr(p.executor, "submit", pending)
    action = await act(p, Tier.HOT, "tenant-bound")
    bob = p.host.identity.context("bob")
    before = read_record(p)
    with pytest.raises(FoundationError):
        await p.service.reconcile(bob, action.intent.action_id)
    with pytest.raises(FoundationError):
        await p.service.accept_feedback(bob, action.intent, action.feedback)
    assert read_record(p) == before
    assert (
        await p.service.reconcile(p.ctx, action.intent.action_id)
    ).state == ActionState.SUCCEEDED


@pytest.mark.parametrize("mismatch", ["memory", "content_hash", "provider_instance_id", "readable"])
async def test_fresh_observation_must_match_exact_action(publication, monkeypatch, mismatch):
    p = publication
    observe = p.executor.observe

    async def wrong(*args):
        result = await observe(*args)
        replacement = {
            "memory": p.item.ref.model_copy(update={"version": 999}),
            "content_hash": "f" * 64,
            "provider_instance_id": "other-instance",
            "readable": False,
        }[mismatch]
        return result.model_copy(update={mismatch: replacement})

    monkeypatch.setattr(p.executor, "observe", wrong)
    action = await act(p, Tier.HOT, "wrong-observation")
    assert action.state == ActionState.UNKNOWN
    assert read_record(p)["cache_location"] is None


async def test_body_generation_changed_during_readback_requires_fresh_proof(
    publication, monkeypatch
):
    p = publication
    observe = p.executor.observe
    changed = False

    async def replace_body_binding(*args):
        nonlocal changed
        observation = await observe(*args)
        if not changed:
            changed = True
            with p.host.uow.transaction() as tx:
                ref = memory_ref(p.item.ref, versioned=True)
                raw = tx.get(ref)
                raw["body_location"]["generation"] = "next-generation"
                tx.put_if_revision(ref, raw, tx.revision(ref))
        return observation

    monkeypatch.setattr(p.executor, "observe", replace_body_binding)
    action = await act(p, Tier.HOT, "body-generation")
    assert action.state == ActionState.UNKNOWN
    assert read_record(p)["cache_location"] is None


async def test_global_epoch_change_from_unrelated_work_does_not_block_publication(
    publication, monkeypatch
):
    p = publication
    observe = p.executor.observe

    async def other_work(*args):
        observation = await observe(*args)
        with p.host.uow.transaction() as tx:
            epoch = tx.read(p.executor.table("settings"), "epoch") or 0
            tx.write(p.executor.table("settings"), "epoch", epoch + 1)
        return observation

    monkeypatch.setattr(p.executor, "observe", other_work)
    assert (await act(p, Tier.HOT, "unrelated-epoch")).state == ActionState.SUCCEEDED
    assert read_record(p)["cache_location"] is not None


async def test_demote_writeback_failure_keeps_old_pointer_until_recovered(publication, monkeypatch):
    p = publication
    assert (await act(p, Tier.HOT, "hot-first")).state == ActionState.SUCCEEDED
    hot_location = read_record(p)["cache_location"]
    patch = p.service.record_sync.patch

    def fail_clear(tx, memory, raw, location):
        if location is None:
            raise ConnectionError("metadata unavailable")
        return patch(tx, memory, raw, location)

    monkeypatch.setattr(p.service.record_sync, "patch", fail_clear)
    action = await act(p, Tier.COLD, "cool-writeback")
    assert action.state == ActionState.UNKNOWN
    assert p.cache.get_sync(p.item.ref.scope, p.item.content_hash) is None
    assert read_record(p)["cache_location"] == hot_location
    monkeypatch.setattr(p.service.record_sync, "patch", patch)
    assert (await p.service.reconcile(p.ctx, "cool-writeback")).state == ActionState.SUCCEEDED
    assert read_record(p)["cache_location"] is None


async def test_lost_response_after_committed_success_is_idempotent(publication, monkeypatch):
    p = publication
    commit = p.service.commit_feedback
    lost = False

    def lose_once(*args):
        nonlocal lost
        action = commit(*args)
        if action.state == ActionState.SUCCEEDED and not lost:
            lost = True
            raise ConnectionError("caller lost the commit response")
        return action

    monkeypatch.setattr(p.service, "commit_feedback", lose_once)
    result = await act(p, Tier.HOT, "committed-response-lost")
    assert result.state == ActionState.SUCCEEDED
    assert read_record(p)["cache_location"] is not None
    revision = record_revision(p)
    assert await p.service.reconcile(p.ctx, result.intent.action_id) == result
    assert record_revision(p) == revision


async def test_cleanup_publishes_only_after_purge_and_retries_writeback(publication, monkeypatch):
    p = publication
    assert (await act(p, Tier.HOT, "archive-hot")).state == ActionState.SUCCEEDED
    with p.host.uow.transaction() as tx:
        ref = memory_ref(p.item.ref, versioned=True)
        raw = tx.get(ref)
        tx.put_if_revision(ref, {**raw, "status": "archived"}, tx.revision(ref))
    task = evaluation_task(p, cleanup=True)
    prepared = await p.service.prepare_evaluation(p.ctx, task)
    assert prepared["valid"]
    patch = p.service.record_sync.patch

    def fail(*args):
        raise ConnectionError("cleanup metadata unavailable")

    monkeypatch.setattr(p.service.record_sync, "patch", fail)
    with pytest.raises(ConnectionError):
        await p.service.submit_evaluation(p.ctx, task, prepared)
    assert p.cache.get_sync(p.item.ref.scope, p.item.content_hash) is None
    assert read_record(p)["cache_location"] is not None
    monkeypatch.setattr(p.service.record_sync, "patch", patch)
    assert await p.service.submit_evaluation(p.ctx, task, prepared) == {
        "cache_cleanup": "completed"
    }
    assert read_record(p)["cache_location"] is None
    with p.host.uow.transaction() as tx:
        assert tx.read("operate_cache_bindings", p.service.record_sync.key(p.item.ref)) is None


async def test_same_action_concurrent_deliveries_publish_once(publication):
    p = publication
    action = intent(p.executor, p.ctx, p.item, Tier.HOT, "parallel-original")
    first, second = await asyncio.gather(
        p.service.execute(p.ctx, action), p.service.execute(p.ctx, action)
    )
    result = await p.service.reconcile(p.ctx, action.action_id)
    assert result.state == ActionState.SUCCEEDED
    assert first.state in {ActionState.UNKNOWN, ActionState.SUCCEEDED}
    assert second.state in {ActionState.UNKNOWN, ActionState.SUCCEEDED}
    assert read_record(p)["cache_location"] is not None
    with p.host.uow.transaction() as tx:
        assert len(tx.rows(p.executor.table("actions"))) == 1
        assert len(tx.rows("operate_cache_bindings")) == 1


async def test_temporal_cleanup_reconcile_retries_metadata_after_lost_purge_reply(publication):
    p = publication
    assert (await act(p, Tier.HOT, "temporal-archive-hot")).state == ActionState.SUCCEEDED
    with p.host.uow.transaction() as tx:
        ref = memory_ref(p.item.ref, versioned=True)
        raw = tx.get(ref)
        tx.put_if_revision(ref, {**raw, "status": "archived"}, tx.revision(ref))
    task = evaluation_task(p, cleanup=True)
    stages = OperateStages(p.service)
    with ThreadPoolExecutor(2) as pool:
        token = _context.set(StageContext(p.ledger, task, p.ctx, task.execution, pool))
        try:
            assert (await stages.prepare(p.step)).outcome == "done"
            data = stages.required("prepared")
            stages.write("temporal_cache_cleanup", task.task_id, data)
            # Crash window: Redis removal confirmed, task never saved evaluated phase.
            await asyncio.to_thread(p.executor.purge, p.item.ref, permanent=False, ctx=p.ctx)
            assert read_record(p)["cache_location"] is not None
            assert (await stages.reconcile(p.step)).outcome == "done"
            assert read_record(p)["cache_location"] is None
            assert stages.required("evaluated") == {"cache_cleanup": "completed"}
        finally:
            _context.reset(token)


async def test_real_publication_does_not_block_transport_loop(publication, monkeypatch):
    p = publication
    action = intent(p.executor, p.ctx, p.item, Tier.HOT, "async-publication")
    original = p.host.uow.transaction
    responsive = []
    loop = asyncio.get_running_loop()

    @contextmanager
    def check_thread():
        released = threading.Event()
        loop.call_soon_threadsafe(released.set)
        responsive.append(released.wait(0.15))
        with original() as tx:
            yield tx

    monkeypatch.setattr(p.host.uow, "transaction", check_thread)
    assert (await p.service.execute(p.ctx, action)).state == ActionState.SUCCEEDED
    assert (await p.service.reconcile(p.ctx, action.action_id)).state == ActionState.SUCCEEDED
    assert responsive and all(responsive)


async def test_pending_action_blocks_observation_only_publication(publication, monkeypatch):
    p = publication
    submit = p.executor.submit

    async def unknown(ctx, action):
        return (await submit(ctx, action)).model_copy(update={"state": "unknown"})

    monkeypatch.setattr(p.executor, "submit", unknown)
    assert (await act(p, Tier.HOT, "pending-publication")).state == ActionState.UNKNOWN
    evidence = p.service.capture_evidence(p.item.ref)
    observation = await p.executor.observe(p.ctx, p.item.ref, "original")
    assert p.service.sync_observation(p.ctx, observation, evidence) == "pending_action"
    assert read_record(p)["cache_location"] is None


async def test_unsupported_adapter_never_invents_a_location(publication, monkeypatch):
    p = publication
    observation = await p.executor.observe(p.ctx, p.item.ref, "original")
    monkeypatch.setattr(p.executor, "policy_managed", False)
    evidence = p.service.capture_evidence(p.item.ref)
    with pytest.raises(FoundationError):
        p.service.sync_observation(p.ctx, observation, evidence)
    assert read_record(p)["cache_location"] is None


@pytest.mark.parametrize("ineligible_reason", ["expired", "new_version"])
async def test_cleanup_handles_logically_inactive_record_and_preserves_new_version(
    publication, monkeypatch, ineligible_reason
):
    p = publication
    assert (await act(p, Tier.HOT, "logical-inactive")).state == ActionState.SUCCEEDED
    new_ref = None
    with p.host.uow.transaction() as tx:
        ref = memory_ref(p.item.ref, versioned=True)
        raw = tx.get(ref)
        if ineligible_reason == "expired":
            expires = later(raw["created_at"], 1)
            raw["expires_at"] = expires
            tx.put_if_revision(ref, raw, tx.revision(ref))
            # Keep the operation within the context's validity but after record expiry.
            monkeypatch.setattr(p.host.identity, "clock", lambda: later(expires, 1))
        else:
            memory = p.item.ref.model_copy(update={"version": p.item.ref.version + 1})
            new_ref = memory_ref(memory, versioned=True)
            newer = {**raw, "ref": memory.model_dump(mode="json"), "cache_location": None}
            tx.put_if_revision(new_ref, newer, None)
            tx.write("remember_current", memory.memory_id, new_ref.model_dump(mode="json"))
    task = evaluation_task(p, cleanup=True)
    prepared = await p.service.prepare_evaluation(p.ctx, task)
    assert prepared["valid"]
    assert await p.service.submit_evaluation(p.ctx, task, prepared) == {
        "cache_cleanup": "completed"
    }
    assert read_record(p)["status"] == "active"
    assert read_record(p)["cache_location"] is None
    if new_ref is not None:
        with p.host.uow.transaction() as tx:
            assert tx.get(new_ref) == newer
