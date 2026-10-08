"""AET-16 RC-QUA-05–08: real candidate search and Remember authority.

Uses the existing test-owned Azure/Temporal fixtures. B response faults are
injected only at MemoryQualificationPort; lifecycle changes use Remember APIs.
"""

import asyncio
from contextlib import contextmanager
from contextvars import ContextVar
from threading import Event

import pytest
import remember_helpers
from remember_helpers import context, drain, facts, save, source
from test_flows import app as app
from test_generation_candidates import setup
from test_remember_boundary import projection

from aether_agent_memory.remember.basic.boundary import RememberBoundary
from aether_agent_memory.remember.contracts.foundation import CandidateQualificationTarget
from aether_agent_memory.remember.contracts.models import CorrectionRequest, DeleteRequest

pytestmark = [pytest.mark.integration, pytest.mark.p0]
remember_app = remember_helpers.app


class QualificationTransactionGate:
    """Coordinate real PostgreSQL transactions without replacing persistence.

    Let a delete attempt reach the database while qualification owns its first
    transaction. If qualification opens another transaction, force that read
    after the deletion commits, exposing a loss of batch atomicity.
    """

    def __init__(self, uow):
        self.uow = uow
        self.qualifying = ContextVar("qualification_test", default=False)
        self.first_read = Event()
        self.delete_attempted = Event()
        self.deleted = Event()

    def __getattr__(self, name):
        return getattr(self.uow, name)

    @contextmanager
    def transaction(self):
        qualifying = self.qualifying.get()
        first = qualifying and not self.first_read.is_set()
        if qualifying and not first:
            assert self.deleted.wait(10), "concurrent delete did not finish"
        if not qualifying:
            self.delete_attempted.set()
        with self.uow.transaction() as tx:
            yield tx
            if first:
                self.first_read.set()
                assert self.delete_attempted.wait(10), "delete never attempted during the batch"


@pytest.mark.parametrize(
    "decision,coverage",
    [
        ("allowed", "complete"),
        ("excluded", "complete"),
        ("unverifiable", "partial"),
    ],
)
def test_rc_qua_05_decision_controls_candidates_and_coverage(app, decision, coverage):
    ctx, search, authority, request = setup(app)
    authority.decisions["m1"] = decision
    result = asyncio.run(search.search(ctx, request))
    assert [c.memory.memory_id for c in result.candidates] == (
        ["m1", "m2"] if decision == "allowed" else ["m2", "m3"]
    )
    assert result.coverage == coverage
    page = app.foundation.telemetry.page(ctx, ctx.trace_id)
    returned = [
        row["output"]
        for row in page["records"]
        if row["node"] == "recall.candidates.qualify" and row["phase"] == "returned"
    ]
    assert returned, "qualification decisions must remain observable"
    reasons = {proof["reason_code"] for batch in returned for proof in batch.values()}
    assert decision in reasons
    if decision != "allowed":
        assert ({"excluded", "unverifiable"} - {decision}).isdisjoint(reasons)


@pytest.mark.parametrize("converges", [True, False])
def test_rc_qua_06_conflicting_evidence_gets_one_bounded_reverification(app, converges):
    ctx, search, authority, request = setup(app)
    first_id = next(iter(authority.proofs))
    authority.proofs[first_id][1]["relations_revision"] = 2
    original = authority.qualify

    async def controlled_authority(ctx, targets, purpose):
        if converges and authority.calls:
            authority.proofs[first_id][1]["relations_revision"] = 1
        return await original(ctx, targets, purpose)

    authority.qualify = controlled_authority
    result = asyncio.run(search.search(ctx, request))
    assert [c.memory.memory_id for c in result.candidates] == (
        ["m1", "m2"] if converges else ["m2", "m3"]
    )
    assert result.coverage == ("complete" if converges else "partial")
    # Exactly one replay of the two conflicting chunks, regardless of convergence.
    assert len(authority.calls) == 3
    assert authority.calls[1] == authority.calls[0]
    if converges:
        assert len(result.candidates[0].hits) == 2
        assert result.candidates[0].guard.relations_revision == 1


def test_rc_qua_06_check_time_alone_does_not_trigger_reverification(app):
    ctx, search, authority, request = setup(app)
    first_id = next(iter(authority.proofs))
    authority.proofs[first_id][1]["checked_at"] = "2026-09-21T02:00:00.000Z"
    result = asyncio.run(search.search(ctx, request))
    assert [c.memory.memory_id for c in result.candidates] == ["m1", "m2"]
    assert result.coverage == "complete" and len(authority.calls) == 2


def test_rc_qua_07_unpublished_memory_has_no_qualification(remember_app):
    receipt = save(remember_app, "Unpublished qualification fixture")
    target = CandidateQualificationTarget(
        memory=receipt.memories[0],
        memory_source="working",
        generation="not_published",
        model_space=remember_app.embedding.space.model_space,
        body_hash="a" * 64,
        input_hash="b" * 64,
        vector_id="c" * 64,
        chunk_index=0,
    )
    boundary = RememberBoundary(remember_app.remember)
    result = asyncio.run(boundary.qualify(context(remember_app), (target,), "recall"))[0]
    assert result.target == target and result.decision == "excluded"
    assert result.manifest is None and result.guard is None


@pytest.mark.parametrize("change", ["correct", "delete"])
def test_rc_qua_07_old_version_and_tombstone_are_excluded(remember_app, change):
    receipt = save(remember_app, "Qualification lifecycle fixture")
    drain(remember_app)
    ref = facts(remember_app, receipt)[0]
    _, target = projection(remember_app, ref)
    boundary = RememberBoundary(remember_app.remember)
    before = asyncio.run(boundary.qualify(context(remember_app), (target,), "recall"))[0]
    assert before.decision == "allowed"
    if change == "correct":
        asyncio.run(
            remember_app.remember.correct_async(
                context(remember_app),
                ref.memory_id,
                CorrectionRequest(
                    expected_version=ref.version,
                    content="Corrected qualification fact",
                    source=source("correction"),
                    reason="verified correction",
                ),
            )
        )
        current = remember_app.remember.get(context(remember_app), ref.memory_id)
        assert current.ref.version > ref.version
    else:
        remember_app.remember.delete(
            context(remember_app),
            ref.memory_id,
            DeleteRequest(expected_revision=before.guard.object_revision, reason="remove"),
        )
    # Recheck before physical vector cleanup: residue cannot grant qualification.
    after = asyncio.run(boundary.qualify(context(remember_app), (target,), "recall"))[0]
    assert after.target == target and after.decision == "excluded"
    assert after.manifest is None and after.guard is None


def test_rc_qua_08_batch_evidence_is_consistent_and_next_batch_sees_deletion(
    remember_app, monkeypatch
):
    receipt = save(remember_app, "Same batch authority evidence " * 300)
    drain(remember_app)
    ref = facts(remember_app, receipt)[0]
    manifest, first = projection(remember_app, ref)
    assert len(manifest.chunks) > 1, "fixture must cover multiple targets for one memory"
    targets = tuple(
        first.model_copy(
            update={
                "chunk_index": chunk.chunk_index,
                "vector_id": chunk.vector_id,
                "input_hash": chunk.input_hash,
            }
        )
        for chunk in manifest.chunks
    )
    boundary = RememberBoundary(remember_app.remember)
    before = asyncio.run(boundary.qualify(context(remember_app), targets, "recall"))
    assert tuple(r.target for r in before) == targets
    assert all(r.decision == "allowed" and r.manifest == manifest for r in before)
    assert len({r.guard.model_dump_json(exclude={"checked_at"}) for r in before}) == 1
    gate = QualificationTransactionGate(remember_app.remember.uow)
    monkeypatch.setattr(remember_app.remember, "uow", gate)
    qualification_ctx = context(remember_app)
    deletion_ctx = context(remember_app)

    async def qualify_during_delete():
        token = gate.qualifying.set(True)
        try:
            return await boundary.qualify(qualification_ctx, targets, "recall")
        finally:
            gate.qualifying.reset(token)

    async def delete_after_read():
        assert await asyncio.to_thread(gate.first_read.wait, 10), "batch read never completed"
        try:
            await asyncio.to_thread(
                remember_app.remember.delete,
                deletion_ctx,
                ref.memory_id,
                DeleteRequest(
                    expected_revision=before[0].guard.object_revision, reason="batch changed"
                ),
            )
        finally:
            gate.deleted.set()

    async def race_deletion():
        racing, _ = await asyncio.gather(
            qualify_during_delete(),
            delete_after_read(),
        )
        return racing

    racing = asyncio.run(race_deletion())
    assert tuple(r.target for r in racing) == targets
    assert gate.first_read.is_set() and gate.delete_attempted.is_set() and gate.deleted.is_set()
    assert all(r.decision == "allowed" and r.manifest == manifest for r in racing)
    assert len({r.guard.model_dump_json(exclude={"checked_at"}) for r in racing}) == 1
    monkeypatch.setattr(remember_app.remember, "uow", gate.uow)
    after = asyncio.run(boundary.qualify(context(remember_app), targets, "recall"))
    assert tuple(r.target for r in after) == targets
    assert all(r.decision == "excluded" and r.manifest is None and r.guard is None for r in after)
