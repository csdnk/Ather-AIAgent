"""Real Remember/P2 pipeline, deterministic model providers and isolated Redis double."""

import asyncio

import pytest
from remember_helpers import app as app
from remember_helpers import context, drain, recall, source

from aether_agent_memory.remember.basic.content import RedisBodyCache
from aether_agent_memory.remember.basic.service import memory_ref
from aether_agent_memory.remember.basic.sources import PreparedDocument
from aether_agent_memory.remember.basic.summaries import ExtractiveSummary
from aether_agent_memory.remember.contracts.models import (
    CandidateFact,
    CorrectionRequest,
    DeleteRequest,
    DocumentInput,
    ExtractionResult,
    LifecycleRequest,
    RememberRequest,
    TextInput,
)
from aether_agent_memory.runtime.contracts.models import RecordRef, ScopeSelector
from aether_agent_memory.runtime.foundation.common import FoundationError
from aether_agent_memory.runtime.foundation.requests import text_hash


class FactsFromOriginal:
    def __init__(self, fact="Refund timeout is 30 seconds.", kind="semantic"):
        self.fact, self.kind, self.inputs = fact, kind, []

    async def extract(self, ctx, request):
        self.inputs.append(request.text)
        return ExtractionResult(
            candidates=(
                CandidateFact(
                    text=self.fact,
                    sources=(request.source,),
                    kind=self.kind,
                    evidence_status="supported",
                ),
            )
            if self.fact in request.text
            else (),
            model_id="mock-source-extraction",
            policy_version=request.policy_version,
        )


def configure(app, extractor=None):
    app.remember.policy = app.remember.policy.model_copy(
        update={
            "working_summary_min_bytes": 512,
            "working_summary_max_chars": 256,
            "source_page_chars": 256,
        }
    )
    app.remember.extraction = extractor or FactsFromOriginal()


def save_long(app, text=None, request=None, operation=None):
    text = (
        text
        or "Architecture background and request tracing.\n" * 30 + "Refund timeout is 30 seconds."
    )
    request = request or RememberRequest(
        source=source(),
        selection=ScopeSelector(session_id="session_1"),
        content=TextInput(kind="text", text=text),
        task_context="Review the refund timeout.",
    )
    receipt = asyncio.run(app.remember.save(context(app, operation=operation), request))
    return receipt, text, request


def durable(app):
    with app.foundation.uow.transaction() as tx:
        return [
            app.remember.decode(tx, tx.get(RecordRef.model_validate(p)))
            for _, p in tx.rows("remember_current")
            if tx.get(RecordRef.model_validate(p))["kind"] != "working"
        ]


def test_save_does_not_wait_for_summary_original_immediately_readable(app):
    configure(app)

    class NeverCalled:
        async def select(self, *args):
            raise AssertionError("save must not call a model")

    app.remember.summaries.provider = NeverCalled()
    receipt, text, _ = save_long(app)
    working = app.remember.get(context(app), receipt.memories[0].memory_id)
    assert working.status == "active" and working.content != text
    assert "Review the refund timeout" in working.content
    assert working.content_hash != receipt.source.content_hash == text_hash(text)
    read = asyncio.run(app.remember.read_source(context(app), receipt.source, len(text) - 28))
    assert read["content"] == text[-28:] and read["is_complete"] is False
    assert (
        app.remember.processing(context(app), working.ref.memory_id)["working_summary"]["state"]
        == "pending"
    )
    assert durable(app) == []


@pytest.mark.parametrize(
    "fact,kind",
    [
        ("Refund timeout is 30 seconds.", "semantic"),
        ("Deployment failed on Tuesday.", "episodic"),
        ("All migrations require approval.", "semantic"),
    ],
)
def test_summary_version_and_long_term_evidence_use_original(app, fact, kind):
    extraction = FactsFromOriginal(fact, kind)
    configure(app, extraction)
    text = "Context details unrelated to the final fact.\n" * 35 + fact
    receipt, _, _ = save_long(app, text)
    original_created = app.remember.get(context(app), receipt.memories[0].memory_id).created_at
    drain(app)
    working = app.remember.get(context(app), receipt.memories[0].memory_id)
    assert working.ref.version == 2 and working.supersedes == receipt.memories[0]
    assert working.created_at == original_created
    assert len(working.content) < len(text)
    assert any(fact in value for value in extraction.inputs)
    memories = durable(app)
    assert len(memories) == 1 and memories[0].content == fact
    assert memories[0].projection_state == "ready"
    assert recall(app, query=fact).outcome == "available"
    with app.foundation.uow.transaction() as tx:
        old = tx.get(memory_ref(receipt.memories[0], versioned=True))
        assert old["status"] == "superseded"
        relation = tx.read("remember_relations", memories[0].ref.memory_id)
        evidence = relation["evidence"][0]
        assert text[evidence["start_char"] : evidence["end_char"]] == fact
    details = app.remember.processing(context(app), working.ref.memory_id)
    assert details["working_summary"]["state"] == "ready"
    assert details["working_summary"]["is_complete"] is False


def test_summary_failure_falls_back_to_original_and_can_retry_without_duplicates(app):
    configure(app)

    class Hallucinating:
        calls = 0

        async def select(self, *args):
            self.calls += 1
            return ("An invented requirement absent from the source.",)

    provider = Hallucinating()
    app.remember.summaries.provider = provider
    receipt, _, _ = save_long(app)
    drain(app)
    info = app.remember.processing(context(app), receipt.memories[0].memory_id)
    assert provider.calls == 3
    assert info["working_summary"]["state"] == "failed"
    assert info["state"] == "completed_with_summary_failure"
    assert len(durable(app)) == 1
    app.remember.summaries.provider = ExtractiveSummary()
    app.remember.reprocess(context(app), receipt.memories[0].memory_id)
    drain(app)
    assert app.remember.get(context(app), receipt.memories[0].memory_id).ref.version == 2
    assert len(durable(app)) == 1


def test_real_unicode_range_does_not_load_whole_object_and_checks_hash(app, monkeypatch):
    configure(app)
    text = "甲😀乙\n" * 400
    receipt, _, _ = save_long(app, text)
    p2 = app.remember.bodies.p2
    calls = []
    original = p2.read_range

    async def ranged(key, start, end):
        calls.append(end - start)
        return await original(key, start, end)

    async def full_read_forbidden(*args):
        raise AssertionError("full source read on interactive range path")

    monkeypatch.setattr(p2, "read_range", ranged)
    monkeypatch.setattr(p2, "get_object", full_read_forbidden)
    result = asyncio.run(app.remember.read_source(context(app), receipt.source, 254, 262))
    assert result["content"] == text[254:262]
    assert len(calls) == 2 and sum(calls) < len(text.encode())

    async def corrupt(key, start, end):
        return b"x" * (end - start)

    monkeypatch.setattr(p2, "read_range", corrupt)
    with pytest.raises(FoundationError) as exc:
        asyncio.run(app.remember.read_source(context(app), receipt.source, 0, 10))
    assert exc.value.code == "CONTRACT_VIOLATION"


def test_source_read_scope_version_budget_and_revoke_during_read(app, monkeypatch):
    configure(app)
    receipt, _, _ = save_long(app)
    for user in ("bob", "carol"):
        with pytest.raises(FoundationError):
            asyncio.run(app.remember.read_source(context(app, user), receipt.source))
    forged = receipt.source.model_copy(update={"source_version": 2})
    with pytest.raises(FoundationError):
        asyncio.run(app.remember.read_source(context(app), forged))
    with pytest.raises(FoundationError):
        asyncio.run(app.remember.read_source(context(app), receipt.source, -1, 2))
    original = app.remember.bodies.p2.read_range
    revoked = False

    async def revoke(key, start, end):
        nonlocal revoked
        content = await original(key, start, end)
        if not revoked:
            revoked = True
            app.remember.revoke_source(
                context(app),
                receipt.source.source_id,
                DeleteRequest(expected_revision=1, reason="withdraw"),
            )
        return content

    monkeypatch.setattr(app.remember.bodies.p2, "read_range", revoke)
    with pytest.raises(FoundationError) as exc:
        asyncio.run(app.remember.read_source(context(app), receipt.source, 0, 8))
    assert exc.value.code == "MEMORY_GONE"


def test_correction_while_summary_runs_cannot_overwrite_new_source(app):
    configure(app)
    receipt, _, _ = save_long(app)
    new_text = "Updated architecture.\n" * 40 + "Refund timeout is 60 seconds."

    class CorrectDuringModel:
        changed = False

        async def select(self, ctx, text, task_context, max_chars):
            if not self.changed:
                self.changed = True
                item = app.remember.get(context(app), receipt.memories[0].memory_id)
                await app.remember.correct_async(
                    context(app),
                    item.ref.memory_id,
                    CorrectionRequest(
                        expected_version=item.ref.version,
                        content=new_text,
                        source=source("revision"),
                        reason="correct file",
                    ),
                )
            return (text[: min(80, max_chars)],)

    app.remember.summaries.provider = CorrectDuringModel()
    drain(app)
    item = app.remember.get(context(app), receipt.memories[0].memory_id)
    assert item.ref.version == 3  # correction v2, its own summary v3
    assert item.sources[0] != receipt.source
    assert "Updated architecture" in item.content
    info = app.remember.processing(context(app), item.ref.memory_id)
    assert info["working_summary"]["memory"] == item.ref.model_dump(mode="json")
    assert info["working_summary"]["source"] == item.sources[0].model_dump(mode="json")


def test_delete_during_summary_prevents_publication(app):
    configure(app)
    receipt, _, _ = save_long(app)

    class DeleteDuringModel:
        deleted = False

        async def select(self, ctx, text, task_context, max_chars):
            if not self.deleted:
                self.deleted = True
                item = app.remember.get(context(app), receipt.memories[0].memory_id)
                app.remember.delete(
                    context(app),
                    item.ref.memory_id,
                    DeleteRequest(expected_revision=item.object_revision, reason="delete"),
                )
            return (text[:80],)

    app.remember.summaries.provider = DeleteDuringModel()
    drain(app)
    with app.foundation.uow.transaction() as tx:
        row = tx.get(memory_ref(receipt.memories[0], versioned=True))
        assert row["status"] == "deleted"
    assert durable(app) == []


def test_summary_redis_eviction_and_working_archive_do_not_erase_source_or_es(app):
    configure(app)

    async def scenario():
        import fakeredis.aioredis

        redis = fakeredis.aioredis.FakeRedis()
        app.remember.bodies.cache = RedisBodyCache(redis, app.remember.policy)
        request = RememberRequest(
            source=source(),
            selection=ScopeSelector(session_id="s"),
            content=TextInput(
                kind="text", text="Background.\n" * 70 + "Refund timeout is 30 seconds."
            ),
        )
        receipt = await app.remember.save(context(app), request)
        initial = app.remember.get(context(app), receipt.memories[0].memory_id)
        assert (
            await app.remember.bodies.cache.get(initial.ref.scope, initial.content_hash)
            == initial.content
        )
        assert (
            await app.remember.bodies.cache.get(initial.ref.scope, receipt.source.content_hash)
            is None
        )
        # This multi-stage integration asserts convergence, not the host's 12s default wait.
        await app.drain(timeout_seconds=60)
        item = app.remember.get(context(app), initial.ref.memory_id)
        assert (
            await app.remember.bodies.cache.get(item.ref.scope, item.content_hash) == item.content
        )
        await redis.flushdb()
        loaded = await app.remember.load_async(context(app), (item.ref,))
        assert loaded.items[0].content == item.content
        app.remember.lifecycle(
            context(app),
            item.ref.memory_id,
            LifecycleRequest(expected_version=item.ref.version, target="archived", reason="done"),
        )
        assert (await app.remember.read_source(context(app), receipt.source))[
            "content"
        ] == request.content.text
        await redis.aclose()

    asyncio.run(scenario())
    assert len(durable(app)) == 1 and recall(app, query="refund timeout").outcome == "available"


def test_prepared_file_separates_original_file_hash_from_parsed_text(app):
    configure(app)
    parsed = "Refund timeout is 30 seconds.\n" * 30
    document = DocumentInput(
        kind="document",
        provider_id="files",
        document_id="design",
        document_version="7",
        expected_hash=text_hash("binary original"),
    )

    class P2FileProvider:
        async def acquire_text(self, ctx, request):
            return PreparedDocument(
                document=document,
                original_storage_ref="p2://files/design@7",
                original_bytes=30000,
                media_type="application/pdf",
                parsed_text=parsed,
                parsed_hash=text_hash(parsed),
                parser_version="mock-parser-v1",
            )

    app.remember.documents["files"] = P2FileProvider()
    request = RememberRequest(source=source("file"), selection=ScopeSelector(), content=document)
    receipt, _, _ = save_long(app, request=request)
    read = asyncio.run(app.remember.read_source(context(app), receipt.source))
    assert receipt.source.content_hash == text_hash(parsed) != document.expected_hash
    assert read["document"]["document"]["expected_hash"] == document.expected_hash
    assert "parsed_text" not in read["document"]
    drain(app)
    assert len(durable(app)) == 1


def test_source_http_requires_authorization_and_returns_original_not_summary(app):
    from fastapi.testclient import TestClient

    from aether_agent_memory.runtime.flows.http import create_app

    configure(app)
    receipt, text, _ = save_long(app)
    from temporal_test_support import http_execution

    http_execution(app, app.execution.endpoint)
    with TestClient(create_app(app)) as client:
        endpoint = "/p3/sources/read-range?start=4&end=20"
        assert client.post(endpoint, json=receipt.source.model_dump(mode="json")).status_code == 401
        reply = client.post(
            endpoint,
            json=receipt.source.model_dump(mode="json"),
            headers={"Authorization": "Bearer alice"},
        )
        assert reply.status_code == 200 and reply.json()["content"] == text[4:20]
        assert (
            client.post(
                endpoint,
                json=receipt.source.model_dump(mode="json"),
                headers={"Authorization": "Bearer carol"},
            ).status_code
            == 403
        )


def test_task_context_changes_are_idempotency_conflicts(app):
    configure(app)
    receipt, _, request = save_long(app, operation="same_request")
    repeated = asyncio.run(app.remember.save(context(app, operation="same_request"), request))
    assert repeated == receipt
    with pytest.raises(FoundationError) as exc:
        asyncio.run(
            app.remember.save(
                context(app, operation="different_request"),
                request.model_copy(update={"task_context": "A different task"}),
            )
        )
    assert exc.value.code == "IDEMPOTENCY_CONFLICT"


def test_summary_timeout_is_bounded_and_source_read_does_not_reinforce(app):
    configure(app)
    app.remember.policy = app.remember.policy.model_copy(
        update={"summary_call_timeout_seconds": 0.01}
    )

    class Slow:
        calls = 0

        async def select(self, *args):
            self.calls += 1
            await asyncio.sleep(10)
            return ()

    slow = Slow()
    app.remember.summaries.provider = slow
    receipt, _, _ = save_long(app)
    before = app.remember.retention.read(context(app), receipt.memories[0].memory_id)["retention"]
    asyncio.run(app.remember.read_source(context(app), receipt.source, 0, 20))
    after = app.remember.retention.read(context(app), receipt.memories[0].memory_id)["retention"]
    assert before["anchor_hour"] == after["anchor_hour"]
    assert before["reinforcements"] == after["reinforcements"] == 0
    drain(app)
    assert slow.calls == 3 and len(durable(app)) == 1
    assert (
        app.remember.processing(context(app), receipt.memories[0].memory_id)["working_summary"][
            "state"
        ]
        == "failed"
    )


def test_original_based_episodic_to_semantic_reflection(app):
    rule = "Rollback requires an approval."

    class Review(FactsFromOriginal):
        async def review_episodes(self, ctx, episodes, originals, policy_version):
            assert all(e.kind == "episodic" for e in episodes)
            assert any(rule in original.content for original in originals)
            return ExtractionResult(
                candidates=(
                    CandidateFact(
                        text=rule,
                        sources=(originals[0].sources[0],),
                        kind="semantic",
                        evidence_status="supported",
                    ),
                ),
                model_id="mock-review",
                policy_version=policy_version,
            )

    configure(app, Review("Deployment failed on Tuesday.", "episodic"))
    receipt, _, _ = save_long(
        app, "Design history.\n" * 40 + "Deployment failed on Tuesday.\n" + rule
    )
    drain(app)
    episode = durable(app)[0]
    app.remember.distill(context(app), (episode.ref,))
    drain(app)
    assert {m.kind for m in durable(app)} == {"episodic", "semantic"}
    semantic = next(m for m in durable(app) if m.kind == "semantic")
    assert semantic.content == rule and semantic.sources == (receipt.source,)
    assert app.remember.get(context(app), episode.ref.memory_id).status == "active"


def test_failed_p2_acquisition_never_claims_saved(app):
    configure(app)

    class Unavailable:
        async def acquire_text(self, ctx, request):
            raise OSError("P2 unavailable")

    app.remember.documents["files"] = Unavailable()
    request = RememberRequest(
        source=source("file"),
        selection=ScopeSelector(),
        content=DocumentInput(
            kind="document",
            provider_id="files",
            document_id="doc",
            document_version="1",
            expected_hash=text_hash("original"),
        ),
    )
    with pytest.raises(FoundationError) as exc:
        save_long(app, request=request)
    assert exc.value.code == "DEPENDENCY_UNAVAILABLE"
    with app.foundation.uow.transaction() as tx:
        assert not tx.rows("remember_current") and not tx.rows("remember_sources")


def test_replayed_old_save_without_optional_task_context_still_matches(app):
    from aether_agent_memory.runtime.foundation.common import fingerprint
    from aether_agent_memory.runtime.foundation.requests import request_key

    request = RememberRequest(
        source=source("short"),
        selection=ScopeSelector(),
        content=TextInput(kind="text", text="Small text"),
    )
    ctx = context(app, operation="legacy")
    first = asyncio.run(app.remember.save(ctx, request))
    key = request_key(ctx, "remember.save")
    with app.foundation.uow.transaction() as tx:
        row = tx.read("remember_operations", key)
        row["signature"] = fingerprint(request.model_dump(mode="json", exclude={"task_context"}))
        tx.write("remember_operations", key, row)
    assert asyncio.run(app.remember.save(ctx, request)) == first


def test_summary_preserves_retention_policy_and_does_not_reset_decay(app):
    from aether_agent_memory.remember.contracts.models import RetentionRequest

    configure(app)
    receipt, _, _ = save_long(app)
    item = app.remember.get(context(app), receipt.memories[0].memory_id)
    before = app.remember.retention.read(context(app), item.ref.memory_id)["retention"]
    app.remember.retention.configure(
        context(app),
        item.ref.memory_id,
        RetentionRequest(
            expected_version=item.ref.version,
            expected_object_revision=item.object_revision,
            completed=True,
            legal_hold=True,
            reason="retain design",
        ),
    )
    drain(app)
    after = app.remember.retention.read(context(app), item.ref.memory_id)
    assert after["memory"]["version"] == after["policy"]["version"] == 2
    assert after["policy"]["legal_hold"] and after["policy"]["enabled"]
    assert after["retention"]["anchor_hour"] == before["anchor_hour"]
    assert after["retention"]["reinforcements"] == 0


def test_missing_document_extractor_does_not_turn_file_into_giant_episode(app):
    from aether_agent_memory.remember.basic.extraction import LiteralExtraction

    configure(app, LiteralExtraction())
    receipt, _, _ = save_long(app)
    drain(app)
    info = app.remember.processing(context(app), receipt.memories[0].memory_id)
    assert info["working_summary"]["state"] == "ready"
    assert info["state"] == "awaiting_extraction_provider"
    assert durable(app) == []
    app.remember.extraction = FactsFromOriginal()
    app.remember.reprocess(context(app), receipt.memories[0].memory_id)
    drain(app)
    assert len(durable(app)) == 1
    assert (
        app.remember.processing(context(app), receipt.memories[0].memory_id)["state"] == "completed"
    )
