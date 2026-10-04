"""Executable examples for the database-backed Remember workflow and failure boundaries."""

import asyncio

import pytest
from remember_helpers import app as app
from remember_helpers import context, drain, facts, recall, save

from aether_agent_memory.remember.basic.comparison import ConservativeComparison
from aether_agent_memory.remember.contracts.models import (
    CandidateFact,
    DeleteRequest,
    ExtractionResult,
    FactEvidence,
    MemoryKind,
)
from aether_agent_memory.runtime.foundation.common import FoundationError


def test_batch_model_schema_repair_is_bounded_and_checkpointed(app):
    class Repair:
        calls = 0

        async def extract_batch(self, ctx, items, policy_version):
            self.calls += 1
            if self.calls < 3:
                return {"invalid": "model response"}
            return ExtractionResult(
                candidates=(), model_id="mock_repaired", policy_version=policy_version
            )

    model = Repair()
    app.remember.extraction = model
    receipt = save(app, "Input that needs schema repair")
    drain(app)
    assert facts(app, receipt) == []
    assert model.calls == 3
    with app.foundation.uow.transaction() as tx:
        assert len(tx.rows("remember_extraction_parts")) == 1


def test_invalid_model_exhausts_budget_without_publishing(app):
    class Broken:
        calls = 0

        async def extract_batch(self, ctx, items, policy_version):
            self.calls += 1
            return {"not": "an ExtractionResult"}

    model = Broken()
    app.remember.extraction = model
    app.remember.policy = app.remember.policy.model_copy(update={"max_model_calls": 3})
    receipt = save(app, "Never publish unvalidated output")
    drain(app)
    status = app.remember.processing(context(app), receipt.memories[0].memory_id)
    assert status["state"] == "failed"
    assert model.calls == 3
    assert status["derived_memory_ids"] == []


def test_successful_reprocessing_retains_failure_history_but_recovers_state(app):
    class Fail:
        async def extract(self, ctx, request):
            raise ValueError("mock malformed output")

    app.remember.extraction = Fail()
    receipt = save(app, "Recovered event")
    drain(app)
    memory_id = receipt.memories[0].memory_id
    assert app.remember.processing(context(app), memory_id)["state"] == "failed"
    app.remember.extraction = Semantic()
    task_id = app.remember.reprocess(context(app), memory_id)
    drain(app)
    assert app.foundation.diagnostics.task(context(app), task_id).state == "succeeded"
    result = app.remember.processing(context(app), memory_id)
    assert result["state"] == "completed"
    assert result["historical_failed_tasks"] >= 1


def test_review_keeps_two_episodes_from_same_original_and_current_content(app):
    class TwoEvents:
        async def extract(self, ctx, request):
            return ExtractionResult(
                candidates=tuple(
                    CandidateFact(
                        text=text,
                        sources=(request.source,),
                        evidence_status="supported",
                        event_key=key,
                    )
                    for key, text in (("first", "First failed."), ("second", "Second succeeded."))
                ),
                model_id="mock_two_events",
                policy_version=request.policy_version,
            )

    app.remember.extraction = TwoEvents()
    receipt = save(app, "First failed. Second succeeded. Always verify results.")
    drain(app)
    episodes = tuple(facts(app, receipt))
    assert len(episodes) == 2

    class Review:
        async def review_episodes(self, ctx, items, originals, policy_version):
            assert tuple(i.ref for i in items) == episodes
            assert [i.content for i in items] == ["First failed.", "Second succeeded."]
            assert len(originals) == 1
            return ExtractionResult(
                candidates=(
                    CandidateFact(
                        text="Always verify results.",
                        kind="semantic",
                        sources=originals[0].sources,
                        evidence_status="supported",
                    ),
                ),
                model_id="mock_review",
                policy_version=policy_version,
            )

    app.remember.extraction = Review()
    task_id = app.remember.distill(context(app), episodes)
    drain(app)
    task = app.foundation.diagnostics.task(context(app), task_id)
    assert task.state == "succeeded"
    with app.foundation.uow.transaction() as tx:
        result = tx.get(task.result_ref)
        relation = tx.read("remember_relations", result["memories"][0]["memory_id"])
    assert relation["derived_from"] == [r.model_dump(mode="json") for r in episodes]
    assert all(app.remember.get(context(app), r.memory_id).status == "active" for r in episodes)


def test_cache_fill_racing_source_revocation_is_removed(app):
    class Cache:
        content = None

        async def put(self, scope, text):
            self.content = text
            with app.foundation.uow.transaction() as tx:
                source_id, _ = tx.rows("remember_sources")[0]
            app.remember.revoke_source(
                context(app),
                source_id,
                DeleteRequest(expected_revision=1, reason="concurrent revoke"),
            )
            return True

        async def delete(self, scope, digest):
            self.content = None

        async def get(self, scope, digest):
            return self.content

    cache = Cache()
    app.remember.bodies.cache = cache
    receipt = save(app, "revoked during cache admission")
    assert cache.content is None
    body = asyncio.run(app.remember.read_body(context(app), receipt.memories[0]))
    assert body.outcome == "excluded"


class Semantic:
    async def extract(self, ctx, request):
        return ExtractionResult(
            candidates=(
                CandidateFact(
                    text=request.text,
                    sources=(request.source,),
                    evidence_status="supported",
                    kind="semantic",
                    fact_key="deployment_policy",
                    importance_category="fact",
                ),
            ),
            model_id="explicit_test_adapter",
            policy_version=request.policy_version,
        )


def test_authorization_does_not_require_body_replica_or_available_p2(app, monkeypatch):
    receipt = save(app, "metadata independent authorization")
    bodies = app.remember.bodies
    ctx = context(app)
    with app.foundation.uow.transaction() as tx:
        item = app.remember.current(tx, receipt.memories[0].memory_id)
        location = bodies.location(item.ref.scope, item.content)
    assert bodies.remote_only and not bodies.path(location).exists()
    cache = bodies.cache
    cache.delete_sync(item.ref.scope, item.content_hash)
    bodies.verified.clear()
    bodies.verified_bytes = 0

    def unavailable(**kwargs):
        raise OSError("controlled Ceph read outage")

    with monkeypatch.context() as fault:
        fault.setattr(bodies.p2.transport.client, "get_object", unavailable)
        ctx = context(app)
        with app.foundation.uow.transaction() as tx:
            assert (
                app.remember.final_guard(tx, ctx, (item.ref,), "recall").items[0].decision
                == "allowed"
            )
        with pytest.raises(FoundationError) as error:
            asyncio.run(app.remember.read_body(context(app), item.ref))
        assert error.value.code == "DEPENDENCY_UNAVAILABLE"
    assert asyncio.run(app.remember.read_body(context(app), item.ref)).content == item.content


def test_task_input_is_frozen_when_record_lifecycle_changes(app):
    receipt = save(app, "frozen task input")
    task = app.foundation.diagnostics.task(context(app), receipt.task_ids[0])
    with app.foundation.uow.transaction() as tx:
        before = tx.get(task.input_ref)
        item = app.remember.current(tx, receipt.memories[0].memory_id)
        app.remember.change(tx, item, importance=0.8)
        assert tx.get(task.input_ref) == before
        assert task.input_ref.object_type == "processing_input"


def test_policy_is_frozen_before_worker_changes(app):
    app.remember.policy = app.remember.policy.model_copy(update={"projection_chunk_tokens": 64})
    receipt = save(app, "one event with detailed evidence " * 30)
    app.remember.policy = app.remember.policy.model_copy(update={"projection_chunk_tokens": 8})
    drain(app)
    ref = facts(app, receipt)[0]
    with app.foundation.uow.transaction() as tx:
        manifest = tx.read("remember_manifests", app.remember.refkey(ref))
    assert any(c["end_char"] - c["start_char"] > 8 for c in manifest["chunks"])


def test_concurrent_create_detects_space_change_and_reuses_fact(app):
    app.remember.extraction = Semantic()
    owner = app.remember

    class CompetingWriter(ConservativeComparison):
        fired = False

        async def compare(self, ctx, candidate, existing):
            if not self.fired:
                self.fired = True
                await owner.bodies.persist(ctx, receipt.memories[0].scope, candidate.text)
                with owner.uow.transaction() as tx:
                    other = owner.new_memory(
                        tx,
                        "competing_fact",
                        receipt.memories[0].scope,
                        candidate.text,
                        candidate.sources,
                        MemoryKind.SEMANTIC,
                    )
                    owner.emit(tx, ctx, other, "saved")
            return await super().compare(ctx, candidate, existing)

    owner.comparison = CompetingWriter()
    receipt = save(app, "Deploy only after tests pass")
    drain(app)
    assert facts(app, receipt)[0].memory_id == "competing_fact"
    with owner.uow.transaction() as tx:
        assert tx.read("remember_comparison_retries", receipt.task_ids[0])["attempts"] == 1


def test_unrelated_evidence_does_not_approve_model_claim(app):
    class Hallucination:
        async def extract(self, ctx, request):
            return ExtractionResult(
                candidates=(
                    CandidateFact(
                        text="The release succeeded",
                        sources=(request.source,),
                        evidence_status="supported",
                        evidence=(
                            FactEvidence(
                                source=request.source,
                                start_char=0,
                                end_char=len(request.text),
                                quote=request.text,
                            ),
                        ),
                    ),
                ),
                model_id="bad_test_adapter",
                policy_version=request.policy_version,
            )

    app.remember.extraction = Hallucination()
    receipt = save(app, "The release failed")
    drain(app)
    task = app.foundation.diagnostics.task(context(app), receipt.task_ids[0])
    assert task.state != "succeeded"
    assert (
        app.remember.processing(context(app), receipt.memories[0].memory_id)["derived_memory_ids"]
        == []
    )


def test_same_event_addition_produces_new_version_not_new_event(app):
    class Event:
        async def extract(self, ctx, request):
            return ExtractionResult(
                candidates=(
                    CandidateFact(
                        text=request.text,
                        sources=(request.source,),
                        evidence_status="supported",
                        event_key="deploy_20260923",
                    ),
                ),
                model_id="event_test_adapter",
                policy_version=request.policy_version,
            )

    from aether_agent_memory.remember.basic.comparison import OccurrenceVerdict

    class SameOccurrenceVerifier:
        async def verify_occurrence(self, ctx, candidate, target, evidence):
            return OccurrenceVerdict(
                equivalent=False,
                same_identity=True,
                preserves_conditions=True,
                same_occurrence=True,
                candidate_quote=candidate.text,
                existing_quote=target.content,
                candidate_context_quote=evidence.candidate_context.quote,
                existing_context_quote=evidence.existing_context.quote,
                reason="explicitly verified same deployment and additive cause",
            )

    from aether_agent_memory.remember.basic.comparison import ComparisonDecision

    class AdditiveComparison:
        async def compare(self, ctx, candidate, existing):
            prior = next((item for item in existing if item.content in candidate.text), None)
            return ComparisonDecision(
                outcome="amend" if prior else "create",
                target_id=prior.ref.memory_id if prior else None,
                reason="proposed additive detail requires separate occurrence verification",
            )

    app.remember.comparison = AdditiveComparison()
    app.remember.equivalence_verifier = SameOccurrenceVerifier()
    app.remember.extraction = Event()
    first = save(app, "Deployment failed.")
    drain(app)
    second = save(app, "Deployment failed. Cause: connection pool exhausted.")
    drain(app)
    old, new = facts(app, first)[0], facts(app, second)[0]
    assert old.memory_id == new.memory_id and new.version == old.version + 1
    ctx = context(app)
    with app.foundation.uow.transaction() as tx:
        assert app.remember.final_guard(tx, ctx, (old,), "recall").items[0].decision == "excluded"


def test_retract_one_of_two_independent_sources_revalidates_remaining(app):
    app.remember.extraction = Semantic()
    first = save(app, "Deployment requires tests")
    drain(app)
    second = save(app, "Deployment requires tests")
    drain(app)
    ref = facts(app, first)[0]
    app.remember.revoke_source(
        context(app),
        first.source.source_id,
        DeleteRequest(expected_revision=1, reason="source withdrawn"),
    )
    ctx = context(app)
    with app.foundation.uow.transaction() as tx:
        assert app.remember.final_guard(tx, ctx, (ref,), "recall").items[0].decision == "excluded"
    drain(app)
    item = app.remember.get(context(app), ref.memory_id)
    assert [s.source_id for s in item.sources] == [second.source.source_id]
    ctx = context(app)
    with app.foundation.uow.transaction() as tx:
        assert app.remember.final_guard(tx, ctx, (ref,), "recall").items[0].decision == "allowed"


def test_single_source_retraction_stays_blocked_without_new_evidence(app):
    first = save(app, "Single source event")
    drain(app)
    ref = facts(app, first)[0]
    app.remember.revoke_source(
        context(app),
        first.source.source_id,
        DeleteRequest(expected_revision=1, reason="source withdrawn"),
    )
    drain(app)
    ctx = context(app)
    with app.foundation.uow.transaction() as tx:
        assert app.remember.final_guard(tx, ctx, (ref,), "recall").items[0].decision == "excluded"
        assert tx.read("remember_relations", ref.memory_id)["evidence_state"] == "unsupported"


def test_signal_versions_and_importance_are_independent_of_projection(app):
    first = save(app, "A remembered event")
    drain(app)
    ref = facts(app, first)[0]
    with app.foundation.uow.transaction() as tx:
        state = tx.read("remember_signal_state", ref.memory_id)
        assert state["change_seq"] > state["semantic_revision"]
    assert recall(app, query="remembered").groups
