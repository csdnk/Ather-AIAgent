"""Working projection shares the verified generation pipeline, never index placeholders."""

import asyncio

import pytest
from remember_helpers import app as app
from remember_helpers import context, drain, facts, save, source
from test_flows import app as _basic_app
from test_remember_boundary import projection
from test_working_summaries import configure, save_long

from aether_agent_memory.remember.basic.boundary import RememberBoundary
from aether_agent_memory.remember.basic.projection import MilvusProjection
from aether_agent_memory.remember.contracts.models import (
    CorrectionRequest,
    DeleteRequest,
    LifecycleRequest,
    ProjectionRequest,
    ProjectionTarget,
    RememberRequest,
    TextInput,
)
from aether_agent_memory.runtime.contracts.models import ErrorCode, ScopeSelector
from aether_agent_memory.runtime.foundation.common import FoundationError

basic_app = _basic_app


def readiness(app, user="alice", selection=None):
    return asyncio.run(
        RememberBoundary(app.remember).projection_readiness(
            context(app, user), selection or ScopeSelector(), "working"
        )
    )


def observe(app):
    return asyncio.run(
        app.remember.save(
            context(app),
            RememberRequest(
                source=source(),
                selection=ScopeSelector(session_id="session_1"),
                content=TextInput(kind="text", text="A short observation."),
                trigger="observe",
            ),
        )
    )


def test_saved_working_is_readable_before_async_projection_and_published_after(app):
    receipt = save(app)
    ref = receipt.memories[0]
    item = app.remember.get(context(app), ref.memory_id)
    assert item.projection_state == "pending"
    body = asyncio.run(app.remember.read_body(context(app), ref))
    assert body.outcome == "read" and body.content == item.content
    kinds = [app.foundation.diagnostics.task(context(app), t).kind for t in receipt.task_ids]
    assert "remember.project" in kinds
    assert readiness(app).pending_count == 1
    drain(app)
    item = app.remember.get(context(app), ref.memory_id)
    assert item.projection_state == "ready"
    state = readiness(app)
    assert state.ready_count == 1 and state.pending_count == state.failed_count == 0
    assert state.complete
    manifest, target = projection(app, ref)
    target = target.model_copy(update={"memory_source": "working"})
    qualified = asyncio.run(
        RememberBoundary(app.remember).qualify(context(app), (target,), "recall")
    )[0]
    assert qualified.decision == "allowed" and qualified.manifest == manifest
    with app.foundation.uow.transaction() as tx:
        rows = [
            r
            for _, r in tx.rows("generation_vectors")
            if r["hit"]["memory"] == ref.model_dump(mode="json")
        ]
    assert rows and all(r["hit"]["memory_source"] == "working" for r in rows)


def test_working_projection_does_not_wait_for_consolidation_threshold(app):
    app.remember.policy = app.remember.policy.model_copy(
        update={
            "consolidation_messages": 32,
            "consolidation_tokens": 8192,
            "consolidation_seconds": 600,
        }
    )
    receipt = observe(app)
    kinds = [app.foundation.diagnostics.task(context(app), t).kind for t in receipt.task_ids]
    assert kinds == ["remember.project"]
    drain(app)
    assert app.remember.get(context(app), receipt.memories[0].memory_id).projection_state == "ready"


def test_readiness_is_metadata_only_and_scope_authorized(app, monkeypatch):
    receipt = observe(app)

    def forbidden(*args):
        raise AssertionError("readiness cannot read body")

    monkeypatch.setattr(app.remember.bodies, "read_local", forbidden)
    assert readiness(app).pending_count == 1
    assert readiness(app, "bob").pending_count == 0
    assert readiness(app, selection=ScopeSelector(session_id="other")).pending_count == 0
    assert receipt.saved


def test_qualification_rejects_working_mislabeled_as_long_term(app):
    receipt = save(app)
    drain(app)
    _, target = projection(app, receipt.memories[0])
    result = asyncio.run(RememberBoundary(app.remember).qualify(context(app), (target,), "recall"))[
        0
    ]
    assert result.decision == "excluded" and result.reason_code == "memory_source_mismatch"


def test_summary_publishes_only_new_actual_summary_version(app):
    configure(app)
    receipt, _, _ = save_long(app)
    assert readiness(app).pending_count == 1
    drain(app)
    item = app.remember.get(context(app), receipt.memories[0].memory_id)
    assert item.ref.version == 2 and item.projection_state == "ready"
    with app.foundation.uow.transaction() as tx:
        rows = [
            r
            for _, r in tx.rows("generation_vectors")
            if r["hit"]["memory"]["memory_id"] == item.ref.memory_id
        ]
    assert rows and all(r["hit"]["memory"]["version"] == 2 for r in rows)


def test_failed_summary_never_projects_descriptor_and_readiness_reports_failure(app):
    configure(app)

    class UnsupportedSummary:
        async def select(self, *args):
            return ("unsupported invented summary",)

    app.remember.summaries.provider = UnsupportedSummary()
    receipt, _, _ = save_long(app)
    drain(app)
    item = app.remember.get(context(app), receipt.memories[0].memory_id)
    state = readiness(app)
    assert state.failed_count == 1 and not state.complete
    assert item.projection_state != "ready"
    with app.foundation.uow.transaction() as tx:
        assert not [
            r
            for _, r in tx.rows("generation_vectors")
            if r["hit"]["memory"]["memory_id"] == item.ref.memory_id
        ]


def test_correction_invalidates_old_working_generation_and_indexes_new_body(app):
    receipt = save(app)
    drain(app)
    old_ref = receipt.memories[0]
    _, old_target = projection(app, old_ref)
    old_target = old_target.model_copy(update={"memory_source": "working"})
    corrected = asyncio.run(
        app.remember.correct_async(
            context(app),
            old_ref.memory_id,
            CorrectionRequest(
                expected_version=1,
                content="I now prefer tea.",
                source=source("corrected"),
                reason="new preference",
            ),
        )
    )
    assert app.remember.get(context(app), old_ref.memory_id).projection_state == "pending"
    result = asyncio.run(
        RememberBoundary(app.remember).qualify(context(app), (old_target,), "recall")
    )[0]
    assert result.decision == "excluded"
    drain(app)
    assert app.remember.get(context(app), old_ref.memory_id).ref == corrected.memories[0]
    assert readiness(app).ready_count == 1


def test_archive_and_reactivate_working_require_verified_projection(app):
    receipt = save(app)
    drain(app)
    ref = receipt.memories[0]
    archived = app.remember.lifecycle(
        context(app),
        ref.memory_id,
        LifecycleRequest(expected_version=1, target="archived", reason="done"),
    )
    assert archived.projection_state == "stale" and readiness(app).ready_count == 0
    active = app.remember.lifecycle(
        context(app),
        ref.memory_id,
        LifecycleRequest(expected_version=1, target="active", reason="resume"),
    )
    assert active.projection_state == "pending" and readiness(app).pending_count == 1
    drain(app)
    assert readiness(app).ready_count == 1


def test_reindex_legacy_working_is_idempotent_without_extraction(app):
    app.remember.policy = app.remember.policy.model_copy(update={"consolidation_messages": 32})
    receipt = observe(app)
    ref = receipt.memories[0]
    with app.foundation.uow.transaction() as tx:
        item = app.remember.current(tx, ref.memory_id)
        app.remember.change(tx, item, projection_state="not_required")
    ctx = context(app, operation="reindex_legacy")
    task_id = app.remember.reindex(ctx, ref.memory_id)
    assert app.remember.reindex(ctx, ref.memory_id) == task_id
    assert app.foundation.diagnostics.task(ctx, task_id).kind == "remember.project"
    with pytest.raises(FoundationError):
        app.remember.reindex(context(app, "bob"), ref.memory_id)
    drain(app)
    assert app.remember.get(context(app), ref.memory_id).ref == ref
    assert readiness(app).ready_count == 1
    with app.foundation.uow.transaction() as tx:
        assert not [r for _, r in tx.rows("tasks") if r["record"]["kind"] == "remember.extract"]


def test_basic_remember_working_publishes_a_verified_generation(basic_app):
    receipt = save(basic_app)
    drain(basic_app)
    state = readiness(basic_app)
    assert state.ready_count == 1 and state.complete
    _, target = projection(basic_app, receipt.memories[0])
    target = target.model_copy(update={"memory_source": "working"})
    qualified = asyncio.run(basic_app.remember.qualify(context(basic_app), (target,), "recall"))[0]
    assert qualified.decision == "allowed"


def test_reindex_http_requires_authorization_and_only_queues_processing(app):
    from fastapi.testclient import TestClient

    from aether_agent_memory.runtime.flows.http import create_app

    receipt = observe(app)
    endpoint = f"/p3/remember/{receipt.memories[0].memory_id}/reindex"
    from temporal_test_support import http_execution

    http_execution(app, app.execution.endpoint)
    with TestClient(create_app(app)) as client:
        assert client.post(endpoint).status_code == 401
        assert client.post(endpoint, headers={"Authorization": "Bearer bob"}).status_code == 403
        response = client.post(endpoint, headers={"Authorization": "Bearer alice"})
    assert response.status_code == 200 and response.json()["phase"] == "processing"
    assert (
        app.foundation.diagnostics.task(context(app), response.json()["task_id"]).kind
        == "remember.project"
    )


def test_legacy_long_term_projection_without_source_keeps_identity_and_replay(app):
    receipt = save(app)
    drain(app)
    _, candidate = projection(app, facts(app, receipt)[0])
    target = ProjectionTarget.model_validate(candidate.model_dump())
    with app.foundation.uow.transaction() as tx:
        row = tx.read("recall_vectors", target.vector_id)
        row["target"].pop("memory_source")
        tx.write("recall_vectors", target.vector_id, row)
        chunk = tx.read("generation_vectors", target.vector_id)
        chunk["hit"].pop("memory_source")
        tx.write("generation_vectors", target.vector_id, chunk)
    ctx = context(app)
    assert asyncio.run(app.projections.inspect(ctx, target, "legacy")).state == "verified"
    request = ProjectionRequest(
        operation_id="legacy",
        target=target,
        vector=tuple(row["vector"]),
        deadline_at=ctx.deadline_at,
    )
    assert asyncio.run(app.projections.project(ctx, request)).state == "verified"


def test_milvus_legacy_source_default_is_normalized_for_inspect_and_replay(app):
    receipt = save(app)
    drain(app)
    _, candidate = projection(app, facts(app, receipt)[0])
    target = ProjectionTarget.model_validate(candidate.model_dump())
    with app.foundation.uow.transaction() as tx:
        row = tx.read("recall_vectors", target.vector_id)
        row["target"].pop("memory_source")
        tx.write("milvus_projections", target.vector_id, {"data": row, "deleted": False})

    class HistoricalClient:
        def __init__(self):
            self.row = row

        def query(self, **kwargs):
            return [self.row]

        def search(self, **kwargs):
            return [[{"entity": {"target": {"memory_source": "long_term", **self.row["target"]}}}]]

        def upsert(self, **kwargs):
            self.row = kwargs["data"][0]

        def close(self):
            pass

    provider = MilvusProjection(
        app.foundation.uow,
        app.foundation.identity,
        app.model_space,
        len(row["vector"]),
        uri="test://isolated",
        client=HistoricalClient(),
    )
    provider.prepared = True
    ctx = context(app)
    try:
        assert asyncio.run(provider.inspect(ctx, target, "legacy")).state == "verified"
        request = ProjectionRequest(
            operation_id="legacy",
            target=target,
            vector=tuple(row["vector"]),
            deadline_at=ctx.deadline_at,
        )
        assert asyncio.run(provider.project(ctx, request)).state == "verified"
    finally:
        provider.close()


def test_projection_failure_is_reported_and_explicit_reindex_recovers(app, monkeypatch):
    receipt = observe(app)
    original = app.remember.embedding.embed

    async def fail(ctx, request):
        raise FoundationError(ErrorCode.CONTRACT_VIOLATION, "invalid provider output")

    monkeypatch.setattr(app.remember.embedding, "embed", fail)
    drain(app)
    assert readiness(app).failed_count == 1 and not readiness(app).complete
    monkeypatch.setattr(app.remember.embedding, "embed", original)
    app.remember.reindex(context(app), receipt.memories[0].memory_id)
    drain(app)
    assert readiness(app).ready_count == 1 and readiness(app).complete


def test_late_working_projection_cannot_publish_after_delete(app, monkeypatch):
    receipt = observe(app)
    ref = receipt.memories[0]
    original = app.remember.embedding.embed

    async def delete_during_embedding(ctx, request):
        item = app.remember.get(context(app), ref.memory_id)
        app.remember.delete(
            context(app),
            ref.memory_id,
            DeleteRequest(expected_revision=item.object_revision, reason="deleted while indexing"),
        )
        return await original(ctx, request)

    monkeypatch.setattr(app.remember.embedding, "embed", delete_during_embedding)
    drain(app)
    assert readiness(app).ready_count == 0
    with app.foundation.uow.transaction() as tx:
        assert not tx.read("remember_manifests", app.remember.refkey(ref))
        assert not tx.rows("generation_vectors")


def test_reindex_unready_summary_explicitly_refuses_without_starting_processing(app):
    configure(app)
    receipt, _, _ = save_long(app)
    with app.foundation.uow.transaction() as tx:
        before = tx.rows("tasks")
    with pytest.raises(FoundationError) as exc:
        app.remember.reindex(context(app), receipt.memories[0].memory_id)
    assert exc.value.code == "REQUEST_IN_PROGRESS"
    with app.foundation.uow.transaction() as tx:
        assert tx.rows("tasks") == before
