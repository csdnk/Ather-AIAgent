"""Remember integrates with unmodified generation Recall and existing Operate."""

import asyncio

import pytest
from remember_helpers import app as app
from remember_helpers import context, drain, facts, recall, save
from test_remember_lcm_complete import advance, enroll
from test_working_summaries import configure, save_long

from aether_agent_memory.remember.basic.boundary import RememberBoundary
from aether_agent_memory.remember.basic.service import memory_ref
from aether_agent_memory.remember.contracts.foundation import (
    CandidateQualificationTarget,
    ContextGuardRequest,
    ProjectionManifest,
)
from aether_agent_memory.remember.contracts.models import DeleteRequest, ProjectionTarget
from aether_agent_memory.runtime.foundation.common import FoundationError


def projection(app, ref):
    with app.foundation.uow.transaction() as tx:
        manifest = ProjectionManifest.model_validate(
            tx.read("remember_manifests", app.remember.refkey(ref))
        )
    chunk = manifest.chunks[0]
    return manifest, CandidateQualificationTarget(
        memory=ref,
        generation=manifest.generation,
        model_space=manifest.model_space,
        body_hash=manifest.body_hash,
        chunk_index=chunk.chunk_index,
        vector_id=chunk.vector_id,
        input_hash=chunk.input_hash,
    )


def test_chunk_qualification_checks_every_identity_without_reading_body(app, monkeypatch):
    receipt = save(app, "Exact source evidence " * 300)
    drain(app)
    ref = facts(app, receipt)[0]
    manifest, target = projection(app, ref)
    boundary = RememberBoundary(app.remember)
    with monkeypatch.context() as patch:

        def forbidden(*args):
            raise AssertionError("qualification must only read metadata")

        patch.setattr(app.remember.bodies, "read_local", forbidden)
        valid = asyncio.run(boundary.qualify(context(app), (target,), "recall"))[0]
        assert valid.decision == "allowed" and valid.manifest == manifest
        for field, value in {
            "generation": "other",
            "model_space": "other",
            "body_hash": "f" * 64,
            "chunk_index": 9999,
            "vector_id": "f" * 64,
            "input_hash": "f" * 64,
        }.items():
            changed = target.model_copy(update={field: value})
            assert (
                asyncio.run(boundary.qualify(context(app), (changed,), "recall"))[0].decision
                == "excluded"
            )
    assert (
        asyncio.run(boundary.qualify(context(app, "bob"), (target,), "recall"))[0].decision
        == "excluded"
    )
    with pytest.raises(FoundationError):
        asyncio.run(boundary.qualify(context(app), (target, target), "recall"))


def test_context_guard_rechecks_metadata_and_rolls_back_in_callers_transaction(app):
    receipt = save(app)
    drain(app)
    ref = facts(app, receipt)[0]
    boundary = RememberBoundary(app.remember)
    manifest, _ = projection(app, ref)
    view = boundary.relations(context(app), (ref,))
    expected = ContextGuardRequest(expected=view.guards, manifests=(manifest,))
    ctx = context(app)
    with app.foundation.uow.transaction() as tx:
        current = boundary.revalidate_context(tx, ctx, expected)
        assert current[0].body_hash == manifest.body_hash
    ctx = context(app)
    with (
        pytest.raises(FoundationError, match="body or relation changed"),
        app.foundation.uow.transaction() as tx,
    ):
        item = app.remember.current(tx, ref.memory_id)
        app.remember.change(tx, item, importance=0.8)
        boundary.revalidate_context(tx, ctx, expected)
    assert (
        boundary.relations(context(app), (ref,)).guards[0].object_revision
        == view.guards[0].object_revision
    )
    ctx = context(app)
    with pytest.raises(FoundationError), app.foundation.uow.transaction() as tx:
        raw = tx.get(memory_ref(ref, versioned=True))
        tx.put_if_revision(
            memory_ref(ref, versioned=True),
            {**raw, "projection_state": "stale"},
            tx.revision(memory_ref(ref, versioned=True)),
        )
        boundary.revalidate_context(tx, ctx, expected)


def test_reinforcement_reads_committed_feedback_before_archive_even_before_delivery(
    app, monkeypatch
):
    receipt = save(app, "Experiment result and rollback")
    drain(app)
    ref = facts(app, receipt)[0]
    enroll(app, ref, delete_after_archive_hours=24)
    advance(app, monkeypatch, 24 * 30)
    pack = recall(app, query="Experiment rollback")
    assert pack.groups
    # Do not dispatch the access event: Remember reconciles committed outbox evidence.
    assert app.remember.retention.periodic() == 0
    first = app.remember.retention.read(context(app), ref.memory_id)["retention"]
    assert first["strength"] == 1 and first["reinforcements"] == 1
    again = app.remember.retention.read(context(app), ref.memory_id)["retention"]
    assert again["reinforcements"] == 1
    advance(app, monkeypatch, 1)
    recall(app, query="Experiment rollback")
    recent = app.remember.retention.read(context(app), ref.memory_id)["retention"]
    assert recent["strength"] == 1 and recent["half_life_hours"] == first["half_life_hours"]


def test_generation_projection_verification_and_cleanup_cover_both_local_indexes(app):
    receipt = save(app, "Complete projection manifest " * 300)
    drain(app)
    ref = facts(app, receipt)[0]
    _, target = projection(app, ref)
    legacy = ProjectionTarget.model_validate(target.model_dump())
    with app.foundation.uow.transaction() as tx:
        tx.raw.delete("p3_rf_" + app.vectors.projection_namespace, "system", target.vector_id)
    result = asyncio.run(app.projections.inspect(context(app), legacy, "verify"))
    assert not result.searchable
    item = app.remember.get(context(app), ref.memory_id)
    app.remember.delete(
        context(app),
        ref.memory_id,
        DeleteRequest(expected_revision=item.object_revision, reason="remove"),
    )
    drain(app)
    with app.foundation.uow.transaction() as tx:
        assert not any(
            row["data"]["target"]["memory"] == ref.model_dump(mode="json") and not row["deleted"]
            for _, row in tx.rows(app.vectors.projection_namespace)
        )
    rows = app.vectors.client.query(
        collection_name=app.vectors.collection,
        filter='target["memory"]["memory_id"] == "' + ref.memory_id + '"',
        output_fields=["target"],
        limit=100,
        consistency_level="Strong",
        timeout=10,
    )
    assert rows == []


def test_summary_cache_fill_racing_deletion_does_not_leave_a_readable_replica(app):
    configure(app)

    cache = app.remember.bodies.cache
    receipt, _, _ = save_long(app)
    put = cache.put
    touched = []

    async def concurrent_delete(scope, text):
        result = await put(scope, text)
        item = app.remember.get(context(app), receipt.memories[0].memory_id)
        touched.append(item.content_hash)
        app.remember.delete(
            context(app),
            item.ref.memory_id,
            DeleteRequest(expected_revision=item.object_revision, reason="cancel"),
        )
        return result

    cache.put = concurrent_delete
    drain(app)
    assert touched and asyncio.run(cache.get(receipt.memories[0].scope, touched[0])) is None
