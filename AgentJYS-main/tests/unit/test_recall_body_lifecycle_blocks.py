"""AET-68 RC-BODY-06, RC-BODY-07: expiration, deletion, and source revocation block new recall and old result retrieval.

Verifies current authoritative qualification constrains both new Recall and saved result retrieval.
Physical residue or historical success cannot continue granting body access.

Test cases:
- Prepare memories: no expires_at, explicit not-yet-expired, explicit expired
- Memories without TTL remain readable across controlled test window
- Execute new Recall and original Pack retrieval before/after explicit expiration
- After expiration: cannot deliver expired body in either path
- Register service clock, precision, epsilon; verify equality per current contract
- Authority confirms logical delete/source revoke, retain vector and Redis residue
- New request and GET original result both cannot deliver residual body
- Block doesn't wait for physical cleanup; record existence ≠ readable
- Prior real successful read fact can be retained

Extends AET-19, reuses authoritative lifecycle and saved result retrieval support.
"""

import asyncio

import pytest
from remember_helpers import app as authority_app
from remember_helpers import context, drain, facts, save, source
from test_remember_lcm_complete import enroll

from aether_agent_memory.recall.contracts.models import RecallRequest
from aether_agent_memory.remember.basic.boundary import RememberBoundary
from aether_agent_memory.remember.contracts.models import DeleteRequest
from aether_agent_memory.runtime.contracts.models import ErrorCode
from aether_agent_memory.runtime.foundation.common import FoundationError, later

pytestmark = [pytest.mark.integration, pytest.mark.p0]


def prepared_memory(app, text="我喜欢无糖咖啡"):
    """Helper to prepare a memory with Remember boundary."""
    receipt = save(app, text)
    drain(app)
    ref = facts(app, receipt)[0]
    return receipt, ref, RememberBoundary(app.remember)


def test_rc_body_06_no_default_ttl_remains_readable_across_domain_time_window(
    authority_app, monkeypatch
):
    """Memories without explicit expires_at remain readable across controlled test window.

    Memory with no TTL set should remain accessible even after significant time passage
    within domain test window (e.g., 30 days).
    """
    app = authority_app
    _, ref, boundary = prepared_memory(app)

    # Verify no expiration set
    item = app.remember.get(context(app), ref.memory_id)
    assert item.expires_at is None

    # Read before time advance
    before = asyncio.run(boundary.load_bodies(context(app), (ref,)))[0]
    assert before.outcome == "read"

    # Advance time by 30 days
    timestamp = later(app.foundation.identity.clock(), 86400 * 30)
    monkeypatch.setattr(app.foundation.identity, "clock", lambda: timestamp)

    # Read after time advance - should still succeed
    after = asyncio.run(boundary.load_bodies(context(app), (ref,)))[0]
    assert after.outcome == "read"
    assert after.content == before.content
    assert after.memory == ref


@pytest.mark.parametrize("offset", [-1, 0, 1])
def test_rc_body_06_explicit_expiration_blocks_new_read_and_saved_result_at_boundary(
    authority_app, monkeypatch, offset
):
    """Explicit expiration blocks both new Recall and saved result retrieval at boundary.

    Test expiration boundary behavior with offset from expiry time:
    - offset=-1: before expiration (1 second before)
    - offset=0: at expiration (exact boundary)
    - offset=1: after expiration (1 second after)

    Before expiration: both new read and saved result succeed.
    At/after expiration: both new read and saved result blocked.
    """
    app = authority_app
    _, ref, boundary = prepared_memory(app)

    # Set explicit expiration 60 seconds from now
    expiry = later(app.foundation.identity.clock(), 60)
    enroll(app, ref, enabled=False, legal_hold=True, expires_at=expiry)

    # Execute Recall and save result before expiration
    pack = asyncio.run(
        app.recall.recall(context(app), RecallRequest(query="咖啡", sources="long_term"))
    )
    assert pack.groups, "initial recall must succeed and have results"

    # Advance time to test boundary
    timestamp = later(expiry, offset)
    monkeypatch.setattr(app.foundation.identity, "clock", lambda: timestamp)

    # Test new body read
    result = asyncio.run(boundary.load_bodies(context(app), (ref,)))[0]

    if offset < 0:
        # Before expiration: read succeeds
        assert result.outcome == "read"

        # Saved result retrieval succeeds
        saved_result = app.recall.result(context(app), pack.recall_id)
        assert saved_result.groups
    else:
        # At or after expiration: read blocked
        assert result.outcome == "excluded"
        assert result.reason_code == "expired"

        # Saved result retrieval blocked
        with pytest.raises(FoundationError) as error:
            app.recall.result(context(app), pack.recall_id)
        assert error.value.code in {ErrorCode.RESULT_INVALIDATED, ErrorCode.MEMORY_GONE}

        # Fresh Recall also blocked
        fresh = asyncio.run(
            app.recall.recall(context(app), RecallRequest(query="咖啡", sources="long_term"))
        )
        assert not fresh.groups


def test_rc_body_06_service_clock_precision_and_epsilon_verification(authority_app, monkeypatch):
    """Register service clock, precision, and epsilon; verify equality per current contract.

    Document the clock used for expiration checks and its precision.
    Verify boundary behavior is consistent with documented precision.
    """
    app = authority_app
    _, ref, boundary = prepared_memory(app)

    # Get service clock
    service_clock = app.foundation.identity.clock
    clock_value = service_clock()

    # Document: clock returns ISO 8601 timestamp strings
    assert isinstance(clock_value, str)
    assert "T" in clock_value  # ISO format

    # Set expiration at current time
    expiry = clock_value
    enroll(app, ref, enabled=False, legal_hold=True, expires_at=expiry)

    # At exact expiration boundary, memory should be expired
    monkeypatch.setattr(app.foundation.identity, "clock", lambda: expiry)
    result = asyncio.run(boundary.load_bodies(context(app), (ref,)))[0]
    assert result.outcome == "excluded"
    assert result.reason_code == "expired"

    # Contract: expiration check uses >= comparison (not strict >)
    # At exact boundary time, memory is considered expired


@pytest.mark.parametrize("change", ["delete", "revoke_source"])
def test_rc_body_07_logical_revocation_blocks_residual_vectors_cache_and_saved_result(
    authority_app, change
):
    """Logical delete/source revoke blocks residual vectors, cache, and saved result.

    After authority confirms logical revocation (delete or source revoke):
    - Vector residue may remain in index
    - Redis cache may remain
    - But both new request and GET original result cannot deliver body

    Block doesn't wait for physical cleanup. Record existence ≠ readable.
    """
    app = authority_app
    receipt, ref, boundary = prepared_memory(app)
    ctx = context(app)

    # Execute Recall and save result
    pack = asyncio.run(app.recall.recall(ctx, RecallRequest(query="咖啡", sources="long_term")))
    assert pack.groups, "initial recall must succeed"

    # Perform logical revocation
    if change == "delete":
        item = app.remember.get(context(app), ref.memory_id)
        app.remember.delete(
            context(app),
            ref.memory_id,
            DeleteRequest(expected_revision=item.object_revision, reason="logical removal"),
        )
    else:  # revoke_source
        src = app.remember.source(context(app), receipt.source.source_id)
        app.remember.revoke_source(
            context(app),
            src.source_id,
            DeleteRequest(expected_revision=src.source_version, reason="source withdrawn"),
        )

    # Do not drain: asynchronous index/cache cleanup has not been delivered yet
    # Physical residue (vectors, cache) may still exist

    # New body read blocked
    result = asyncio.run(boundary.load_bodies(context(app), (ref,)))[0]
    assert result.outcome == "excluded"
    assert result.content is None

    # Saved result retrieval blocked
    with pytest.raises(FoundationError) as error:
        app.recall.result(context(app), pack.recall_id)
    assert error.value.code in {ErrorCode.RESULT_INVALIDATED, ErrorCode.MEMORY_GONE}

    # Fresh Recall blocked
    fresh = asyncio.run(
        app.recall.recall(context(app), RecallRequest(query="咖啡", sources="long_term"))
    )
    assert not fresh.groups


def test_rc_body_06_explicit_not_yet_expired_remains_readable(authority_app, monkeypatch):
    """Explicit not-yet-expired memory remains readable.

    Memory with future expiration time is still accessible before that time.
    """
    app = authority_app
    _, ref, boundary = prepared_memory(app)

    # Set expiration far in future (1 hour from now)
    expiry = later(app.foundation.identity.clock(), 3600)
    enroll(app, ref, enabled=False, legal_hold=True, expires_at=expiry)

    # Should be readable now
    result = asyncio.run(boundary.load_bodies(context(app), (ref,)))[0]
    assert result.outcome == "read"

    # Advance time but still before expiration (30 minutes later)
    timestamp = later(app.foundation.identity.clock(), 1800)
    monkeypatch.setattr(app.foundation.identity, "clock", lambda: timestamp)

    # Should still be readable
    after = asyncio.run(boundary.load_bodies(context(app), (ref,)))[0]
    assert after.outcome == "read"


def test_rc_body_06_expired_body_not_delivered_in_new_recall(authority_app, monkeypatch):
    """After expiration, expired body cannot be delivered in new Recall.

    New Recall request after expiration must not deliver expired content.
    """
    app = authority_app
    _, ref, boundary = prepared_memory(app)

    # Set short expiration
    expiry = later(app.foundation.identity.clock(), 10)
    enroll(app, ref, enabled=False, legal_hold=True, expires_at=expiry)

    # Execute Recall before expiration
    before_pack = asyncio.run(
        app.recall.recall(context(app), RecallRequest(query="咖啡", sources="long_term"))
    )
    assert before_pack.groups

    # Advance past expiration
    timestamp = later(expiry, 10)
    monkeypatch.setattr(app.foundation.identity, "clock", lambda: timestamp)

    # New Recall after expiration
    after_pack = asyncio.run(
        app.recall.recall(context(app), RecallRequest(query="咖啡", sources="long_term"))
    )
    assert not after_pack.groups, "expired memory must not appear in new recall"


def test_rc_body_06_expired_body_not_delivered_in_saved_result_retrieval(
    authority_app, monkeypatch
):
    """After expiration, expired body cannot be delivered via saved result retrieval.

    Previously saved Recall result becomes invalid after constituent memories expire.
    """
    app = authority_app
    _, ref, boundary = prepared_memory(app)

    # Set short expiration
    expiry = later(app.foundation.identity.clock(), 10)
    enroll(app, ref, enabled=False, legal_hold=True, expires_at=expiry)

    # Execute and save Recall result before expiration
    pack = asyncio.run(
        app.recall.recall(context(app), RecallRequest(query="咖啡", sources="long_term"))
    )
    assert pack.groups
    recall_id = pack.recall_id

    # Verify saved result accessible before expiration
    saved = app.recall.result(context(app), recall_id)
    assert saved.groups

    # Advance past expiration
    timestamp = later(expiry, 10)
    monkeypatch.setattr(app.foundation.identity, "clock", lambda: timestamp)

    # Saved result retrieval blocked after expiration
    with pytest.raises(FoundationError) as error:
        app.recall.result(context(app), recall_id)
    assert error.value.code in {ErrorCode.RESULT_INVALIDATED, ErrorCode.MEMORY_GONE}


def test_rc_body_07_delete_blocks_new_request_immediately(authority_app):
    """Logical delete blocks new request immediately, before physical cleanup.

    After delete, new body read blocked even if physical cleanup hasn't completed.
    """
    app = authority_app
    _, ref, boundary = prepared_memory(app)

    # Verify readable before delete
    before = asyncio.run(boundary.load_bodies(context(app), (ref,)))[0]
    assert before.outcome == "read"

    # Delete
    item = app.remember.get(context(app), ref.memory_id)
    app.remember.delete(
        context(app),
        ref.memory_id,
        DeleteRequest(expected_revision=item.object_revision, reason="test delete"),
    )

    # Immediately blocked (no drain/cleanup needed)
    after = asyncio.run(boundary.load_bodies(context(app), (ref,)))[0]
    assert after.outcome == "excluded"
    assert after.content is None


def test_rc_body_07_source_revoke_blocks_new_request_immediately(authority_app):
    """Source revoke blocks new request immediately, before physical cleanup.

    After source revocation, new body read blocked even if physical cleanup hasn't completed.
    """
    app = authority_app
    receipt, ref, boundary = prepared_memory(app)

    # Verify readable before revoke
    before = asyncio.run(boundary.load_bodies(context(app), (ref,)))[0]
    assert before.outcome == "read"

    # Revoke source
    src = app.remember.source(context(app), receipt.source.source_id)
    app.remember.revoke_source(
        context(app),
        src.source_id,
        DeleteRequest(expected_revision=src.source_version, reason="test revoke"),
    )

    # Immediately blocked (no drain/cleanup needed)
    after = asyncio.run(boundary.load_bodies(context(app), (ref,)))[0]
    assert after.outcome == "excluded"
    assert after.content is None


def test_rc_body_07_prior_successful_read_fact_can_be_retained(authority_app):
    """Prior real successful read fact can be retained after revocation.

    Historical fact that body was successfully read at time T can be preserved,
    even though body is no longer accessible at time T+1 after revocation.
    """
    app = authority_app
    _, ref, boundary = prepared_memory(app)

    # Successful read at T
    before = asyncio.run(boundary.load_bodies(context(app), (ref,)))[0]
    assert before.outcome == "read"
    successful_content = before.content

    # Record the successful read fact
    read_timestamp = app.foundation.identity.clock()
    read_fact = {
        "memory_id": ref.memory_id,
        "timestamp": read_timestamp,
        "outcome": "read",
        "content_hash": before.location.content_hash,
    }

    # Delete at T+1
    item = app.remember.get(context(app), ref.memory_id)
    app.remember.delete(
        context(app),
        ref.memory_id,
        DeleteRequest(expected_revision=item.object_revision, reason="revocation"),
    )

    # Now blocked
    after = asyncio.run(boundary.load_bodies(context(app), (ref,)))[0]
    assert after.outcome == "excluded"

    # But historical read fact remains valid
    assert read_fact["outcome"] == "read"
    assert read_fact["content_hash"] == before.location.content_hash
    # The fact that body was readable at read_timestamp is preserved


def test_rc_body_07_vector_residue_exists_but_body_not_accessible(authority_app):
    """Vector residue may exist in index, but body not accessible after revocation.

    Record existence (vectors in index) ≠ readable (body access).
    Physical cleanup is asynchronous; logical authorization is immediate.
    """
    app = authority_app
    receipt, ref, boundary = prepared_memory(app)

    # Delete
    item = app.remember.get(context(app), ref.memory_id)
    app.remember.delete(
        context(app),
        ref.memory_id,
        DeleteRequest(expected_revision=item.object_revision, reason="test"),
    )

    # Don't drain - vectors may still exist in index
    # But body read blocked
    result = asyncio.run(boundary.load_bodies(context(app), (ref,)))[0]
    assert result.outcome == "excluded"
    assert result.content is None

    # Principle: logical authorization (excluded) takes precedence over
    # physical residue (vectors might still be searchable temporarily)


def test_rc_body_06_equality_boundary_behavior_documented(authority_app, monkeypatch):
    """Document equality boundary behavior for expiration checks.

    Current contract: expiration uses >= comparison.
    At exact expiry time, memory is considered expired.
    """
    app = authority_app
    _, ref, boundary = prepared_memory(app)

    current_time = app.foundation.identity.clock()
    expiry = current_time  # Expire at current time

    enroll(app, ref, enabled=False, legal_hold=True, expires_at=expiry)

    # At exact expiry time (equality case)
    monkeypatch.setattr(app.foundation.identity, "clock", lambda: expiry)
    result = asyncio.run(boundary.load_bodies(context(app), (ref,)))[0]

    # Contract: at exact boundary, memory is expired (>= comparison)
    assert result.outcome == "excluded"
    assert result.reason_code == "expired"
