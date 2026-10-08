"""Real-backend coverage of source references, cache metadata and deferred batching."""

import asyncio

import pytest
from remember_helpers import app as app
from remember_helpers import context, drain, source

from aether_agent_memory.remember.basic.service import memory_ref
from aether_agent_memory.remember.contracts.models import RememberRequest, TextInput
from aether_agent_memory.runtime.contracts.models import ScopeSelector


def observation(app, text, name="observation", operation=None):
    return asyncio.run(
        app.remember.save(
            context(app, operation=operation),
            RememberRequest(
                source=source(name),
                selection=ScopeSelector(session_id="new-batch"),
                content=TextInput(kind="text", text=text),
            ),
        )
    )


def test_short_observation_cached_after_durable_commit_and_waits_for_batch(app):
    receipt = observation(app, "这里是一条完整消息。")
    ref = receipt.memories[0]
    with app.foundation.uow.transaction() as tx:
        record = tx.get(memory_ref(ref, versioned=True))
        pending = tx.read("remember_pending", ref.memory_id)
        cache = tx.read("remember_cache_admission", ref.memory_id)
        assert pending["state"] == "pending"
        assert pending["bytes"] == len("这里是一条完整消息。".encode())
        assert cache["state"] == "cached"
        assert cache["memory"] == ref.model_dump(mode="json")
        assert record["cache_location"]["provider_id"] == "redis"
        assert record["cache_location"]["content_hash"] == record["body_location"]["content_hash"]
    drain(app)
    with app.foundation.uow.transaction() as tx:
        # Working projection updates must not silently erase the cache marker.
        record = tx.get(memory_ref(ref, versioned=True))
        assert record["cache_location"] is not None
        assert tx.read("remember_pending", ref.memory_id)["state"] == "pending"


def test_large_working_uses_reference_no_summary_or_projection_and_compression_releases(app):
    text = "我喜欢清淡食物。" * 500
    receipt = observation(app, text)
    ref = receipt.memories[0]
    with app.foundation.uow.transaction() as tx:
        pending = tx.read("remember_pending", ref.memory_id)
        assert pending["state"] == "waiting_compression"
        assert tx.read("remember_working_summaries", ref.memory_id) is None
        assert (
            tx.read("remember_working_representations", ref.memory_id)["representation"]
            == "source_reference"
        )
        assert not app.remember.projection_buildable(tx, app.remember.current(tx, ref.memory_id))
        kinds = [tx.read("tasks", tid)["record"]["kind"] for tid in receipt.task_ids]
        assert kinds == ["remember.compress"]
    drain(app)
    with app.foundation.uow.transaction() as tx:
        assert tx.read("remember_pending", ref.memory_id)["state"] != "waiting_compression"
        assert tx.read("remember_artifacts", app.remember.refkey(ref)) is not None
        assert tx.read("remember_working_summaries", ref.memory_id) is None


def test_duplicate_save_retries_failed_cache_without_another_source(app):
    original = app.remember.bodies.admit

    async def unavailable(scope, text):
        return "unavailable"

    app.remember.bodies.admit = unavailable
    receipt = observation(app, "需要缓存重试的完整内容", operation="cache-retry")
    app.remember.bodies.admit = original
    # Reuse the exact request including source timestamp to exercise idempotent replay.
    with app.foundation.uow.transaction() as tx:
        raw = tx.read("remember_source_input", receipt.source.source_id)
    request = RememberRequest(
        source=raw,
        selection=ScopeSelector(session_id="new-batch"),
        content=TextInput(kind="text", text="需要缓存重试的完整内容"),
    )
    replay = asyncio.run(app.remember.save(context(app, operation="cache-retry"), request))
    assert replay == receipt
    with app.foundation.uow.transaction() as tx:
        assert (
            tx.read("remember_cache_admission", receipt.memories[0].memory_id)["state"] == "cached"
        )


@pytest.mark.parametrize(
    "text,large", [("a" * 7999, False), ("a" * 8000, True), ("中" * 2667, True)]
)
def test_large_threshold_counts_utf8_bytes(app, text, large):
    receipt = observation(app, text)
    with app.foundation.uow.transaction() as tx:
        row = tx.read("remember_working_representations", receipt.memories[0].memory_id)
        assert bool(row) is large


def test_compression_quality_failure_falls_back_and_does_not_wait_forever(app):
    from aether_agent_memory.remember.basic.compression import QualityEvidence

    class Reject:
        async def verify(self, ctx, original, compressed):
            return QualityEvidence(
                passed=False,
                policy="controlled_reject",
                reason="controlled fidelity failure",
                retained_fact_fraction=0,
            )

    app.remember.quality = Reject()
    app.remember.policy = app.remember.policy.model_copy(
        update={"compression_quality_sample_rate": 1.0}
    )
    receipt = observation(app, "完整原文。" * 600)
    drain(app)
    with app.foundation.uow.transaction() as tx:
        pending = tx.read("remember_pending", receipt.memories[0].memory_id)
        artifact = tx.read("remember_artifacts", app.remember.refkey(receipt.memories[0]))
        assert artifact["published"] is False
        assert pending["compression_terminal"] == "failed"
        assert pending["state"] != "waiting_compression"
        assert pending.get("task_id") is not None
        assert pending["state"] == "processed"
    assert app.remember.processing(context(app), receipt.memories[0].memory_id)["state"] == (
        "completed_with_compression_failure"
    )
    # A confirmed transport failure can reach the same recovered state; its
    # diagnostic remains visible even though raw-source consolidation completed.
    with app.foundation.uow.transaction() as tx:
        task_id = pending["compression_task_id"]
        row = tx.read("tasks", task_id)
        row["record"].update(state="failed", effect_status="no_effect")
        tx.write("tasks", task_id, row)
    status = app.remember.processing(context(app), receipt.memories[0].memory_id)
    assert status["state"] == "completed_with_compression_failure"
    assert status["historical_failed_tasks"] == 1
    with app.foundation.uow.transaction() as tx:
        row = tx.read("tasks", task_id)
        row["record"].update(state="attention_required", effect_status="unknown")
        tx.write("tasks", task_id, row)
    assert app.remember.processing(context(app), receipt.memories[0].memory_id)["state"] == "failed"


def test_late_cache_completion_cannot_mark_new_content_version_cached(app):
    from aether_agent_memory.remember.contracts.models import CorrectionRequest

    ctx = context(app, operation="cache-race")
    request = RememberRequest(
        source=source("race"),
        selection=ScopeSelector(session_id="new-batch"),
        content=TextInput(kind="text", text="旧内容"),
    )
    prepared = asyncio.run(app.remember.prepare_save(ctx, request))
    asyncio.run(app.remember.persist_save(ctx, prepared))
    receipt = app.remember.commit_save(ctx, request, prepared)
    original = app.remember.bodies.admit
    corrected = None

    async def replace_during_admit(scope, text):
        nonlocal corrected
        result = await original(scope, text)
        corrected = await app.remember.correct_async(
            context(app, operation="cache-race-correction"),
            receipt.memories[0].memory_id,
            CorrectionRequest(
                expected_version=1,
                content="新内容",
                source=source("race-correction"),
                reason="explicit correction",
            ),
        )
        return result

    app.remember.bodies.admit = replace_during_admit
    asyncio.run(app.remember.admit_save_cache(ctx, prepared, receipt))
    assert corrected is not None
    with app.foundation.uow.transaction() as tx:
        current = tx.get(memory_ref(corrected.memories[0], versioned=True))
        assert current["ref"]["version"] == 2
        assert current["cache_location"] is None
        marker = tx.read("remember_cache_admission", receipt.memories[0].memory_id)
        assert marker is None or marker["memory"]["version"] == 2


@pytest.mark.parametrize("fault", ["revocation", "unavailable"])
def test_authority_read_cache_repair_preserves_read_and_permission_guards(app, fault):
    from aether_agent_memory.remember.contracts.models import DeleteRequest

    receipt = observation(app, "Verified authority body.")
    ref = receipt.memories[0]
    cache = app.remember.bodies.cache
    key, field, _ = cache.keys(
        ref.scope, app.remember.get(context(app), ref.memory_id).content_hash
    )
    cache.client.hset(key, field, b"corrupt")
    original = cache.put

    async def inject(scope, text):
        if fault == "unavailable":
            raise OSError("controlled cache failure")
        value = await original(scope, text)
        app.remember.revoke_source(
            context(app),
            receipt.source.source_id,
            DeleteRequest(expected_revision=1, reason="revoked during cache repair"),
        )
        return value

    cache.put = inject
    body = asyncio.run(app.remember.read_body(context(app), ref))
    with app.foundation.uow.transaction() as tx:
        record = tx.get(memory_ref(ref, versioned=True))
        admission = tx.read("remember_cache_admission", ref.memory_id)
    assert record["cache_location"] is None
    if fault == "unavailable":
        assert body.outcome == "read" and body.content == "Verified authority body."
        assert body.path == "authority"
        assert admission["state"] == "unavailable"
    else:
        assert body.outcome == "stale" and body.content is None
        assert admission["state"] == "ineligible"
        assert cache.raw_sync(ref.scope, record["body_location"]["content_hash"]) is None
