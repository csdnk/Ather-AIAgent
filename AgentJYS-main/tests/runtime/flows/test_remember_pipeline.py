"""Remember flow acceptance with provider doubles; no production service claims."""

import asyncio

import pytest
from remember_helpers import app as app
from remember_helpers import context, drain, facts, recall, save, source

from aether_agent_memory.remember.basic.comparison import ComparisonDecision
from aether_agent_memory.remember.basic.compression import CompressionOutput, QualityEvidence
from aether_agent_memory.remember.basic.policy import RememberPolicy, chunks
from aether_agent_memory.remember.basic.service import memory_ref
from aether_agent_memory.remember.contracts.foundation import MemoryRecord, ProjectionManifest
from aether_agent_memory.remember.contracts.models import (
    CandidateFact,
    CorrectionRequest,
    DeleteRequest,
    ExtractionResult,
    LifecycleRequest,
    ProjectionTarget,
    RememberRequest,
    TextInput,
)
from aether_agent_memory.runtime.contracts.models import ScopeSelector
from aether_agent_memory.runtime.foundation.common import FoundationError, later
from aether_agent_memory.runtime.storage.redis_cache import RedisCache
from azure_storage_support import azure_redis as azure_redis


def observe(app, text, operation=None):
    return asyncio.run(
        app.remember.save(
            context(app, operation=operation),
            RememberRequest(
                source=source(),
                selection=ScopeSelector(session_id="session_1"),
                content=TextInput(kind="text", text=text),
                trigger="observe",
            ),
        )
    )


def test_reference_storage_and_long_event_chunk_manifest(app):
    text = "The deployment failed on Tuesday; 原因是连接超时。\n" * 120
    receipt = save(app, text)
    with app.foundation.uow.transaction() as tx:
        raw = tx.get(memory_ref(receipt.memories[0], versioned=True))
        record = MemoryRecord.model_validate(raw)
        assert "content" not in raw and "text" not in tx.read(
            "remember_sources", receipt.source.source_id
        )
        assert app.remember.bodies.read_local(record.body_location) == text
        assert all(tx.read("remember_outbox", task_id) for task_id in receipt.task_ids)
    drain(app)
    refs = facts(app, receipt)
    assert len(refs) == 1  # Storage chunks do not split this one literal event into many memories.
    with app.foundation.uow.transaction() as tx:
        manifest = ProjectionManifest.model_validate(
            tx.read("remember_manifests", app.remember.refkey(refs[0]))
        )
    assert len(manifest.chunks) > 1 and manifest.state == "ready"
    assert "".join(text[c.start_char : c.end_char] for c in manifest.chunks) == text
    assert all(
        app.remember.tokenizer.count(text[c.start_char : c.end_char]) <= 256
        for c in manifest.chunks
    )
    assert len(recall(app, query="Tuesday", budget=16000).groups) == 1


def test_threshold_zero_output_and_timer(app):
    class Empty:
        async def extract(self, ctx, request):
            return ExtractionResult(
                candidates=(), model_id="zero", policy_version=request.policy_version
            )

    app.remember.extraction = Empty()
    app.remember.policy = app.remember.policy.model_copy(update={"consolidation_messages": 2})
    first = observe(app, "temporary detail one")
    assert [app.foundation.diagnostics.task(context(app), t).kind for t in first.task_ids] == [
        "remember.project"
    ]
    second = observe(app, "temporary detail two")
    assert [app.foundation.diagnostics.task(context(app), t).kind for t in second.task_ids] == [
        "remember.extract",
        "remember.project",
    ]
    drain(app)
    assert facts(app, second) == []
    with app.foundation.uow.transaction() as tx:
        assert tx.read("remember_pending", first.memories[0].memory_id)["state"] == "processed"
    third = observe(app, "temporary detail three")
    with app.foundation.uow.transaction() as tx:
        row = tx.read("remember_pending", third.memories[0].memory_id)
        tx.write(
            "remember_pending",
            third.memories[0].memory_id,
            {
                **row,
                "created_at": later(
                    row["created_at"], -app.remember.policy.consolidation_seconds - 1
                ),
            },
        )
    assert app.remember.periodic() == 1
    drain(app)


def test_direct_semantic_and_duplicate_preserves_both_sources(app):
    class Semantic:
        async def extract(self, ctx, request):
            return ExtractionResult(
                candidates=(
                    CandidateFact(
                        text=request.text,
                        sources=(request.source,),
                        kind="semantic",
                        evidence_status="supported",
                        importance_category="explicit_constraint",
                    ),
                ),
                model_id="semantic",
                policy_version=request.policy_version,
            )

    app.remember.extraction = Semantic()
    first = save(app, "Never log API credentials")
    drain(app)
    second = save(app, "Never log API credentials")
    drain(app)
    a, b = facts(app, first)[0], facts(app, second)[0]
    assert a == b
    item = app.remember.get(context(app), a.memory_id)
    assert item.kind == "semantic" and item.importance == 0.8
    assert {s.source_id for s in item.sources} == {first.source.source_id, second.source.source_id}


def test_same_words_do_not_collapse_distinct_event_occurrences(app):
    first = save(app, "Deployment failed")
    drain(app)
    second = save(app, "Deployment failed")
    drain(app)
    assert facts(app, first)[0] != facts(app, second)[0]
    task_id = app.remember.reprocess(context(app), first.memories[0].memory_id)
    drain(app)
    task = app.foundation.diagnostics.task(context(app), task_id)
    expected = facts(app, first)[0].model_dump(mode="json")
    with app.foundation.uow.transaction() as tx:
        assert tx.get(task.result_ref)["memories"][0] == expected


def test_archive_working_preserves_fact_and_correction_blocks_archived_derivative(app):
    receipt = save(app, "deployment failed")
    drain(app)
    fact = facts(app, receipt)[0]
    working = receipt.memories[0]
    app.remember.lifecycle(
        context(app),
        working.memory_id,
        LifecycleRequest(expected_version=1, target="archived", reason="session closed"),
    )
    assert app.remember.get(context(app), fact.memory_id).status == "active"
    app.remember.lifecycle(
        context(app),
        fact.memory_id,
        LifecycleRequest(expected_version=1, target="archived", reason="hide temporarily"),
    )
    asyncio.run(
        app.remember.correct_async(
            context(app),
            working.memory_id,
            CorrectionRequest(
                expected_version=1,
                content="deployment succeeded",
                source=source(),
                reason="verified correction",
            ),
        )
    )
    assert app.remember.get(context(app), fact.memory_id).status == "superseded"
    with pytest.raises(FoundationError):
        app.remember.lifecycle(
            context(app),
            fact.memory_id,
            LifecycleRequest(expected_version=1, target="active", reason="restore stale fact"),
        )


def test_request_timeout_does_not_become_background_deadline(app):
    ctx = app.foundation.identity.context("alice", timeout_seconds=1)
    receipt = asyncio.run(
        app.remember.save(
            ctx,
            RememberRequest(
                source=source(),
                selection=ScopeSelector(),
                content=TextInput(kind="text", text="A durable fact"),
            ),
        )
    )
    task = app.foundation.diagnostics.task(context(app), receipt.task_ids[0])
    assert task.deadline_at > later(ctx.deadline_at, 3600)


@pytest.mark.parametrize(
    "passed,ratio_ok,published", [(True, True, True), (True, False, True), (False, True, False)]
)
def test_compression_ratio_and_independent_quality_gate(app, passed, ratio_ok, published):
    class Compressor:
        async def compress(self, ctx, text):
            return CompressionOutput(text="fact" if ratio_ok else text, strategy="test")

    class Quality:
        async def verify(self, ctx, original, compressed):
            return QualityEvidence(
                passed=passed,
                policy="test",
                reason="independent verification",
                retained_fact_fraction=1 if passed else 0.5,
            )

    app.remember.policy = app.remember.policy.model_copy(update={"compression_min_bytes": 10})
    app.remember.compressor, app.remember.quality = Compressor(), Quality()
    receipt = save(app, "fact " * 50)
    drain(app)
    with app.foundation.uow.transaction() as tx:
        artifact = tx.read("remember_artifacts", app.remember.refkey(receipt.memories[0]))
    assert artifact["published"] is published
    working = app.remember.get(context(app), receipt.memories[0].memory_id)
    assert "全文请通过来源读取" in working.content
    originals = asyncio.run(app.remember.source_access.originals(context(app), (working,)))
    assert originals[0].content == "fact " * 50
    assert facts(app, receipt)  # Compression rejection never blocks extraction.


def test_p2_failure_never_returns_saved_and_verified_read_repairs_replica(app, monkeypatch):
    bodies = app.remember.bodies
    transport = bodies.p2.transport.client

    def unavailable(**kwargs):
        raise OSError("controlled Ceph read failure")

    with monkeypatch.context() as fault:
        fault.setattr(transport, "get_object", unavailable)
        with pytest.raises(FoundationError) as error:
            save(app, "P2 original", operation="p2_save")
        assert error.value.code == "DEPENDENCY_UNAVAILABLE"
    with app.foundation.uow.transaction() as tx:
        assert tx.rows("remember_current") == []
    receipt = save(app, "P2 original", operation="p2_save")
    with app.foundation.uow.transaction() as tx:
        raw = tx.get(memory_ref(receipt.memories[0], versioned=True))
    record = MemoryRecord.model_validate(raw)
    assert bodies.remote_only and not bodies.path(record.body_location).exists()
    cache = bodies.cache
    key, field, _ = cache.keys(record.ref.scope, record.body_location.content_hash)
    cache.client.hset(key, field, b"corrupt")
    body = asyncio.run(app.remember.read_body(context(app), receipt.memories[0]))
    assert body.outcome == "read" and body.content == "P2 original" and body.path == "authority"
    drain(app)
    assert cache.raw_sync(record.ref.scope, record.body_location.content_hash) == b"P2 original"


def test_redis_full_body_quota_hash_and_delete(app, azure_redis):
    async def check():
        client, namespace = azure_redis
        policy = RememberPolicy(cache_max_body_bytes=16, cache_scope_bytes=20)
        cache = RedisCache(client, policy, namespace=namespace)
        scope = context(app).principal.home_scope
        assert await cache.put(scope, "123456789012")
        assert not await cache.put(scope, "abcdefghijkl")
        assert not await cache.put(scope, "x" * 17)
        from aether_agent_memory.runtime.foundation.requests import text_hash

        digest = text_hash("123456789012")
        assert await cache.get(scope, digest) == "123456789012"
        key, body, _ = cache.keys(scope, digest)
        await asyncio.to_thread(client.hset, key, body, b"corrupt")
        with pytest.raises(FoundationError) as error:
            await cache.get(scope, digest)
        assert error.value.code == "CONTRACT_VIOLATION"
        await cache.delete(scope, digest)
        assert await cache.put(scope, "abcdefghijkl")

    asyncio.run(check())


def test_conflict_is_atomic_context_group_and_invalidates_old_pack(app):
    first = save(app, "Deployment failed on Tuesday")
    drain(app)
    old_pack = recall(app, query="Deployment")

    class Compare:
        async def compare(self, ctx, candidate, existing):
            return ComparisonDecision(
                outcome="correct",
                target_id=existing[0].ref.memory_id,
                reason="uncertain correction",
            )

    app.remember.comparison = Compare()
    second = save(app, "Deployment succeeded on Tuesday")
    drain(app)
    pack = recall(app, query="Deployment", budget=1000)
    assert len(pack.groups) == 1 and pack.groups[0].conflict is not None
    assert len(pack.groups[0].items) == 2
    assert facts(app, first)[0] != facts(app, second)[0]
    with pytest.raises(FoundationError) as exc:
        app.recall.result(context(app), old_pack.recall_id)
    assert exc.value.code == "RESULT_INVALIDATED"
    with pytest.raises(FoundationError):
        recall(app, query="Deployment", budget=8)


def test_delete_every_chunk_and_tombstone_late_write(app):
    receipt = save(app, "rollback evidence " * 250)
    drain(app)
    ref = facts(app, receipt)[0]
    rows = app.vectors.client.query(
        collection_name=app.vectors.collection,
        filter='target["memory"]["memory_id"] == "' + ref.memory_id + '"',
        output_fields=["target", "vector"],
        limit=100,
        consistency_level="Strong",
        timeout=10,
    )
    assert len(rows) > 1
    item = app.remember.get(context(app), ref.memory_id)
    app.remember.delete(
        context(app),
        ref.memory_id,
        DeleteRequest(expected_revision=item.object_revision, reason="forget event"),
    )
    drain(app)
    actual = app.vectors.client.query(
        collection_name=app.vectors.collection,
        filter='target["memory"]["memory_id"] == "' + ref.memory_id + '"',
        output_fields=["target"],
        limit=100,
        consistency_level="Strong",
        timeout=10,
    )
    assert actual == []
    with app.foundation.uow.transaction() as tx:
        assert all(
            tx.read(
                app.vectors.projection_namespace,
                ProjectionTarget.model_validate(row["target"]).vector_id,
            )["deleted"]
            for row in rows
        )
    from aether_agent_memory.remember.contracts.models import ProjectionRequest

    with pytest.raises(FoundationError):
        asyncio.run(
            app.projections.project(
                context(app),
                ProjectionRequest(
                    operation_id="late",
                    target=ProjectionTarget.model_validate(rows[0]["target"]),
                    vector=tuple(rows[0]["vector"]),
                    deadline_at=context(app).deadline_at,
                ),
            )
        )


def test_unicode_token_chunks_cover_every_character(app):
    text = "😀汉字\n\t e\u0301 " * 30
    pieces = chunks(text, app.remember.tokenizer.count, 8)
    assert "".join(s for _, _, s in pieces) == text
    assert all(text[a:b] == s and app.remember.tokenizer.count(s) <= 8 for a, b, s in pieces)


def test_explicit_review_creates_semantic_without_overwriting_episode(app):
    receipt = save(app, "Retries must be bounded")
    drain(app)
    episode = facts(app, receipt)[0]

    class Review:
        async def review_episodes(self, ctx, episodes, items, policy_version):
            assert tuple(i.ref for i in episodes) == (episode,)
            assert episodes[0].content == "Retries must be bounded"
            return ExtractionResult(
                candidates=(
                    CandidateFact(
                        text=items[0].content,
                        kind="semantic",
                        sources=items[0].sources,
                        evidence_status="supported",
                    ),
                ),
                model_id="review",
                policy_version=policy_version,
            )

    app.remember.extraction = Review()
    task_id = app.remember.distill(context(app), (episode,))
    drain(app)
    task = app.foundation.diagnostics.task(context(app), task_id)
    assert task.state == "succeeded"
    with app.foundation.uow.transaction() as tx:
        output = tx.get(task.result_ref)["memories"][0]
    assert app.remember.get(context(app), output["memory_id"]).kind == "semantic"
    assert app.remember.get(context(app), episode.memory_id).kind == "episodic"


def test_partial_batch_invalidation_reschedules_valid_input(app):
    app.remember.policy = app.remember.policy.model_copy(update={"consolidation_messages": 2})
    first = observe(app, "first event")
    second = observe(app, "second event")
    app.remember.lifecycle(
        context(app),
        first.memories[0].memory_id,
        LifecycleRequest(expected_version=1, target="archived", reason="hide first"),
    )
    drain(app)
    with app.foundation.uow.transaction() as tx:
        pending = tx.read("remember_pending", second.memories[0].memory_id)
    assert pending["state"] == "processed"
    assert recall(app, query="second event").groups


def test_batch_model_inputs_are_bounded_and_evidence_offsets_stay_original(app):
    class Batch:
        sizes = []

        async def extract_batch(self, ctx, items, policy_version):
            self.sizes.append(sum(app.remember.tokenizer.count(i.content) for i in items))
            return ExtractionResult(
                candidates=(), model_id="bounded", policy_version=policy_version
            )

    provider = Batch()
    app.remember.extraction = provider
    app.remember.policy = app.remember.policy.model_copy(update={"extraction_chunk_tokens": 32})
    receipt = save(app, "events and conditions " * 160)
    drain(app)
    assert len(provider.sizes) > 1 and max(provider.sizes) <= 32
    assert facts(app, receipt) == []


def test_qualified_artifact_used_with_original_fallback(app):
    class Batch:
        supports_representations = True
        saw_summary = False
        saw_original = False

        def __init__(self, fail_summary):
            self.fail_summary = fail_summary

        async def extract_batch(self, ctx, items, policy_version, representations=None):
            if representations:
                self.saw_summary = True
                if self.fail_summary:
                    raise ValueError("summary lacks exact original quote")
            else:
                self.saw_original = True
            return ExtractionResult(
                candidates=(), model_id="summary", policy_version=policy_version
            )

    receipt = save(app, "original fact " * 30)
    drain(app)
    item = app.remember.get(context(app), receipt.memories[0].memory_id)
    location = asyncio.run(
        app.remember.bodies.persist(context(app), item.ref.scope, "original fact")
    )
    with app.foundation.uow.transaction() as tx:
        tx.write(
            "remember_artifacts",
            app.remember.refkey(item.ref),
            {
                "published": True,
                "source_hash": item.content_hash,
                "location": location.model_copy(update={"kind": "artifact"}).model_dump(
                    mode="json"
                ),
            },
        )
    for fail in (False, True):
        provider = Batch(fail)
        app.remember.extraction = provider
        task = app.remember.reprocess(context(app), item.ref.memory_id)
        drain(app)
        assert app.foundation.diagnostics.task(context(app), task).state == "succeeded"
        assert provider.saw_summary and provider.saw_original == fail
