import asyncio
from hashlib import sha256

import pytest
from remember_helpers import app as app
from remember_helpers import context, drain, facts, recall, save

from aether_agent_memory.remember.contracts.models import (
    CandidateFact,
    CorrectionRequest,
    DocumentInput,
    ExtractionResult,
    LifecycleRequest,
    ReflectionRequest,
    RememberRequest,
    RetentionRequest,
)
from aether_agent_memory.runtime.contracts.models import Permission, ScopeSelector
from aether_agent_memory.runtime.foundation.common import FoundationError, later


def enroll(app, ref, **values):
    item = app.remember.get(context(app), ref.memory_id)
    return app.remember.retention.configure(
        context(app),
        ref.memory_id,
        RetentionRequest(
            expected_version=item.ref.version,
            expected_object_revision=item.object_revision,
            completed=True,
            reason="approved retention",
            **values,
        ),
    )


def advance(app, monkeypatch, hours):
    timestamp = later(app.foundation.identity.clock(), int(hours * 3600))
    monkeypatch.setattr(app.foundation.identity, "clock", lambda: timestamp)


def episode(app, text="A temporary experiment completed"):
    receipt = save(app, text)
    drain(app)
    return receipt, facts(app, receipt)[0]


def test_retention_two_phase_delete_and_cleanup_keep_p2(app, monkeypatch):
    _, ref = episode(app)
    enroll(app, ref, delete_after_archive_hours=24)
    advance(app, monkeypatch, 24 * 35)
    assert app.remember.retention.periodic() == 1
    assert app.remember.get(context(app), ref.memory_id).status == "archived"
    assert app.remember.retention.periodic() == 0  # grace starts at archive commit
    advance(app, monkeypatch, 25)
    assert app.remember.retention.periodic() == 1
    with pytest.raises(FoundationError) as error:
        app.remember.get(context(app), ref.memory_id)
    assert error.value.code == "MEMORY_GONE"
    assert app.remember.retention.periodic() == 0
    monkeypatch.undo()  # Resume Workers on real server time after domain clock simulation.
    drain(app)
    with app.foundation.uow.transaction() as tx:
        cleanups = [
            r["record"] for _, r in tx.rows("tasks") if r["record"]["kind"] == "remember.cleanup"
        ]
        assert cleanups and all(t["state"] == "succeeded" for t in cleanups)
        audits = [r for _, r in tx.rows("remember_retention_audit")]
        assert any(r["state"] == "deleted" and not r["physical_erasure"] for r in audits)
    assert app.remember.bodies.read_local(
        app.remember.bodies.location(ref.scope, "A temporary experiment completed")
    )


@pytest.mark.parametrize("protection", ["hold", "important", "dependent", "disabled"])
def test_retention_protects_against_automatic_disposal(app, monkeypatch, protection):
    receipt, ref = episode(app)
    chosen = receipt.memories[0] if protection == "dependent" else ref
    if protection == "important":
        with app.foundation.uow.transaction() as tx:
            app.remember.change(tx, app.remember.current(tx, chosen.memory_id), importance=0.8)
    enroll(
        app,
        chosen,
        delete_after_archive_hours=24,
        legal_hold=protection == "hold",
        enabled=protection != "disabled",
    )
    advance(app, monkeypatch, 24 * 40)
    app.remember.retention.periodic()
    advance(app, monkeypatch, 48)
    app.remember.retention.periodic()
    current = app.remember.get(context(app), chosen.memory_id)
    assert current.status == ("archived" if protection == "dependent" else "active")


def test_packed_reinforcement_delays_archive_and_reactivation_disarms_delete(app, monkeypatch):
    _, ref = episode(app)
    enroll(app, ref, delete_after_archive_hours=24)
    advance(app, monkeypatch, 24 * 30)
    recall(app, query="temporary experiment")
    assert app.remember.retention.periodic() == 0
    assert app.remember.retention.read(context(app), ref.memory_id)["retention"]["strength"] == 1
    advance(app, monkeypatch, 24 * 60)
    assert app.remember.retention.periodic() == 1
    item = app.remember.get(context(app), ref.memory_id)
    app.remember.lifecycle(
        context(app),
        ref.memory_id,
        LifecycleRequest(
            expected_version=1,
            expected_object_revision=item.object_revision,
            target="active",
            reason="resume",
        ),
    )
    advance(app, monkeypatch, 24 * 60)
    assert app.remember.retention.periodic() == 0
    assert app.remember.get(context(app), ref.memory_id).status == "active"


def test_expiry_blocks_immediately_and_worker_materializes_state(app, monkeypatch):
    _, ref = episode(app)
    enroll(
        app,
        ref,
        enabled=False,
        legal_hold=True,
        expires_at=later(app.foundation.identity.clock(), 3600),
    )
    advance(app, monkeypatch, 2)
    ctx = context(app)
    with app.foundation.uow.transaction() as tx:
        assert app.remember.final_guard(tx, ctx, (ref,), "recall").items[0].reason == "expired"
    assert app.remember.retention.periodic() == 1
    assert app.remember.get(context(app), ref.memory_id).status == "expired"
    assert app.remember.retention.periodic() == 0
    monkeypatch.undo()  # Resume Workers on real server time after domain clock simulation.
    drain(app)
    from remember_helpers import source

    updated = asyncio.run(
        app.remember.correct_async(
            context(app),
            ref.memory_id,
            CorrectionRequest(
                expected_version=1,
                content="New evidence confirms a new experiment",
                source=source("new_evidence"),
                reason="renew with new evidence",
            ),
        )
    )
    new = app.remember.get(context(app), ref.memory_id)
    assert updated.memories[0].version == 2 and new.status == "active"
    assert new.expires_at is None
    assert app.remember.retention.periodic() == 0


def test_policy_cas_delete_permission_and_revocation(app, monkeypatch):
    _, ref = episode(app)
    item = app.remember.get(context(app), ref.memory_id)
    request = RetentionRequest(
        expected_version=1,
        expected_object_revision=item.object_revision,
        delete_after_archive_hours=24,
        reason="expiry",
    )
    ctx = context(app)
    first = app.remember.retention.configure(ctx, ref.memory_id, request)
    assert app.remember.retention.configure(ctx, ref.memory_id, request) == first
    with pytest.raises(FoundationError) as intent:
        app.remember.retention.configure(
            ctx, ref.memory_id, request.model_copy(update={"expires_at": None})
        )
    assert intent.value.code == "IDEMPOTENCY_CONFLICT"
    # An older deployed request had only the original six fields. Safe retries
    # must still return its receipt after the additive policy schema upgrade.
    from aether_agent_memory.runtime.foundation.common import fingerprint
    from aether_agent_memory.runtime.foundation.requests import request_key

    legacy_ctx = context(app)
    legacy_request = request.model_copy(update={"delete_after_archive_hours": None})
    legacy_fields = {
        "expected_version",
        "expected_object_revision",
        "enabled",
        "completed",
        "archive_after_idle_hours",
        "reason",
    }
    with app.foundation.uow.transaction() as tx:
        tx.write(
            "remember_operations",
            request_key(legacy_ctx, "retention_" + ref.memory_id),
            {
                "signature": fingerprint(
                    legacy_request.model_dump(mode="json", include=legacy_fields)
                ),
                "result": {"enabled": True, "version": 1},
            },
        )
    assert app.remember.retention.configure(legacy_ctx, ref.memory_id, legacy_request) == {
        "enabled": True,
        "version": 1,
    }
    with pytest.raises(FoundationError) as error:
        app.remember.retention.configure(context(app), ref.memory_id, request)
    assert error.value.code == "VERSION_CONFLICT"
    principal = ctx.principal.model_copy(
        update={
            "auth_epoch": 2,
            "permissions": tuple(p for p in ctx.principal.permissions if p != Permission.DELETE),
        }
    )
    app.foundation.identity.provision([(sha256(b"alice").hexdigest(), principal)])
    advance(app, monkeypatch, 24 * 60)
    assert app.remember.retention.periodic() == 0
    with pytest.raises(FoundationError):
        enroll(app, ref, delete_after_archive_hours=24)


class Review:
    async def review_episodes(self, ctx, episodes, items, policy_version):
        return ExtractionResult(
            candidates=(
                CandidateFact(
                    text=items[0].content,
                    kind="semantic",
                    sources=items[0].sources,
                    evidence_status="supported",
                ),
            ),
            model_id="test_review",
            policy_version=policy_version,
        )


def test_automatic_reflection_creates_semantic_once_keeps_episodes(app, monkeypatch):
    refs = [episode(app, f"Experiment {i}: retries must be bounded")[1] for i in range(3)]
    app.remember.extraction = Review()
    request = ReflectionRequest(
        selection=ScopeSelector(session_id="session_1"), reason="project retrospective"
    )
    ctx = context(app)
    first = app.remember.reflection.configure(ctx, request)
    assert app.remember.reflection.configure(ctx, request) == first
    assert app.remember.reflection.periodic() == 1
    assert app.remember.reflection.periodic() == 0
    drain(app)
    with app.foundation.uow.transaction() as tx:
        tasks = [
            r["record"] for _, r in tx.rows("tasks") if r["record"]["kind"] == "remember.distill"
        ]
        assert len(tasks) == 1 and tasks[0]["state"] == "succeeded"
        all_items = [app.remember.current(tx, mid) for mid, _ in tx.rows("remember_current")]
        assert len([i for i in all_items if i.kind == "semantic"]) == 1
    assert all(app.remember.get(context(app), r.memory_id).kind == "episodic" for r in refs)
    advance(app, monkeypatch, 25)
    assert app.remember.reflection.periodic() == 0


def test_reflection_independent_evidence_scope_and_missing_provider(app, monkeypatch):
    for _ in range(3):
        episode(app, "Identical event repeated")
    request = ReflectionRequest(selection=ScopeSelector(session_id="session_1"), reason="review")
    app.remember.reflection.configure(context(app), request)
    assert app.remember.reflection.periodic() == 0
    with app.foundation.uow.transaction() as tx:
        assert tx.rows("remember_reflection_policies")[0][1]["status"] == "provider_unavailable"
    app.remember.extraction = Review()
    advance(app, monkeypatch, 25)
    assert app.remember.reflection.periodic() == 0
    with app.foundation.uow.transaction() as tx:
        assert tx.rows("remember_reflection_policies")[0][1]["status"] == "waiting_evidence"
    with pytest.raises(FoundationError):
        app.remember.reflection.configure(
            context(app, "carol"),
            request.model_copy(update={"selection": ScopeSelector(user_id="alice")}),
        )


def test_new_policy_http_contracts(app):
    from fastapi.testclient import TestClient

    from aether_agent_memory.runtime.flows.http import create_app

    body = {"selection": {"session_id": "session_1"}, "reason": "retrospective"}
    headers = {"Authorization": "Bearer alice", "X-Operation-ID": "reflection_http"}
    from temporal_test_support import http_execution

    http_execution(app, app.execution.endpoint)
    with TestClient(create_app(app)) as client:
        url = "/p3/remember/reflection"
        assert client.post(url, json=body).status_code == 401
        first = client.post(url, headers=headers, json=body)
        assert first.status_code == 200 and first.json()["revision"] == 1
        receipt = client.get(
            "/p3/mutation-receipts/reflection_http",
            params={"kind": "remember.reflection"},
            headers=headers,
        )
        assert receipt.status_code == 200, receipt.text
        assert receipt.json()["state"] == "committed"
        assert receipt.json()["receipt"]["operation_id"] == "reflection_http"
        assert (
            client.get(url, headers=headers, params={"session_id": "session_1"}).json()["revision"]
            == 1
        )
        assert (
            client.get(
                url, headers={"Authorization": "Bearer bob"}, params={"session_id": "session_1"}
            ).json()["status"]
            == "not_enrolled"
        )
        assert client.post(url, headers=headers, json=body).json() == first.json()
        other = {**headers, "X-Operation-ID": "new_reflection"}
        assert client.post(url, headers=other, json=body).status_code == 409
        assert client.post(url, headers=other, json={**body, "period_hours": 0}).status_code == 422


def test_document_invalid_utf8_is_typed_rejection(app):
    import asyncio

    from remember_helpers import source

    class BadDocument:
        async def read(self, ctx, request):
            return b"\xff\xfe"

    app.remember.documents = {"docs": BadDocument()}
    request = RememberRequest(
        selection=ScopeSelector(session_id="session_1"),
        source=source("bad_document"),
        content=DocumentInput(
            kind="document",
            provider_id="docs",
            document_id="bad",
            document_version="1",
            expected_hash=sha256(b"\xff\xfe").hexdigest(),
        ),
    )
    with pytest.raises(FoundationError) as error:
        asyncio.run(app.remember.save(context(app), request))
    assert error.value.code == "INVALID_ARGUMENT"
    with app.foundation.uow.transaction() as tx:
        assert not tx.rows("remember_current")
