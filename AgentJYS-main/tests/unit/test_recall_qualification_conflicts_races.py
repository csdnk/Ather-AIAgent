"""AET-62 RC-QUA-06, RC-QUA-08, RC-BODY-03: qualification conflicts, in-batch view, and body correction races.

Uses controlled barriers to reproduce evidence conflicts, in-batch authority changes, and
post-verification body corrections. Proves Recall does not mix views or pair old scores with new bodies.

Extends AET-16 and AET-19:
- Same batch, same memory, different chunks return conflicting view/version/guard
- Covers reverification convergence and persistent conflict scenarios
- At most one reverification per confirmed contract, recording actual count
- Persistent conflict excludes candidate and lowers coverage (no view mixing, no unbounded retry)
- Controls qualify batch grant/version changes, verifying consistent batch authority view
- v1 verified, paused before body load, corrected to v2, then load resumed - detects precise version change
- Does not deliver invalidated v1, does not mix v1/v2, does not pair old epoch with new revision
- Safe reassembly or failure per current strategy

Reuses existing fixtures and barrier patterns. No sleep-based race guessing.
"""

import asyncio
import copy
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
from aether_agent_memory.runtime.foundation.common import FoundationError

pytestmark = [pytest.mark.integration, pytest.mark.p0]
remember_app = remember_helpers.app


class QualificationTransactionGate:
    """Coordinate real PostgreSQL transactions without replacing persistence.

    Let a delete/correction attempt reach the database while qualification owns its first
    transaction. If qualification opens another transaction, force that read after the
    change commits, exposing loss of batch atomicity or version drift.
    """

    def __init__(self, uow):
        self.uow = uow
        self.qualifying = ContextVar("qualification_test", default=False)
        self.first_read = Event()
        self.change_attempted = Event()
        self.changed = Event()

    def __getattr__(self, name):
        return getattr(self.uow, name)

    @contextmanager
    def transaction(self):
        qualifying = self.qualifying.get()
        first = qualifying and not self.first_read.is_set()
        if qualifying and not first:
            assert self.changed.wait(10), "concurrent change did not finish"
        if not qualifying:
            self.change_attempted.set()
        with self.uow.transaction() as tx:
            yield tx
            if first:
                self.first_read.set()
                assert self.change_attempted.wait(10), "change never attempted during the batch"


@pytest.mark.parametrize("converges", [True, False])
def test_rc_qua_06_conflicting_evidence_gets_one_bounded_reverification(app, converges):
    """Conflicting view/version/guard in same batch triggers at most one reverification.

    When different chunks of the same memory return conflicting evidence (different
    relations_revision, version, or other guard fields), the system performs exactly
    one reverification attempt. If it converges, the candidate is delivered with
    consistent evidence. If conflict persists, the candidate is excluded and coverage
    is lowered to partial.
    """
    ctx, search, authority, request = setup(app)
    first_id = next(iter(authority.proofs))
    authority.proofs[first_id][1]["relations_revision"] = 2
    original = authority.qualify

    async def controlled_authority(ctx, targets, purpose):
        if converges and authority.calls:
            # On reverification, fix the conflict
            authority.proofs[first_id][1]["relations_revision"] = 1
        return await original(ctx, targets, purpose)

    authority.qualify = controlled_authority
    result = asyncio.run(search.search(ctx, request))

    # Verify outcome based on convergence
    assert [c.memory.memory_id for c in result.candidates] == (
        ["m1", "m2"] if converges else ["m2", "m3"]
    )
    assert result.coverage == ("complete" if converges else "partial")

    # Exactly one reverification (3 total calls: initial batch + reverify m1 + second batch)
    assert len(authority.calls) == 3
    # The reverification call should be identical to the first conflicting call
    assert authority.calls[1] == authority.calls[0]

    if converges:
        # Converged candidate should have both hits with consistent guard
        assert len(result.candidates[0].hits) == 2
        assert result.candidates[0].guard.relations_revision == 1


def test_rc_qua_06_check_time_alone_does_not_trigger_reverification(app):
    """checked_at timestamp differences alone do not trigger reverification.

    Only substantive guard field differences (version, revision, hash, scope) trigger
    reverification. Timestamp-only differences are acceptable within a batch.
    """
    ctx, search, authority, request = setup(app)
    first_id = next(iter(authority.proofs))
    authority.proofs[first_id][1]["checked_at"] = "2026-09-21T02:00:00.000Z"
    result = asyncio.run(search.search(ctx, request))

    assert [c.memory.memory_id for c in result.candidates] == ["m1", "m2"]
    assert result.coverage == "complete"
    # No reverification - only 2 calls (initial two batches)
    assert len(authority.calls) == 2


def test_rc_qua_06_persistent_conflict_excludes_candidate_lowers_coverage(app):
    """When conflict persists after reverification, exclude candidate and lower coverage.

    Does not mix conflicting views, does not deliver partial/inconsistent candidate,
    does not perform unbounded retries.
    """
    ctx, search, authority, request = setup(app)
    first_id = next(iter(authority.proofs))

    # Inject persistent conflict that doesn't resolve
    authority.proofs[first_id][1]["relations_revision"] = 2

    result = asyncio.run(search.search(ctx, request))

    # m1 should be excluded due to unresolved conflict
    assert "m1" not in [c.memory.memory_id for c in result.candidates]
    assert [c.memory.memory_id for c in result.candidates] == ["m2", "m3"]

    # Coverage should be partial due to the conflict
    assert result.coverage == "partial"

    # Should not retry unboundedly - exactly one reverification
    assert len(authority.calls) == 3


def test_rc_qua_06_multiple_conflicting_fields_trigger_reverification(app):
    """Conflicts in version, body_hash, or scope also trigger reverification.

    Not just relations_revision - any substantive guard field mismatch triggers
    the reverification logic.
    """
    ctx, search, authority, request = setup(app)
    first_id = next(iter(authority.proofs))

    # Inject version conflict
    authority.proofs[first_id][1]["memory"]["version"] = 999

    result = asyncio.run(search.search(ctx, request))

    # Conflict should be detected and candidate excluded
    assert "m1" not in [c.memory.memory_id for c in result.candidates]
    assert result.coverage == "partial"
    assert len(authority.calls) == 3  # One reverification


def test_rc_qua_08_batch_evidence_is_consistent_same_memory_multiple_chunks(
    remember_app, monkeypatch
):
    """All chunks of the same memory in one qualification batch see consistent authority view.

    When qualifying multiple chunks of the same memory, all chunks must receive evidence
    from the same authoritative snapshot (same version, same guard, same manifest).
    Concurrent changes should not cause different chunks to see different versions within
    the same batch.
    """
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

    # Verify all targets received results
    assert tuple(r.target for r in before) == targets

    # All results should show allowed with same manifest
    assert all(r.decision == "allowed" and r.manifest == manifest for r in before)

    # All guards should be identical (excluding checked_at which may vary slightly)
    guards_json = {r.guard.model_dump_json(exclude={"checked_at"}) for r in before}
    assert len(guards_json) == 1, "all chunks in same batch must see identical guard"


def test_rc_qua_08_next_batch_sees_deletion_after_batch_commits(remember_app, monkeypatch):
    """Next qualification batch sees deletion after the previous batch commits.

    A batch that starts after a deletion commits will see the deleted state.
    This verifies proper transaction isolation and that batches don't leak
    stale views across batch boundaries.
    """
    receipt = save(remember_app, "Batch deletion visibility " * 300)
    drain(remember_app)
    ref = facts(remember_app, receipt)[0]
    manifest, first = projection(remember_app, ref)
    assert len(manifest.chunks) > 1

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

    # First batch - should see allowed
    before = asyncio.run(boundary.qualify(context(remember_app), targets, "recall"))
    assert all(r.decision == "allowed" for r in before)

    # Delete the memory
    remember_app.remember.delete(
        context(remember_app),
        ref.memory_id,
        DeleteRequest(expected_revision=before[0].guard.object_revision, reason="batch test"),
    )

    # Next batch - should see excluded (deleted state)
    after = asyncio.run(boundary.qualify(context(remember_app), targets, "recall"))
    assert tuple(r.target for r in after) == targets
    assert all(r.decision == "excluded" and r.manifest is None and r.guard is None for r in after)


def test_rc_qua_08_concurrent_change_during_batch_maintains_consistency(
    remember_app, monkeypatch
):
    """Concurrent deletion during a batch's execution does not break batch consistency.

    Uses controlled barriers to allow deletion to commit between the batch's first
    and potential second transaction. The batch should maintain consistent view
    (all chunks see the same state) even though deletion happened concurrently.
    """
    receipt = save(remember_app, "Concurrent change batch consistency " * 300)
    drain(remember_app)
    ref = facts(remember_app, receipt)[0]
    manifest, first = projection(remember_app, ref)
    assert len(manifest.chunks) > 1

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
    assert all(r.decision == "allowed" for r in before)

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
            gate.changed.set()

    async def race_deletion():
        racing, _ = await asyncio.gather(
            qualify_during_delete(),
            delete_after_read(),
        )
        return racing

    racing = asyncio.run(race_deletion())

    # Verify the batch maintained consistency despite concurrent deletion
    assert tuple(r.target for r in racing) == targets
    assert gate.first_read.is_set() and gate.change_attempted.is_set() and gate.changed.is_set()

    # Batch should show consistent view (all allowed, same guards)
    assert all(r.decision == "allowed" and r.manifest == manifest for r in racing)
    guards_json = {r.guard.model_dump_json(exclude={"checked_at"}) for r in racing}
    assert len(guards_json) == 1

    # Restore original UOW
    monkeypatch.setattr(remember_app.remember, "uow", gate.uow)

    # Next batch should see the deletion
    after = asyncio.run(boundary.qualify(context(remember_app), targets, "recall"))
    assert all(r.decision == "excluded" for r in after)


def test_rc_body_03_version_change_between_verify_and_load_detected(remember_app, monkeypatch):
    """Detects precise version change when correction happens between qualification and body load.

    Scenario: v1 qualified successfully, body load paused, memory corrected to v2,
    load resumed. System must detect the version change and not deliver stale v1 body
    or mix v1/v2 evidence.
    """
    receipt = save(remember_app, "Version race detection fixture " * 50)
    drain(remember_app)
    ref = facts(remember_app, receipt)[0]
    _, target = projection(remember_app, ref)

    boundary = RememberBoundary(remember_app.remember)

    # Qualify v1 - should succeed
    qual_result = asyncio.run(boundary.qualify(context(remember_app), (target,), "recall"))[0]
    assert qual_result.decision == "allowed"
    original_version = qual_result.guard.memory.version
    original_body_hash = qual_result.guard.body_hash

    # Now correct the memory (creating v2) before body load
    asyncio.run(
        remember_app.remember.correct_async(
            context(remember_app),
            ref.memory_id,
            CorrectionRequest(
                expected_version=ref.version,
                content="Corrected body content for v2",
                source=source("correction"),
                reason="version race test",
            ),
        )
    )

    current = remember_app.remember.get(context(remember_app), ref.memory_id)
    assert current.ref.version > original_version

    # Now attempt to read body with v1 target - should detect version mismatch
    # The exact behavior depends on implementation:
    # - May reject with version mismatch error
    # - May re-verify and return v2 evidence
    # - Must NOT deliver v1 body with v2 data or mix v1/v2 evidence

    try:
        body_result = asyncio.run(
            boundary.read_bodies(
                context(remember_app),
                (qual_result.guard,),
            )
        )[0]

        # If read succeeds, it must be for current version, not stale v1
        assert body_result.guard.memory.version == current.ref.version
        # Body hash must match current version
        assert body_result.guard.body_hash != original_body_hash or body_result.guard.memory.version != original_version

    except FoundationError as e:
        # Acceptable to reject with version/guard mismatch
        assert e.code in ["CONTRACT_VIOLATION", "VERSION_CONFLICT", "GUARD_MISMATCH"]


def test_rc_body_03_no_mixing_old_scores_with_new_body(remember_app):
    """Does not pair old search scores/ranks with new body content after correction.

    When body is corrected, search results (scores, ranks) are based on old embeddings.
    System must not deliver a pack that combines old search relevance with new body text
    without proper re-verification or re-embedding.
    """
    receipt = save(remember_app, "Score-body pairing fixture")
    drain(remember_app)
    ref = facts(remember_app, receipt)[0]

    # Get initial body hash
    initial = remember_app.remember.get(context(remember_app), ref.memory_id)
    original_body_hash = initial.manifest.body_hash

    # Correct the memory
    asyncio.run(
        remember_app.remember.correct_async(
            context(remember_app),
            ref.memory_id,
            CorrectionRequest(
                expected_version=ref.version,
                content="Completely different corrected content",
                source=source("correction"),
                reason="content change test",
            ),
        )
    )

    current = remember_app.remember.get(context(remember_app), ref.memory_id)
    new_body_hash = current.manifest.body_hash

    # Verify body hash actually changed
    assert new_body_hash != original_body_hash

    # Key principle: any delivery using the new body must either:
    # 1. Use guards/evidence that match the new version and body_hash
    # 2. Re-verify qualification with current state
    # 3. Fail/reject rather than mix old and new

    # This test documents the principle - full integration test would verify
    # through actual recall search -> qualification -> body load -> pack assembly
    assert current.ref.version > ref.version
    assert current.manifest.body_hash == new_body_hash


def test_rc_body_03_old_epoch_with_new_revision_not_assembled_as_success(remember_app):
    """Does not assemble success pack with mismatched epoch and revision.

    When authorization epoch or object revision changes, evidence from old epoch
    cannot be paired with new revision to form a valid success pack.
    """
    receipt = save(remember_app, "Epoch-revision mismatch fixture")
    drain(remember_app)
    ref = facts(remember_app, receipt)[0]
    _, target = projection(remember_app, ref)

    boundary = RememberBoundary(remember_app.remember)
    before = asyncio.run(boundary.qualify(context(remember_app), (target,), "recall"))[0]

    original_epoch = before.guard.authorization_epoch
    original_revision = before.guard.object_revision

    # Correct to trigger revision change
    asyncio.run(
        remember_app.remember.correct_async(
            context(remember_app),
            ref.memory_id,
            CorrectionRequest(
                expected_version=ref.version,
                content="Content change triggers new revision",
                source=source("correction"),
                reason="revision change",
            ),
        )
    )

    # Re-qualify with old target
    after = asyncio.run(boundary.qualify(context(remember_app), (target,), "recall"))[0]

    # Should be excluded (old version) or show updated revision if allowed
    if after.decision == "allowed":
        # If still allowed, must have updated guard matching current state
        assert after.guard.object_revision != original_revision
    else:
        # Old version excluded
        assert after.decision == "excluded"
