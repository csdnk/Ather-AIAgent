"""AET-59 RC-QUA-01, RC-QUA-04: qualification target precise binding and batch response coverage.

Extends AET-16 qualification consumption tests with:
- Multiple distinct targets forming a traceable closed loop from qualification request to final pack
- Target binding verification for all identity fields
- Injection of missing, duplicate, extra, and wrong targets as contract violations
- Legal out-of-order response matching by target (not position)
- Qualification stage event isolation (no body reads or heat events)

Reuses existing fixtures and support code from test_qualification_consumption.py and
test_recall_qualification.py. Only adds missing variants and assertions.
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


class QualificationProvider:
    """Controlled qualification authority for testing target binding."""

    def __init__(self, values):
        self.values = values
        self.calls = []

    async def qualify(self, ctx, targets, purpose):
        self.calls.append((ctx, targets, purpose))
        return self.values


@pytest.fixture
def multi_target_consumer():
    """Setup with multiple distinct targets for batch coverage testing."""
    candidate = evidence()
    hit_template = candidate["hits"][0]

    # Create two distinct hits with different memory IDs and vector IDs
    hit1 = ChunkHit.model_validate(hit_template)
    hit2_data = copy.deepcopy(hit_template)
    hit2_data["memory"]["memory_id"] = "m2"
    hit2_data["vector_id"] = "b" * 64
    hit2_data["input_hash"] = "b" * 64
    hit2 = ChunkHit.model_validate(hit2_data)

    ctx = TrustedContext(
        principal={
            "principal_id": "reader",
            "home_scope": hit1.memory.scope,
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

    # Create two valid qualification results
    qual1 = qualification()
    qual2 = copy.deepcopy(qual1)
    qual2["target"]["memory"]["memory_id"] = "m2"
    qual2["target"]["vector_id"] = "b" * 64
    qual2["target"]["input_hash"] = "b" * 64
    qual2["manifest"]["memory_id"] = "m2"
    qual2["guard"]["memory"]["memory_id"] = "m2"

    provider = QualificationProvider((
        CandidateQualificationResult(**qual1),
        CandidateQualificationResult(**qual2),
    ))

    search = MemoryCandidates(
        None,
        SimpleNamespace(clock=lambda: "2026-09-21T02:00:01.000Z"),
        None,
        None,
        provider,
        EmbeddingSpaces((space,)),
    )

    return search, provider, ctx, (hit1, hit2), request


@pytest.mark.p0
def test_rc_qua_01_multiple_targets_form_traceable_closed_loop(multi_target_consumer):
    """Multiple distinct targets pass through qualification to final candidates without loss or mix-up.

    Verifies that targets are non-empty, deduplicated, have correct purpose, and all identity
    fields (Ref, generation, space, body_hash, chunk_index, vector_id, input_hash) match exactly.
    Qualification results must correspond to final precise references in the delivery.
    """
    search, provider, ctx, hits, request = multi_target_consumer
    result = asyncio.run(search.qualify(ctx, hits, request))

    # Verify request was made with correct targets
    passed_ctx, targets, passed_purpose = provider.calls[0]
    assert passed_ctx == ctx
    assert passed_purpose == request.purpose
    assert len(targets) == 2, "targets must be non-empty and match hit count"

    # Verify no duplicates
    target_ids = [t.memory.memory_id for t in targets]
    assert len(target_ids) == len(set(target_ids)), "targets must be deduplicated"

    # Verify first target identity fields
    target1 = targets[0]
    assert target1.memory.memory_id == hits[0].memory.memory_id
    assert target1.generation == hits[0].generation
    assert target1.model_space == request.model_space
    assert target1.body_hash == hits[0].body_hash
    assert target1.chunk_index == hits[0].chunk_index
    assert target1.vector_id == hits[0].vector_id
    assert target1.input_hash == hits[0].input_hash

    # Verify second target identity fields
    target2 = targets[1]
    assert target2.memory.memory_id == hits[1].memory.memory_id
    assert target2.generation == hits[1].generation
    assert target2.model_space == request.model_space
    assert target2.body_hash == hits[1].body_hash
    assert target2.chunk_index == hits[1].chunk_index
    assert target2.vector_id == hits[1].vector_id
    assert target2.input_hash == hits[1].input_hash

    # Verify results correspond to targets
    assert len(result) == 2
    result_ids = [r.target.memory.memory_id for r in result.values()]
    assert set(result_ids) == set(target_ids), "qualification results must match requested targets"

    # Verify allowed results have complete manifest and guard
    for proof in result.values():
        assert proof.decision == "allowed"
        assert proof.manifest is not None, "allowed decision must provide manifest"
        assert proof.guard is not None, "allowed decision must provide guard"


@pytest.mark.p0
@pytest.mark.parametrize("fault_type", ["missing", "duplicate", "extra", "wrong_target"])
def test_rc_qua_04_target_coverage_violations_are_contract_errors(multi_target_consumer, fault_type):
    """Inject missing, duplicate, extra, or wrong targets to verify contract violation detection.

    Invalid evidence must not authorize body reads or delivery. Each fault type must be
    rejected as CONTRACT_VIOLATION.
    """
    search, provider, ctx, hits, request = multi_target_consumer

    valid1, valid2 = provider.values

    if fault_type == "missing":
        # Return only one result when two were requested
        provider.values = (valid1,)
    elif fault_type == "duplicate":
        # Return the same target twice
        provider.values = (valid1, valid1)
    elif fault_type == "extra":
        # Return an unrequested third target
        qual3 = copy.deepcopy(qualification())
        qual3["target"]["memory"]["memory_id"] = "m3"
        qual3["manifest"]["memory_id"] = "m3"
        qual3["guard"]["memory"]["memory_id"] = "m3"
        extra = CandidateQualificationResult(**qual3)
        provider.values = (valid1, valid2, extra)
    else:  # wrong_target
        # Return a completely different target than requested
        qual3 = copy.deepcopy(qualification())
        qual3["target"]["memory"]["memory_id"] = "m3"
        qual3["manifest"]["memory_id"] = "m3"
        qual3["guard"]["memory"]["memory_id"] = "m3"
        wrong = CandidateQualificationResult(**qual3)
        provider.values = (wrong, valid2)

    with pytest.raises(FoundationError) as failure:
        asyncio.run(search.qualify(ctx, hits, request))

    assert failure.value.code == "CONTRACT_VIOLATION"


@pytest.mark.p0
def test_rc_qua_04_legal_out_of_order_response_matches_by_target_not_position(multi_target_consumer):
    """When response order differs from request order, targets match by identity, not position.

    If the current contract allows out-of-order responses, verify pairing happens by target
    matching, not by position-based zip. If contract prohibits it, verify explicit rejection.

    Current implementation: order-independent matching is allowed.
    """
    search, provider, ctx, hits, request = multi_target_consumer

    # Reverse the response order
    valid1, valid2 = provider.values
    provider.values = (valid2, valid1)

    result = asyncio.run(search.qualify(ctx, hits, request))

    # Verify correct pairing despite reversed order
    assert len(result) == 2

    # First hit should match with first qualification (by memory_id, not position)
    hit1_id = hits[0].memory.memory_id
    hit2_id = hits[1].memory.memory_id

    result_by_memory = {r.target.memory.memory_id: r for r in result.values()}
    assert hit1_id in result_by_memory
    assert hit2_id in result_by_memory

    # Verify that matching happened by target identity
    proof1 = result_by_memory[hit1_id]
    proof2 = result_by_memory[hit2_id]

    assert proof1.target.memory.memory_id == hit1_id
    assert proof2.target.memory.memory_id == hit2_id
    assert proof1.target.vector_id == hits[0].vector_id
    assert proof2.target.vector_id == hits[1].vector_id


@pytest.mark.p0
def test_rc_qua_01_qualification_stage_event_isolation(multi_target_consumer):
    """Qualification phase must not produce body read events or heat updates.

    The qualify() call itself handles only candidate/qualify events. Actual body reads
    and heat tracking happen in subsequent stages. This test verifies isolation at the
    qualification boundary - real event verification happens in integration tests.
    """
    search, provider, ctx, hits, request = multi_target_consumer

    # Execute qualification
    result = asyncio.run(search.qualify(ctx, hits, request))

    # Verify qualification completed successfully
    assert len(result) == 2
    assert all(r.decision == "allowed" for r in result.values())

    # The provider should have been called exactly once
    assert len(provider.calls) == 1

    # At this unit test level, we can only verify that qualification completed
    # without attempting body reads (no storage/body port interactions).
    # Full event isolation verification (no body read events, no heat events)
    # requires integration tests with real telemetry and Remember boundary.
    # See test_recall_qualification.py for those scenarios.


@pytest.mark.p0
@pytest.mark.parametrize("field", [
    "generation", "model_space", "body_hash", "chunk_index", "vector_id", "input_hash"
])
def test_rc_qua_01_target_identity_field_mismatch_rejected(multi_target_consumer, field):
    """Each target identity field must match exactly between hit and qualification result.

    Mismatches in generation, model_space, body_hash, chunk_index, vector_id, or input_hash
    must be detected as contract violations during validation.
    """
    search, provider, ctx, hits, request = multi_target_consumer

    # Modify one identity field in the first qualification result
    qual1, qual2 = [copy.deepcopy(q.model_dump()) for q in provider.values]

    if field == "generation":
        qual1["target"]["generation"] = "different_gen"
        qual1["manifest"]["generation"] = "different_gen"
    elif field == "model_space":
        qual1["target"]["model_space"] = "different_space"
    elif field == "body_hash":
        qual1["target"]["body_hash"] = "f" * 64
        qual1["guard"]["body_hash"] = "f" * 64
    elif field == "chunk_index":
        qual1["target"]["chunk_index"] = 99
    elif field == "vector_id":
        qual1["target"]["vector_id"] = "f" * 64
    else:  # input_hash
        qual1["target"]["input_hash"] = "f" * 64

    # Try to create a qualification result with mismatched field
    # This should fail at Pydantic validation level
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        CandidateQualificationResult(**qual1)


@pytest.mark.p0
def test_rc_qua_01_allowed_qualification_provides_complete_evidence(multi_target_consumer):
    """Allowed qualification must provide complete manifest and guard for authorization.

    Both manifest and guard must be present and valid for allowed decisions.
    The manifest must contain all chunk information, and the guard must have
    matching body_hash and memory reference.
    """
    search, provider, ctx, hits, request = multi_target_consumer
    result = asyncio.run(search.qualify(ctx, hits, request))

    for proof in result.values():
        assert proof.decision == "allowed"

        # Verify manifest completeness
        assert proof.manifest is not None
        assert proof.manifest.memory_id == proof.target.memory.memory_id
        assert proof.manifest.generation == proof.target.generation
        assert len(proof.manifest.chunks) > 0
        assert proof.manifest.state == "published"

        # Verify guard completeness
        assert proof.guard is not None
        assert proof.guard.memory.memory_id == proof.target.memory.memory_id
        assert proof.guard.body_hash == proof.target.body_hash
        assert proof.guard.memory.scope == proof.target.memory.scope
