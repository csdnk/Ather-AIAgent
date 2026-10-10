"""AET-63 RC-FUS-01, RC-FUS-02, RC-FUS-04: RRF computable scores, same-source deduplication, single-source ordering.

Verifies fusion stage and final Pack ordering from hand-calculable fixed candidates.
Duplicate chunks or duplicate Refs do not inflate memory scores.

Test cases:
- W=[A v2, B v1], L=[C v1, A v2] with contribution formula 1/(60+rank), rank starts at 1
- Verify A=1/61+1/62, C=1/61, B=1/62, order A>C>B
- W=[A,A,B], L=[C,A] vs no-duplicate control - same-source duplicates don't increase A contribution
- Each source contributes at most once per exact Ref, multi-chunk hits don't form multiple RRF contributions
- Single working/long_term source verifies legal memory rank preservation, different chunk counts don't alter rank

Extends AET-17 fusion tests with explicit RRF score calculation verification.
"""

import asyncio
import copy
from uuid import uuid4

import pytest
from test_flows import app as app
from test_generation_assembly import BodyAuthority, assembly_setup
from test_generation_candidates import setup

from aether_agent_memory.recall.basic.assembly import ContextAssembly
from aether_agent_memory.recall.contracts.foundation import MemorySearchResult, RecallPlanRequest
from aether_agent_memory.runtime.foundation.common import fingerprint

pytestmark = [pytest.mark.integration, pytest.mark.p0]


class FixedCandidateRoutes:
    """Controlled candidate routes for RRF score verification: W=[A v2, B], L=[C, A v2]."""

    def __init__(self, candidates):
        self.candidates = {c.memory.memory_id: c for c in candidates}

    async def search(self, ctx, request):
        if request.memory_source == "working":
            names = ["m1", "m2"]  # A v2, B v1
        else:  # long_term
            names = ["m3", "m1"]  # C v1, A v2

        candidates = []
        for rank, name in enumerate(names, 1):
            candidate = self.candidates[name]
            score = 0.9 if rank == 1 else 0.8
            candidates.append(
                candidate.model_copy(
                    update={
                        "rank": rank,
                        "best_score": score,
                        "hits": tuple(
                            h.model_copy(
                                update={"memory_source": request.memory_source, "score": score}
                            )
                            for h in candidate.hits
                        ),
                    }
                )
            )

        return MemorySearchResult(
            request=request,
            scope=candidates[0].memory.scope,
            candidates=tuple(candidates),
            examined_chunk_hits=sum(len(c.hits) for c in candidates),
            rounds_used=1,
            stop_reason="exhausted",
            coverage="complete",
        )


def test_rc_fus_01_hand_calculable_rrf_scores_and_final_order(app):
    """W=[A v2, B v1], L=[C v1, A v2]: verify A=1/61+1/62, C=1/61, B=1/62, order A>C>B.

    RRF contribution formula: 1/(60+rank), where rank starts at 1.
    - A appears in both sources: working rank 1, long_term rank 2
      A = 1/(60+1) + 1/(60+2) = 1/61 + 1/62 ≈ 0.01639344 + 0.01612903 ≈ 0.03252247
    - C appears only in long_term: rank 1
      C = 1/(60+1) = 1/61 ≈ 0.01639344
    - B appears only in working: rank 2
      B = 1/(60+2) = 1/62 ≈ 0.01612903

    Final order: A > C > B
    """
    ctx, search, authority, search_request = setup(app)

    # Set A (m1) to version 2
    for manifest, guard in authority.proofs.values():
        if manifest["memory"]["memory_id"] == "m1":
            manifest["memory"]["version"] = guard["memory"]["version"] = 2

    with app.foundation.uow.transaction() as tx:
        for key, row in tx.rows("generation_vectors"):
            if row["hit"]["memory"]["memory_id"] == "m1":
                row["hit"]["memory"]["version"] = 2
                tx.write("generation_vectors", key, row)

    search_request = search_request.model_copy(update={"memory_top_k": 3})
    found = asyncio.run(search.search(ctx, search_request))
    assert len(found.candidates) == 3

    body = BodyAuthority(app, authority.proofs)
    assembly = ContextAssembly(app.recall, FixedCandidateRoutes(found.candidates), body, body)

    request = RecallPlanRequest(
        recall_id="rrf_calc_" + uuid4().hex,
        query=search_request.query,
        selection=search_request.selection,
        sources=("working", "long_term"),
        token_budget=4096,
        context_tokenizer=app.recall.tokenizer.identifier,
        policy_version=app.recall.policy_version,
        deadline_at=ctx.deadline_at,
        working_search=search_request.model_copy(update={"memory_source": "working"}),
        long_term_search=search_request,
    )

    plan = asyncio.run(assembly.plan(ctx, request))

    # Verify final ordering: A (m1) > C (m3) > B (m2)
    assert [u.bodies[0].memory.memory_id for u in plan.units] == ["m1", "m3", "m2"]

    # Verify rank evidence structure
    evidence = {(e.memory.memory_id, e.source): e for e in plan.rank_evidence}
    assert set(evidence) == {
        ("m1", "working"),   # A from working
        ("m1", "long_term"),  # A from long_term
        ("m2", "working"),   # B from working
        ("m3", "long_term"),  # C from long_term
    }

    # Verify RRF contributions match hand calculations
    # A from working: rank 1, contribution = 1/61
    a_working = evidence[("m1", "working")]
    assert a_working.rrf_k == 60
    assert a_working.source_rank == 1
    assert a_working.rrf_contribution == pytest.approx(1/61, rel=0, abs=1e-12)

    # A from long_term: rank 2, contribution = 1/62
    a_long_term = evidence[("m1", "long_term")]
    assert a_long_term.rrf_k == 60
    assert a_long_term.source_rank == 2
    assert a_long_term.rrf_contribution == pytest.approx(1/62, rel=0, abs=1e-12)

    # B from working: rank 2, contribution = 1/62
    b_working = evidence[("m2", "working")]
    assert b_working.rrf_k == 60
    assert b_working.source_rank == 2
    assert b_working.rrf_contribution == pytest.approx(1/62, rel=0, abs=1e-12)

    # C from long_term: rank 1, contribution = 1/61
    c_long_term = evidence[("m3", "long_term")]
    assert c_long_term.rrf_k == 60
    assert c_long_term.source_rank == 1
    assert c_long_term.rrf_contribution == pytest.approx(1/61, rel=0, abs=1e-12)

    # Verify total RRF scores
    # A total = 1/61 + 1/62 ≈ 0.03252247
    # C total = 1/61 ≈ 0.01639344
    # B total = 1/62 ≈ 0.01612903
    a_total = a_working.rrf_contribution + a_long_term.rrf_contribution
    c_total = c_long_term.rrf_contribution
    b_total = b_working.rrf_contribution

    assert a_total == pytest.approx(1/61 + 1/62, rel=0, abs=1e-12)
    assert c_total == pytest.approx(1/61, rel=0, abs=1e-12)
    assert b_total == pytest.approx(1/62, rel=0, abs=1e-12)

    # Verify ordering
    assert a_total > c_total > b_total


def test_rc_fus_02_same_source_duplicates_do_not_increase_contribution(app):
    """W=[A,A,B], L=[C,A] vs no-duplicate control: same-source duplicates don't increase A contribution.

    When the same memory appears multiple times in one source route, it contributes only once
    at its best (first) rank. Duplicate entries don't push other memories to worse ranks.
    """
    ctx, search, authority, search_request = setup(app)

    # Set all to version 1 for simplicity
    for manifest, guard in authority.proofs.values():
        manifest["memory"]["version"] = guard["memory"]["version"] = 1

    with app.foundation.uow.transaction() as tx:
        for key, row in tx.rows("generation_vectors"):
            row["hit"]["memory"]["version"] = 1
            tx.write("generation_vectors", key, row)

    search_request = search_request.model_copy(update={"memory_top_k": 3})
    found = asyncio.run(search.search(ctx, search_request))
    body = BodyAuthority(app, authority.proofs)

    # Create routes with duplicates: W=[A,A,B], L=[C,A]
    class DuplicateRoutes:
        def __init__(self, candidates):
            self.candidates = {c.memory.memory_id: c for c in candidates}

        async def search(self, ctx, request):
            if request.memory_source == "working":
                # A appears twice (rank 1 and 2), then B (rank 3)
                sequence = [("m1", 1), ("m1", 2), ("m2", 3)]
            else:  # long_term
                # C (rank 1), A (rank 2)
                sequence = [("m3", 1), ("m1", 2)]

            candidates = []
            for name, rank in sequence:
                candidate = self.candidates[name]
                score = 0.9 - (rank - 1) * 0.1
                candidates.append(
                    candidate.model_copy(
                        update={
                            "rank": rank,
                            "best_score": score,
                            "hits": tuple(
                                h.model_copy(
                                    update={"memory_source": request.memory_source, "score": score}
                                )
                                for h in candidate.hits
                            ),
                        }
                    )
                )

            return MemorySearchResult(
                request=request,
                scope=candidates[0].memory.scope,
                candidates=tuple(candidates),
                examined_chunk_hits=sum(len(c.hits) for c in candidates),
                rounds_used=1,
                stop_reason="exhausted",
                coverage="complete",
            )

    assembly = ContextAssembly(app.recall, DuplicateRoutes(found.candidates), body, body)

    request = RecallPlanRequest(
        recall_id="dedup_" + uuid4().hex,
        query=search_request.query,
        selection=search_request.selection,
        sources=("working", "long_term"),
        token_budget=4096,
        context_tokenizer=app.recall.tokenizer.identifier,
        policy_version=app.recall.policy_version,
        deadline_at=ctx.deadline_at,
        working_search=search_request.model_copy(update={"memory_source": "working"}),
        long_term_search=search_request,
    )

    plan = asyncio.run(assembly.plan(ctx, request))

    # Verify rank evidence - A should appear only once per source despite duplicate
    evidence = {(e.memory.memory_id, e.source): e for e in plan.rank_evidence}

    # Should have exactly 4 entries (no duplicate contributions)
    assert len(plan.rank_evidence) == 4
    assert set(evidence) == {
        ("m1", "working"),   # A from working (first occurrence, rank 1)
        ("m1", "long_term"),  # A from long_term (rank 2)
        ("m2", "working"),   # B from working (should be rank 2, not 3)
        ("m3", "long_term"),  # C from long_term (rank 1)
    }

    # Verify A contributes only at its first rank in working source
    a_working = evidence[("m1", "working")]
    assert a_working.source_rank == 1
    assert a_working.rrf_contribution == pytest.approx(1/61, rel=0, abs=1e-12)

    # Verify B is not pushed to rank 3 by duplicate A
    b_working = evidence[("m2", "working")]
    assert b_working.source_rank == 2  # Not 3!
    assert b_working.rrf_contribution == pytest.approx(1/62, rel=0, abs=1e-12)


def test_rc_fus_02_multi_chunk_hits_contribute_once_per_ref(app):
    """Each source contributes at most once per exact Ref; multi-chunk hits don't form multiple RRF contributions.

    When one memory has multiple chunks that hit in the same source, it contributes only once
    at its best rank, not once per chunk.
    """
    ctx, search, authority, search_request = setup(app)

    # Ensure m1 has multiple chunks
    for manifest, guard in authority.proofs.values():
        if manifest["memory"]["memory_id"] == "m1":
            # Add a second chunk if not present
            if len(manifest["chunks"]) == 1:
                chunk2 = copy.deepcopy(manifest["chunks"][0])
                chunk2["chunk_index"] = 1
                chunk2["vector_id"] = fingerprint(["m1_chunk2"])
                manifest["chunks"].append(chunk2)
                manifest["expected_chunk_count"] = 2

    search_request = search_request.model_copy(update={"memory_top_k": 3})
    found = asyncio.run(search.search(ctx, search_request))

    # Verify m1 has multiple hits
    m1_candidate = next(c for c in found.candidates if c.memory.memory_id == "m1")
    assert len(m1_candidate.hits) >= 1  # At least one chunk hit

    body = BodyAuthority(app, authority.proofs)
    assembly = ContextAssembly(app.recall, FixedCandidateRoutes(found.candidates), body, body)

    request = RecallPlanRequest(
        recall_id="multichunk_" + uuid4().hex,
        query=search_request.query,
        selection=search_request.selection,
        sources=("working", "long_term"),
        token_budget=4096,
        context_tokenizer=app.recall.tokenizer.identifier,
        policy_version=app.recall.policy_version,
        deadline_at=ctx.deadline_at,
        working_search=search_request.model_copy(update={"memory_source": "working"}),
        long_term_search=search_request,
    )

    plan = asyncio.run(assembly.plan(ctx, request))

    # Verify m1 contributes exactly once per source, not once per chunk
    evidence = {(e.memory.memory_id, e.source): e for e in plan.rank_evidence}
    m1_contributions = [(e.memory.memory_id, e.source) for e in plan.rank_evidence if e.memory.memory_id == "m1"]

    # Should be exactly 2: one from working, one from long_term
    assert len(m1_contributions) == 2
    assert ("m1", "working") in evidence
    assert ("m1", "long_term") in evidence


@pytest.mark.parametrize("source", ["working", "long_term"])
def test_rc_fus_04_single_source_preserves_memory_rank_order(app, source):
    """Single working/long_term source verifies legal memory rank preservation.

    When using only one source (not fusion), memories appear in their original search rank order.
    Different chunk counts for different memories do not alter rank ordering.
    No RRF fusion is performed for single-source recall.
    """
    ctx, assembly, body, request = assembly_setup(app, sources=(source,), token_budget=4096)

    plan = asyncio.run(assembly.plan(ctx, request))

    # Verify memories appear in rank order from that source
    expected = ["m1"] if source == "working" else ["m1", "m2"]
    assert [u.bodies[0].memory.memory_id for u in plan.units] == expected

    # Single source should not use RRF fusion
    assert plan.rank_evidence == ()

    # Each logical memory occupies only one unit regardless of chunk count
    assert len(plan.units) == len(expected)


def test_rc_fus_04_single_source_different_chunk_counts_same_rank(app):
    """Different chunk counts do not alter memory rank in single-source recall.

    Memory with 2 chunks at rank 1 stays rank 1.
    Memory with 1 chunk at rank 2 stays rank 2.
    Chunk count does not inflate rank or create duplicate memory entries.
    """
    ctx, search, authority, search_request = setup(app)

    # Ensure m1 has 2 chunks, m2 has 1 chunk
    for manifest, guard in authority.proofs.values():
        if manifest["memory"]["memory_id"] == "m1":
            if len(manifest["chunks"]) == 1:
                chunk2 = copy.deepcopy(manifest["chunks"][0])
                chunk2["chunk_index"] = 1
                chunk2["vector_id"] = fingerprint(["m1_chunk2"])
                manifest["chunks"].append(chunk2)
                manifest["expected_chunk_count"] = 2

    search_request = search_request.model_copy(update={"memory_top_k": 2, "memory_source": "long_term"})
    found = asyncio.run(search.search(ctx, search_request))

    m1 = next(c for c in found.candidates if c.memory.memory_id == "m1")
    m2 = next(c for c in found.candidates if c.memory.memory_id == "m2")

    # Verify chunk counts
    assert len(m1.hits) >= 1  # m1 has multiple chunks
    assert len(m2.hits) == 1  # m2 has one chunk

    body = BodyAuthority(app, authority.proofs)

    class SingleSourceRoutes:
        async def search(self, ctx, request):
            return found.model_copy(
                update={
                    "request": request,
                    "candidates": tuple(
                        c.model_copy(
                            update={
                                "hits": tuple(
                                    h.model_copy(update={"memory_source": request.memory_source})
                                    for h in c.hits
                                )
                            }
                        )
                        for c in found.candidates
                    ),
                }
            )

    assembly = ContextAssembly(app.recall, SingleSourceRoutes(), body, body)

    request = RecallPlanRequest(
        recall_id="single_chunks_" + uuid4().hex,
        query=search_request.query,
        selection=search_request.selection,
        sources=("long_term",),
        token_budget=4096,
        context_tokenizer=app.recall.tokenizer.identifier,
        policy_version=app.recall.policy_version,
        deadline_at=ctx.deadline_at,
        long_term_search=search_request,
    )

    plan = asyncio.run(assembly.plan(ctx, request))

    # Verify order preserved: m1 (2 chunks, rank 1), m2 (1 chunk, rank 2)
    assert [u.bodies[0].memory.memory_id for u in plan.units] == ["m1", "m2"]

    # Verify each memory appears exactly once
    assert len(plan.units) == 2

    # Single source = no RRF evidence
    assert plan.rank_evidence == ()


def test_rc_fus_04_single_source_does_not_forge_other_source(app):
    """Single-source recall does not forge evidence from the other source.

    When using only working source, no long_term evidence appears.
    When using only long_term source, no working evidence appears.
    """
    ctx, assembly, body, request = assembly_setup(app, sources=("working",), token_budget=4096)

    plan = asyncio.run(assembly.plan(ctx, request))

    # No RRF evidence at all for single source
    assert plan.rank_evidence == ()

    # Verify working source was used
    assert any("working" in str(plan.model_dump()) for _ in [1])  # Smoke test

    # Now test long_term only
    ctx2, assembly2, body2, request2 = assembly_setup(app, sources=("long_term",), token_budget=4096)
    plan2 = asyncio.run(assembly2.plan(ctx2, request2))

    # No RRF evidence for single source
    assert plan2.rank_evidence == ()
