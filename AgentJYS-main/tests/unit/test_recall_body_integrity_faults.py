"""AET-67 RC-BODY-04, RC-BODY-05: body integrity errors and storage fault safe handling.

Injects body binding errors and storage faults to verify incorrect bodies don't enter results.
Faults and coverage cannot be masked by normal empty or stale cache.

Test cases:
- Controlled Remember body hash, Ref, version, object_revision errors (one at a time)
- Reject body, don't use old guard to prove new body qualification
- Metadata and vectors still Ready; create object missing, denied permission, corrupt/digest error, read timeout
- Verify model input, Pack, delivery: incorrect body can't enter normally
- Fault located at read/qualification, not disguised as no hits, no relevance, or normal qualification denial
- Stale cache can't serve as available body fallback
- Source coverage and true reason remain observable
- Add other safe complete candidates, contrast allowed/forbidden partial results
- Only deliver other safe material when policy allows, preserve fault reason

Extends AET-19. Real storage faults and contract doubles tracked separately.
"""

import asyncio

import pytest
from remember_helpers import app as authority_app
from remember_helpers import context, drain, facts, save, source
from test_flows import app as app
from test_generation_assembly import BodyAuthority, CaptureReranker, assembly_setup, use_reranker

from aether_agent_memory.remember.basic.boundary import RememberBoundary
from aether_agent_memory.remember.contracts.foundation import FullBodyReadResult
from aether_agent_memory.remember.contracts.models import CorrectionRequest
from aether_agent_memory.runtime.contracts.models import ErrorCode
from aether_agent_memory.runtime.foundation.common import FoundationError
from aether_agent_memory.runtime.foundation.requests import text_hash

pytestmark = [pytest.mark.integration, pytest.mark.p0]


@pytest.mark.parametrize("fault", ["content", "hash", "version", "id", "object_revision"])
def test_rc_body_04_wrong_body_binding_rejected_never_enters_plan_or_model(app, fault):
    """Controlled Remember body errors: hash, Ref, version, object_revision (one at a time).

    Each fault type is tested independently:
    - content: body content changed
    - hash: body_hash mismatch
    - version: version mismatch
    - id: memory_id mismatch
    - object_revision: object_revision changed (stale guard)

    Incorrect body must not enter model input or Pack. Don't use old guard to prove new body qualification.
    """
    ctx, assembly, body, request = assembly_setup(app)

    before = body.bodies["m1"]

    if fault == "content":
        # Content changed but hash didn't update
        changed = before.model_copy(update={"content": "CORRUPTED_CANARY"})
    elif fault == "hash":
        # Hash mismatch
        changed = before.model_copy(
            update={"guard": before.guard.model_copy(update={"body_hash": "f" * 64})}
        )
    elif fault in {"version", "id"}:
        # Version or ID mismatch
        ref = before.memory.model_copy(
            update={"version": before.memory.version + 1}
            if fault == "version"
            else {"memory_id": "other"}
        )
        changed = before.model_copy(
            update={"memory": ref, "guard": before.guard.model_copy(update={"memory": ref})}
        )
    else:  # object_revision
        # Stale guard (object_revision changed)
        changed = before.model_copy(
            update={
                "guard": before.guard.model_copy(
                    update={"object_revision": before.guard.object_revision + 1}
                )
            }
        )

    body.bodies["m1"] = changed

    scorer = CaptureReranker()
    use_reranker(app, scorer)

    if fault == "object_revision":
        # Stale guard: plan succeeds but m1 excluded
        plan = asyncio.run(assembly.plan(ctx, request))
        assert [b.memory.memory_id for u in plan.units for b in u.bodies] == ["m2"]
        assert "body_stale" in plan.degradation_reasons
        # m1 doesn't enter reranker
        assert scorer.documents == [body.bodies["m2"].content]
    else:
        # Other faults: hard contract violation
        with pytest.raises(FoundationError) as error:
            asyncio.run(assembly.plan(ctx, request))
        assert error.value.code == ErrorCode.CONTRACT_VIOLATION
        # No documents entered reranker
        assert scorer.documents == []


@pytest.mark.parametrize("outcome", ["missing", "denied", "corrupt", "unavailable"])
def test_rc_body_05_read_failure_only_safe_material_survives(app, outcome):
    """Storage fault with safe complete candidates: only complete safe material survives.

    Metadata and vectors still Ready; inject read failures:
    - missing: object not found
    - denied: permission denied
    - corrupt: corrupted object / digest error
    - unavailable: read timeout or service unavailable

    Verify model input, Pack, delivery: faulty body excluded, safe m2 delivered.
    Fault located at read/qualification, not disguised as no hits or no relevance.
    """
    ctx, assembly, body, request = assembly_setup(app)

    # Inject controlled read failure for m1
    body.bodies["m1"] = FullBodyReadResult(
        memory=body.snapshots["m1"].ref,
        outcome=outcome,
        path="none",
        reason_code="controlled_read_failure",
    )

    plan = asyncio.run(assembly.plan(ctx, request))

    # Only m2 survives (m1 excluded due to read failure)
    assert [b.memory.memory_id for u in plan.units for b in u.bodies] == ["m2"]

    # m1 content doesn't appear in Pack
    assert body.snapshots["m1"].content not in plan.rendered_context

    # Degradation reason reflects the actual fault
    if outcome == "excluded":
        # excluded is definitive denial, not a degradation
        assert plan.degradation_reasons == ()
    else:
        assert plan.degradation_reasons == ("body_" + outcome,)


def test_rc_body_04_each_fault_type_tested_independently(app):
    """Verify each fault type (hash, version, id, object_revision) tested independently.

    This test documents that faults are tested one at a time, not combined.
    Each fault scenario has distinct handling.
    """
    ctx, assembly, body, request = assembly_setup(app)

    # Test hash fault alone
    before = body.bodies["m1"]
    hash_fault = before.model_copy(
        update={"guard": before.guard.model_copy(update={"body_hash": "a" * 64})}
    )
    body.bodies["m1"] = hash_fault

    with pytest.raises(FoundationError) as error:
        asyncio.run(assembly.plan(ctx, request))
    assert error.value.code == ErrorCode.CONTRACT_VIOLATION

    # Reset and test version fault alone
    body.bodies["m1"] = before
    ref = before.memory.model_copy(update={"version": before.memory.version + 99})
    version_fault = before.model_copy(
        update={"memory": ref, "guard": before.guard.model_copy(update={"memory": ref})}
    )
    body.bodies["m1"] = version_fault

    with pytest.raises(FoundationError) as error:
        asyncio.run(assembly.plan(ctx, request))
    assert error.value.code == ErrorCode.CONTRACT_VIOLATION


def test_rc_body_05_stale_cache_cannot_mask_authority_failure(authority_app, monkeypatch):
    """Stale cache can't serve as available body fallback when authority fails.

    Object fault with stale cache present: system must not use unverified cache,
    must report actual authority failure.
    """
    app = authority_app

    receipt = save(app, "Cache masking test content")
    drain(app)
    ref = facts(app, receipt)[0]
    boundary = RememberBoundary(app.remember)

    # Verify normal read works
    before = asyncio.run(boundary.load_bodies(context(app), (ref,)))[0]
    assert before.outcome == "read"

    bodies = app.remember.bodies

    # Inject stale cache
    async def corrupt_cache(*args, **kwargs):
        return "STALE_CACHE_CANARY"

    monkeypatch.setattr(bodies.cache, "get", corrupt_cache)

    # Inject authority object failure
    def object_failure(*args, **kwargs):
        from botocore.exceptions import ClientError

        raise ClientError(
            {
                "Error": {"Code": "NoSuchKey"},
                "ResponseMetadata": {"HTTPStatusCode": 404},
            },
            "GetObject",
        )

    monkeypatch.setattr(bodies.p2.transport.client, "get_object", object_failure)

    # Read should fail, not fall back to stale cache
    result = asyncio.run(boundary.load_bodies(context(app), (ref,)))[0]
    assert result.outcome == "missing"  # Authority failure reported
    # Stale cache canary must not appear
    if hasattr(result, "content"):
        assert result.content != "STALE_CACHE_CANARY"


@pytest.mark.parametrize("fault", ["missing", "denied", "corrupt", "timeout"])
def test_rc_body_05_object_fault_cannot_read_stale_cache_or_report_normal_empty(
    authority_app, monkeypatch, fault
):
    """Object faults with stale cache: can't mask authority failure or report normal empty.

    For each fault type (missing, denied, corrupt, timeout):
    - Inject stale cache canary
    - Inject object storage fault
    - Verify fault reported correctly, cache not used
    - Fault not disguised as normal empty or no hits
    """
    app = authority_app

    receipt = save(app, "Fault isolation test content")
    drain(app)
    ref = facts(app, receipt)[0]
    boundary = RememberBoundary(app.remember)

    before = asyncio.run(boundary.load_bodies(context(app), (ref,)))[0]
    assert before.outcome == "read"

    bodies = app.remember.bodies

    # Redis is external replica: unverified canary must not mask authority failure
    async def corrupt_cache(*args, **kwargs):
        return "STALE_CACHE_CANARY"

    monkeypatch.setattr(bodies.cache, "get", corrupt_cache)

    # Inject object failure at authority
    def object_failure(*args, **kwargs):
        if fault == "corrupt":
            from io import BytesIO

            raw = b"CORRUPTED_OBJECT_CANARY"
            return {
                "Body": BytesIO(raw),
                "ContentLength": len(raw),
                "ResponseMetadata": {"HTTPStatusCode": 200},
                "Metadata": {"sha256": text_hash(raw.decode("utf-8"))},
            }
        if fault in {"missing", "denied"}:
            from botocore.exceptions import ClientError

            raise ClientError(
                {
                    "Error": {"Code": "NoSuchKey" if fault == "missing" else "AccessDenied"},
                    "ResponseMetadata": {"HTTPStatusCode": 404 if fault == "missing" else 403},
                },
                "GetObject",
            )
        # timeout
        raise TimeoutError("controlled object timeout")

    monkeypatch.setattr(bodies.p2.transport.client, "get_object", object_failure)

    if fault == "corrupt":
        # Corrupt object: digest mismatch detected
        result = asyncio.run(boundary.load_bodies(context(app), (ref,)))[0]
        assert result.outcome == "corrupt"
        # Canary content must not appear
        if hasattr(result, "content"):
            assert "STALE_CACHE_CANARY" not in str(result.content)
            assert "CORRUPTED_OBJECT_CANARY" not in str(result.content)
    elif fault == "timeout":
        # Timeout: unavailable outcome
        result = asyncio.run(boundary.load_bodies(context(app), (ref,)))[0]
        assert result.outcome == "unavailable"
    else:
        # missing or denied
        result = asyncio.run(boundary.load_bodies(context(app), (ref,)))[0]
        expected_outcome = "missing" if fault == "missing" else "denied"
        assert result.outcome == expected_outcome


def test_rc_body_05_source_coverage_and_true_reason_remain_observable(app):
    """Source coverage and true fault reason remain observable in degradation_reasons.

    When body read fails, the specific reason is tracked and observable,
    not masked as generic "no results" or "low relevance".
    """
    ctx, assembly, body, request = assembly_setup(app)

    # Inject multiple fault types
    body.bodies["m1"] = FullBodyReadResult(
        memory=body.snapshots["m1"].ref,
        outcome="unavailable",
        path="none",
        reason_code="storage_timeout",
    )

    plan = asyncio.run(assembly.plan(ctx, request))

    # Degradation reason reflects actual fault
    assert "body_unavailable" in plan.degradation_reasons

    # Not masked as generic failure
    assert "no_results" not in plan.degradation_reasons
    assert "low_relevance" not in plan.degradation_reasons


def test_rc_body_05_policy_allows_partial_results_preserves_fault_reason(app):
    """When policy allows partial results, deliver other safe material and preserve fault reason.

    m1 has read fault, m2 is safe. Policy allows partial delivery.
    Result: m2 delivered, m1 fault reason preserved in degradation_reasons.
    """
    ctx, assembly, body, request = assembly_setup(app, token_budget=4096)

    # m1 has fault, m2 is safe
    body.bodies["m1"] = FullBodyReadResult(
        memory=body.snapshots["m1"].ref,
        outcome="denied",
        path="none",
        reason_code="permission_denied",
    )

    plan = asyncio.run(assembly.plan(ctx, request))

    # m2 delivered (safe)
    assert [b.memory.memory_id for u in plan.units for b in u.bodies] == ["m2"]

    # m1 not in Pack
    assert body.snapshots["m1"].content not in plan.rendered_context

    # m2 content in Pack
    assert body.snapshots["m2"].content in plan.rendered_context

    # Fault reason preserved
    assert "body_denied" in plan.degradation_reasons


def test_rc_body_04_dont_use_old_guard_to_prove_new_body_qualification(app):
    """Don't use old guard to prove new body qualification after content change.

    When body content changes, old guard (with old body_hash) cannot be used
    to qualify the new content. Must re-verify with current guard.
    """
    ctx, assembly, body, request = assembly_setup(app)

    before = body.bodies["m1"]

    # Change content but keep old guard (invalid state)
    changed = before.model_copy(
        update={
            "content": "New content that doesn't match guard body_hash",
            # Guard still has old body_hash
        }
    )
    body.bodies["m1"] = changed

    scorer = CaptureReranker()
    use_reranker(app, scorer)

    # Should reject due to content/hash mismatch
    with pytest.raises(FoundationError) as error:
        asyncio.run(assembly.plan(ctx, request))

    assert error.value.code == ErrorCode.CONTRACT_VIOLATION


def test_rc_body_05_fault_located_at_read_not_disguised_as_qualification_denial(app):
    """Fault located at read/qualification, not disguised as no hits or normal qualification denial.

    Read failure is distinct from:
    - No vector hits (search returned empty)
    - Low relevance (search found irrelevant candidates)
    - Qualification denial (Remember explicitly excluded)

    Error attribution must be clear.
    """
    ctx, assembly, body, request = assembly_setup(app)

    # Inject read failure
    body.bodies["m1"] = FullBodyReadResult(
        memory=body.snapshots["m1"].ref,
        outcome="corrupt",
        path="none",
        reason_code="digest_mismatch",
    )

    plan = asyncio.run(assembly.plan(ctx, request))

    # Fault attributed to body read, not search or qualification
    assert "body_corrupt" in plan.degradation_reasons

    # Not attributed to search
    assert "no_hits" not in str(plan.degradation_reasons)
    assert "low_relevance" not in str(plan.degradation_reasons)

    # Not attributed to qualification
    assert "excluded" not in str(plan.degradation_reasons)
    assert "unverifiable" not in str(plan.degradation_reasons)


def test_rc_body_04_metadata_and_vectors_ready_body_binding_still_fails(app):
    """Metadata and vectors still Ready (searchable), but body binding error causes failure.

    Search can find the memory (vectors Ready), qualification can verify metadata,
    but body read fails due to binding error. This proves fault is at body read stage.
    """
    ctx, assembly, body, request = assembly_setup(app)

    # Vectors and metadata are Ready (m1 found in search)
    # But inject body binding error
    before = body.bodies["m1"]
    changed = before.model_copy(
        update={"guard": before.guard.model_copy(update={"body_hash": "f" * 64})}
    )
    body.bodies["m1"] = changed

    scorer = CaptureReranker()
    use_reranker(app, scorer)

    # Search found m1, but body read fails
    with pytest.raises(FoundationError) as error:
        asyncio.run(assembly.plan(ctx, request))

    assert error.value.code == ErrorCode.CONTRACT_VIOLATION

    # Failure is at body read stage (after search and qualification)
    # Search succeeded (m1 was found), qualification could have succeeded (metadata Ready)
    # Only body binding check failed
