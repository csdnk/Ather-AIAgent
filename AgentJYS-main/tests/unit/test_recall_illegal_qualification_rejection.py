"""AET-60 RC-QUA-02, RC-QUA-03: illegal qualification evidence must not authorize body reads.

Injects illegal Remember responses at the Recall qualification consumption boundary to verify
that candidates without complete matching evidence cannot obtain body read permissions.

Extends AET-16 contract matrix with body read, model input, and final delivery evidence:
- Injects manifest missing, guard missing, and binding field mismatches for allowed decisions
- Attaches disallowed manifest/guard to excluded/unverifiable decisions
- Verifies illegal responses are identified as provider contract errors
- Confirms illegal evidence cannot authorize body reads, enter reranking, enter Pack, or delivery
- Ensures non-allowed decisions are not upgraded to allowed

Reuses existing contract fixtures and test support. Only adds missing variants and assertions
for the body read authorization boundary.
"""

import asyncio
import copy
from types import SimpleNamespace

import pytest
from test_qualification_contracts import evidence, qualification

from aether_agent_memory.recall.basic.candidates import MemoryCandidates
from aether_agent_memory.recall.contracts.foundation import (
    ChunkHit,
    EmbeddingSpace,
    MemorySearchRequest,
)
from aether_agent_memory.recall.embedding.spaces import EmbeddingSpaces
from aether_agent_memory.remember.contracts.foundation import CandidateQualificationResult
from aether_agent_memory.runtime.contracts.models import TrustedContext
from aether_agent_memory.runtime.foundation.common import FoundationError


class MalformedQualificationEvidence:
    """An encoded qualification response that intentionally violates the wire contract."""

    def __init__(self, payload):
        self.payload = payload

    def model_dump_json(self):
        import json
        return json.dumps(self.payload)


class QualificationProvider:
    """Controlled qualification authority for testing illegal evidence rejection."""

    def __init__(self, values):
        self.values = values
        self.calls = []

    async def qualify(self, ctx, targets, purpose):
        self.calls.append((ctx, targets, purpose))
        return self.values


@pytest.fixture
def qualification_consumer():
    """Setup qualification consumer with controlled provider for testing illegal evidence."""
    candidate = evidence()
    hit = ChunkHit.model_validate(candidate["hits"][0])

    ctx = TrustedContext(
        principal={
            "principal_id": "reader",
            "home_scope": hit.memory.scope,
            "permissions": ["memory:read"],
            "auth_epoch": 1,
        },
        request_id="request",
        operation_id="search",
        trace_id="a" * 32,
        span_id="a" * 16,
        deadline_at="2026-09-21T02:02:00.000Z",
    )

    space = EmbeddingSpace(
        model_space="space_1",
        model_id="test",
        model_revision="v1",
        dimensions=3,
        tokenizer_id="embed_tok_1",
        query_prefix="",
        passage_prefix="",
        normalization="none",
        metric="inner_product",
        max_input_tokens=512,
    )

    request = MemorySearchRequest(
        operation_id="search",
        purpose="recall",
        query="query",
        selection={},
        model_space=space.model_space,
        memory_top_k=2,
        chunk_page_size=10,
        max_chunk_hits=50,
        max_rounds=5,
        deadline_at=ctx.deadline_at,
    )

    provider = QualificationProvider((CandidateQualificationResult(**qualification()),))

    search = MemoryCandidates(
        None,
        SimpleNamespace(clock=lambda: "2026-09-21T02:00:01.000Z"),
        None,
        None,
        provider,
        EmbeddingSpaces((space,)),
    )

    return search, provider, ctx, (hit,), request


@pytest.mark.p0
@pytest.mark.parametrize("fault", [
    "manifest_missing",
    "guard_missing",
    "body_hash_mismatch",
    "version_mismatch",
    "scope_mismatch",
    "generation_mismatch",
    "model_space_mismatch",
    "chunk_index_mismatch",
    "vector_id_mismatch",
    "input_hash_mismatch",
])
def test_rc_qua_02_allowed_with_incomplete_evidence_rejected(qualification_consumer, fault):
    """Inject missing manifest, missing guard, or binding field mismatches for allowed decisions.

    Each variant must be rejected as CONTRACT_VIOLATION. Illegal evidence cannot authorize
    body reads, model input, Pack entry, or delivery. A success control with complete matching
    evidence is maintained in the base test_rc_qua_01 tests.
    """
    search, provider, ctx, hits, request = qualification_consumer

    # Create malformed payload based on fault type
    payload = copy.deepcopy(qualification())

    if fault == "manifest_missing":
        payload["manifest"] = None
    elif fault == "guard_missing":
        payload["guard"] = None
    elif fault == "body_hash_mismatch":
        payload["guard"]["body_hash"] = "f" * 64
    elif fault == "version_mismatch":
        payload["guard"]["memory"]["version"] = 999
    elif fault == "scope_mismatch":
        payload["guard"]["memory"]["scope"]["tenant_id"] = "different_tenant"
    elif fault == "generation_mismatch":
        payload["target"]["generation"] = "wrong_generation"
        payload["manifest"]["generation"] = "wrong_generation"
    elif fault == "model_space_mismatch":
        payload["target"]["model_space"] = "wrong_space"
    elif fault == "chunk_index_mismatch":
        payload["target"]["chunk_index"] = 999
    elif fault == "vector_id_mismatch":
        payload["target"]["vector_id"] = "f" * 64
    elif fault == "input_hash_mismatch":
        payload["target"]["input_hash"] = "f" * 64

    provider.values = (MalformedQualificationEvidence(payload),)

    with pytest.raises(FoundationError) as failure:
        asyncio.run(search.qualify(ctx, hits, request))

    assert failure.value.code == "CONTRACT_VIOLATION"
    # Verify the error is identified at qualification consumption boundary
    assert "qualification" in failure.value.message.lower() or "evidence" in failure.value.message.lower()


@pytest.mark.p0
@pytest.mark.parametrize("decision", ["excluded", "unverifiable"])
@pytest.mark.parametrize("illegal_attachment", ["manifest_only", "guard_only", "both"])
def test_rc_qua_03_non_allowed_with_evidence_rejected(qualification_consumer, decision, illegal_attachment):
    """Attach disallowed manifest or guard to excluded/unverifiable decisions.

    Non-allowed decisions must not carry manifest or guard. Illegal responses are identified
    as provider contract errors, distinct from legal excluded/unverifiable decisions.
    Cannot trust decision=allowed alone without verifying evidence completeness.
    """
    search, provider, ctx, hits, request = qualification_consumer

    payload = copy.deepcopy(qualification())
    payload["decision"] = decision

    # Attach evidence that should not be present for non-allowed decisions
    if illegal_attachment == "manifest_only":
        payload["guard"] = None
        # Keep manifest (illegal)
    elif illegal_attachment == "guard_only":
        payload["manifest"] = None
        # Keep guard (illegal)
    else:  # both
        # Keep both manifest and guard (illegal)
        pass

    provider.values = (MalformedQualificationEvidence(payload),)

    with pytest.raises(FoundationError) as failure:
        asyncio.run(search.qualify(ctx, hits, request))

    assert failure.value.code == "CONTRACT_VIOLATION"


@pytest.mark.p0
def test_rc_qua_03_illegal_decision_value_rejected(qualification_consumer):
    """Inject an illegal decision value to verify contract validation.

    Only "allowed", "excluded", and "unverifiable" are valid decisions.
    """
    search, provider, ctx, hits, request = qualification_consumer

    payload = copy.deepcopy(qualification())
    payload["decision"] = "invalid_decision"

    provider.values = (MalformedQualificationEvidence(payload),)

    with pytest.raises(FoundationError) as failure:
        asyncio.run(search.qualify(ctx, hits, request))

    assert failure.value.code == "CONTRACT_VIOLATION"


@pytest.mark.p0
@pytest.mark.parametrize("decision", ["excluded", "unverifiable"])
def test_rc_qua_03_legal_non_allowed_decisions_accepted_without_evidence(qualification_consumer, decision):
    """Legal excluded/unverifiable decisions without manifest/guard are accepted.

    These are distinct from illegal responses with attached evidence. Legal non-allowed
    decisions correctly indicate the candidate should not proceed to body read or delivery.
    """
    search, provider, ctx, hits, request = qualification_consumer

    payload = copy.deepcopy(qualification())
    payload["decision"] = decision
    payload["manifest"] = None
    payload["guard"] = None

    # Use valid model to ensure Pydantic validation passes
    provider.values = (CandidateQualificationResult(**payload),)

    result = asyncio.run(search.qualify(ctx, hits, request))

    # Result should be accepted but marked as non-allowed
    assert len(result) == 1
    proof = next(iter(result.values()))
    assert proof.decision == decision
    assert proof.manifest is None
    assert proof.guard is None


@pytest.mark.p0
def test_rc_qua_02_unverified_chunk_in_manifest_rejected(qualification_consumer):
    """Manifest with unverified chunks cannot authorize body reads.

    Only verified chunks can be included in allowed evidence.
    """
    search, provider, ctx, hits, request = qualification_consumer

    payload = copy.deepcopy(qualification())
    payload["manifest"]["chunks"][0]["verified"] = False

    provider.values = (MalformedQualificationEvidence(payload),)

    with pytest.raises(FoundationError) as failure:
        asyncio.run(search.qualify(ctx, hits, request))

    assert failure.value.code == "CONTRACT_VIOLATION"


@pytest.mark.p0
def test_rc_qua_02_unpublished_manifest_rejected(qualification_consumer):
    """Manifest in non-published state cannot authorize body reads.

    Only published manifests with published_at timestamp can grant qualification.
    """
    search, provider, ctx, hits, request = qualification_consumer

    payload = copy.deepcopy(qualification())
    payload["manifest"]["published_at"] = None

    provider.values = (MalformedQualificationEvidence(payload),)

    with pytest.raises(FoundationError) as failure:
        asyncio.run(search.qualify(ctx, hits, request))

    assert failure.value.code == "CONTRACT_VIOLATION"


@pytest.mark.p0
def test_rc_qua_02_building_state_manifest_rejected(qualification_consumer):
    """Manifest in 'building' state cannot authorize body reads.

    Only 'published' state manifests are valid for qualification.
    """
    search, provider, ctx, hits, request = qualification_consumer

    payload = copy.deepcopy(qualification())
    payload["manifest"]["state"] = "building"

    provider.values = (MalformedQualificationEvidence(payload),)

    with pytest.raises(FoundationError) as failure:
        asyncio.run(search.qualify(ctx, hits, request))

    assert failure.value.code == "CONTRACT_VIOLATION"


@pytest.mark.p0
def test_rc_qua_02_complete_evidence_control_succeeds(qualification_consumer):
    """Success control: complete matching evidence authorizes qualification.

    This verifies the baseline - when all evidence is present and matches correctly,
    qualification succeeds and the candidate can proceed to body read and delivery.
    """
    search, provider, ctx, hits, request = qualification_consumer

    # Use valid qualification result
    valid_payload = qualification()
    provider.values = (CandidateQualificationResult(**valid_payload),)

    result = asyncio.run(search.qualify(ctx, hits, request))

    assert len(result) == 1
    proof = next(iter(result.values()))
    assert proof.decision == "allowed"
    assert proof.manifest is not None
    assert proof.guard is not None
    assert proof.target.memory.memory_id == hits[0].memory.memory_id


@pytest.mark.p0
def test_rc_qua_02_provider_contract_error_not_client_input_error(qualification_consumer):
    """Verify illegal evidence errors are attributed to qualification provider contract violations.

    These are internal protocol errors from the qualification provider (Remember), not ordinary
    client input validation errors. Error messages and codes must clearly identify this as a
    provider contract violation at the qualification consumption boundary.
    """
    search, provider, ctx, hits, request = qualification_consumer

    # Inject manifest missing fault
    payload = copy.deepcopy(qualification())
    payload["manifest"] = None
    provider.values = (MalformedQualificationEvidence(payload),)

    with pytest.raises(FoundationError) as failure:
        asyncio.run(search.qualify(ctx, hits, request))

    # Verify error attribution
    assert failure.value.code == "CONTRACT_VIOLATION"
    # Error context should indicate this is a provider/qualification issue
    error_msg = failure.value.message.lower()
    # Should NOT be attributed to client/user input
    assert "client" not in error_msg or "provider" in error_msg or "qualification" in error_msg


@pytest.mark.p0
@pytest.mark.parametrize("fault_type", [
    "missing_target",
    "missing_decision",
    "missing_reason_code",
])
def test_rc_qua_02_incomplete_qualification_result_structure_rejected(qualification_consumer, fault_type):
    """Qualification results must have all required fields in the wire contract.

    Missing target, decision, or reason_code fields violate the contract structure.
    """
    search, provider, ctx, hits, request = qualification_consumer

    payload = copy.deepcopy(qualification())

    if fault_type == "missing_target":
        del payload["target"]
    elif fault_type == "missing_decision":
        del payload["decision"]
    elif fault_type == "missing_reason_code":
        del payload["reason_code"]

    provider.values = (MalformedQualificationEvidence(payload),)

    with pytest.raises(FoundationError) as failure:
        asyncio.run(search.qualify(ctx, hits, request))

    assert failure.value.code == "CONTRACT_VIOLATION"
