"""Real-backend coverage of source references, cache metadata and deferred batching."""

import asyncio

import pytest
from remember_candidate_support import configure_candidates
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


def test_large_working_keeps_original_and_schedules_projection_and_extraction(app):
    manager, processor = configure_candidates(app, "我喜欢清淡食物。")
    text = "我喜欢清淡食物。\n" * 500
    receipt = observation(app, text)
    ref = receipt.memories[0]
    with app.foundation.uow.transaction() as tx:
        pending = tx.read("remember_pending", ref.memory_id)
        assert pending["state"] == "scheduled"
        assert tx.read("remember_working_summaries", ref.memory_id) is None
        assert tx.read("remember_working_representations", ref.memory_id) is None
        assert app.remember.current(tx, ref.memory_id).content == text
        assert app.remember.projection_buildable(tx, app.remember.current(tx, ref.memory_id))
        kinds = [tx.read("tasks", tid)["record"]["kind"] for tid in receipt.task_ids]
        assert set(kinds) == {"remember.extract", "remember.project"}
        assert tx.read("remember_precompression_manifest", app.remember.refkey(ref)) is None
    assert not processor.inputs and not manager.extraction_inputs
    drain(app)
    with app.foundation.uow.transaction() as tx:
        assert tx.read("remember_pending", ref.memory_id)["state"] == "processed"
        ids = tx.read("remember_precompression_manifest", app.remember.refkey(ref))
        assert ids
        for artifact_id in ids:
            artifact = tx.read("remember_precompression_artifacts", artifact_id)
            assert artifact["consumer"] == "langmem_candidate_extraction"
            assert artifact["quality_review_enabled"] is False
        assert app.remember.current(tx, ref.memory_id).content == text
        assert app.remember.current(tx, ref.memory_id).ref.version == 1
    assert processor.inputs and manager.extraction_inputs and manager.decision_inputs


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
    manager, processor = configure_candidates(app)
    # Force short inputs through extraction too: only single-input byte length
    # decides whether LLMLingua runs, never the aggregate scheduling setting.
    app.remember.policy = app.remember.policy.model_copy(update={"consolidation_messages": 1})
    receipt = observation(app, text)
    drain(app)
    assert bool(processor.inputs) is large
    assert manager.extraction_inputs
    with app.foundation.uow.transaction() as tx:
        ref = receipt.memories[0]
        assert app.remember.current(tx, ref.memory_id).content == text
        assert tx.read("remember_pending", ref.memory_id)["state"] == "processed"
        assert bool(tx.read("remember_precompression_manifest", app.remember.refkey(ref))) is large


def test_long_route_failure_keeps_pending_and_does_not_silently_use_original(app):
    manager, processor = configure_candidates(app, "完整原文。")

    async def fail_compression():
        raise ValueError("controlled token alignment failure")

    processor.before_compress = fail_compression
    text = "完整原文。\n" * 600
    receipt = observation(app, text)
    drain(app)
    with app.foundation.uow.transaction() as tx:
        ref = receipt.memories[0]
        pending = tx.read("remember_pending", ref.memory_id)
        assert pending["state"] != "processed"
        assert app.remember.current(tx, ref.memory_id).content == text
        assert tx.read("remember_precompression_manifest", app.remember.refkey(ref)) is None
    assert processor.inputs and not manager.extraction_inputs
    assert not manager.decision_inputs


def test_late_cache_completion_cannot_reheat_deleted_working(app):
    from aether_agent_memory.remember.contracts.models import DeleteRequest

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
    ref = receipt.memories[0]
    item = app.remember.get(ctx, ref.memory_id)
    deletions = []

    async def delete_during_admit(scope, text):
        result = await original(scope, text)
        deletions.append(
            app.remember.delete(
                context(app, operation="cache-race-delete"),
                ref.memory_id,
                DeleteRequest(
                    expected_revision=item.object_revision, reason="deleted during cache I/O"
                ),
            )
        )
        return result

    app.remember.bodies.admit = delete_during_admit
    asyncio.run(app.remember.admit_save_cache(ctx, prepared, receipt))
    assert len(deletions) == 1
    with app.foundation.uow.transaction() as tx:
        current = tx.get(memory_ref(ref, versioned=True))
        assert current["ref"]["version"] == ref.version
        assert current["status"] == "deleted"
        assert current["cache_location"] is None
        marker = tx.read("remember_cache_admission", ref.memory_id)
        assert marker is not None and marker["state"] == "ineligible"
        assert tx.read("remember_pending", ref.memory_id)["state"] == "obsolete"
    assert app.remember.bodies.cache.get_sync(ref.scope, item.content_hash) is None
    # Both late completion and a saved-receipt replay must honor the tombstone.
    asyncio.run(app.remember.admit_save_cache(ctx, prepared, receipt))
    assert len(deletions) == 1
    assert app.remember.bodies.cache.get_sync(ref.scope, item.content_hash) is None
    with app.foundation.uow.transaction() as tx:
        assert tx.get(memory_ref(ref, versioned=True))["cache_location"] is None


@pytest.mark.parametrize("fault", ["revocation", "unavailable"])
def test_authority_fallback_is_read_only_and_rechecks_permission(app, fault, monkeypatch):
    from aether_agent_memory.remember.contracts.models import DeleteRequest

    receipt = observation(app, "Verified authority body.")
    ref = receipt.memories[0]
    cache = app.remember.bodies.cache
    key, field, _ = cache.keys(
        ref.scope, app.remember.get(context(app), ref.memory_id).content_hash
    )
    cache.client.hset(key, field, b"corrupt")
    original = app.remember.bodies.read_authority
    writes = []
    expected = None

    def metadata():
        with app.foundation.uow.transaction() as tx:
            return (
                tx.get(memory_ref(ref, versioned=True)),
                tx.read("remember_cache_admission", ref.memory_id),
            )

    async def forbid_put(scope, text):
        writes.append((scope, text))
        raise AssertionError("ordinary body read must not repair the cache")

    async def unavailable(*args):
        raise OSError("controlled cache failure")

    async def inject(location):
        nonlocal expected
        value = await original(location)
        if fault == "revocation":
            app.remember.revoke_source(
                context(app),
                receipt.source.source_id,
                DeleteRequest(expected_revision=1, reason="revoked during authority fetch"),
            )
        # The explicit revoke operation may change metadata. The ordinary read
        # must not make further cache/address changes after this point.
        expected = metadata()
        return value

    monkeypatch.setattr(cache, "put", forbid_put)
    if fault == "unavailable":
        monkeypatch.setattr(cache, "read_location", unavailable)
    monkeypatch.setattr(app.remember.bodies, "read_authority", inject)
    body = asyncio.run(app.remember.read_body(context(app), ref))
    assert not writes
    assert expected is not None and metadata() == expected
    if fault == "unavailable":
        assert body.outcome == "read" and body.content == "Verified authority body."
        assert body.path == "authority"
        assert expected[0]["cache_location"] is not None
        assert expected[1]["state"] == "cached"
    else:
        assert body.outcome == "stale" and body.content is None
