"""AET-64 RC-FUS-03, RC-FUS-05, RC-FUS-06: post-qualification precise version fusion and identity preservation.

Verifies along qualification filtering, fusion, and final Pack that only precisely qualified candidates
contribute scores. Same body content must not lose different memory identities or sources.

Test cases:
- W has A v2, L has old A v1 and A v2, Remember allows only v2: merge only scope/ID/version-exact candidates
- Old v1 scores and sources don't merge into v2, record post-filter list and formal fusion contributions
- Top position old/revoked Ref, middle mixed unverifiable: unqualified items don't occupy formal rank or contribute scores
- Wrong source can't borrow illegal candidate scores to inflate other readable items
- Same body/body_hash, different ID or source evidence: don't merge by hash or lose source, preserve identity per contract

Extends AET-17, reuses AET-16 qualification consumption support.
"""

import asyncio
import copy
from uuid import uuid4

import pytest
from test_flows import app as app
from test_generation_assembly import BodyAuthority
from test_generation_candidates import setup

from aether_agent_memory.recall.basic.assembly import ContextAssembly
from aether_agent_memory.recall.contracts.foundation import MemorySearchResult, RecallPlanRequest
from aether_agent_memory.runtime.foundation.common import fingerprint

pytestmark = [pytest.mark.integration, pytest.mark.p0]


class VersionFilteredRoutes:
    """Routes that return both v1 and v2 of memory A for version filtering testing."""

    def __init__(self, candidates, v1_candidate):
        self.candidates = {c.memory.memory_id: c for c in candidates}
        self.v1_candidate = v1_candidate

    async def search(self, ctx, request):
        if request.memory_source == "working":
            # Working only has A v2
            names = [("m1", 2)]
        else:  # long_term
            # Long_term has both A v1 (old) and A v2 (current)
            names = [("m1", 1), ("m1", 2)]

        candidates = []
        for memory_id, version in names:
            if version == 1:
                candidate = self.v1_candidate
            else:
                candidate = self.candidates[memory_id]

            rank = len(candidates) + 1
            score = 0.9 - (rank - 1) * 0.1

            candidates.append(
                candidate.model_copy(
                    update={
                        "rank": rank,
                        "best_score": score,
                        "memory": candidate.memory.model_copy(update={"version": version}),
                        "hits": tuple(
                            h.model_copy(
                                update={
                                    "memory_source": request.memory_source,
                                    "memory": h.memory.model_copy(update={"version": version}),
                                    "score": score,
                                }
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


def test_rc_fus_03_only_exact_qualified_version_merged_old_version_filtered(app):
    """W has A v2, L has old A v1 and A v2, Remember allows only v2: merge only exact version matches.

    When multiple versions of the same memory appear in search results, only the precisely
    qualified version (matching scope, ID, and version) is merged for fusion scoring.
    Old v1 scores and sources don't contribute to v2's RRF score.
    """
    ctx, search, authority, search_request = setup(app)

    # Create v1 fixture for A (old version)
    v1_candidate = None
    for manifest, guard in list(authority.proofs.values()):
        if manifest["memory"]["memory_id"] == "m1":
            # Set v2 as current allowed version
            manifest["memory"]["version"] = guard["memory"]["version"] = 2

            # Create v1 fixtures (will be excluded by qualification)
            v1_manifest = copy.deepcopy(manifest)
            v1_manifest["memory"]["version"] = 1
            v1_guard = copy.deepcopy(guard)
            v1_guard["memory"]["version"] = 1

            # Create v1 candidate
            candidate = next(c for c in search.search(ctx, search_request).result().candidates if c.memory.memory_id == "m1")
            v1_candidate = candidate.model_copy(
                update={"memory": candidate.memory.model_copy(update={"version": 1})}
            )

            # Add v1 proofs for qualification to exclude
            v1_vector_id = fingerprint(["m1_v1"])
            authority.proofs[v1_vector_id] = (v1_manifest, v1_guard)

    # Update qualification to exclude v1
    original_qualify = authority.qualify

    async def qualify_filter_v1(ctx, targets, purpose):
        results = await original_qualify(ctx, targets, purpose)
        return tuple(
            result.model_copy(
                update={
                    "decision": "excluded",
                    "reason_code": "stale_version",
                    "manifest": None,
                    "guard": None,
                }
            )
            if result.target.memory.version == 1
            else result
            for result in results
        )

    authority.qualify = qualify_filter_v1

    with app.foundation.uow.transaction() as tx:
        for key, row in tx.rows("generation_vectors"):
            if row["hit"]["memory"]["memory_id"] == "m1":
                row["hit"]["memory"]["version"] = 2
                tx.write("generation_vectors", key, row)

    search_request = search_request.model_copy(update={"memory_top_k": 3})
    found = asyncio.run(search.search(ctx, search_request))
    body = BodyAuthority(app, authority.proofs)

    assembly = ContextAssembly(app.recall, VersionFilteredRoutes(found.candidates, v1_candidate), body, body)

    request = RecallPlanRequest(
        recall_id="version_filter_" + uuid4().hex,
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

    # Verify only v2 appears in final pack
    for unit in plan.units:
        if unit.bodies[0].memory.memory_id == "m1":
            assert unit.bodies[0].memory.version == 2

    # Verify rank evidence only includes v2 contributions
    evidence = {(e.memory.memory_id, e.memory.version, e.source): e for e in plan.rank_evidence}

    # Should only have v2 contributions, not v1
    assert ("m1", 2, "working") in evidence
    assert ("m1", 2, "long_term") in evidence
    assert ("m1", 1, "working") not in evidence
    assert ("m1", 1, "long_term") not in evidence

    # Verify v1 scores didn't merge into v2
    # If v1 contributed, we'd see 3 contributions for m1 (working v2, long_term v1, long_term v2)
    m1_contributions = [e for e in plan.rank_evidence if e.memory.memory_id == "m1"]
    assert len(m1_contributions) == 2  # Only v2 from both sources


def test_rc_fus_05_unqualified_items_do_not_consume_formal_rank_or_contribute_scores(app):
    """Top position old/revoked Ref, middle mixed unverifiable: unqualified items don't occupy rank or contribute.

    Excluded and unverifiable candidates are filtered out before fusion rank assignment.
    They don't consume rank positions and don't contribute to RRF scores.
    Coverage loss from unverifiable is still reported.
    """
    ctx, search, authority, request = setup(
        app, memories=(("old", (0.99,)), ("unknown", (0.95,)), ("A", (0.8,)), ("B", (0.7,)))
    )

    # Set qualification decisions
    authority.decisions.update(old="excluded", unknown="unverifiable")

    found = asyncio.run(search.search(ctx, request))

    # After qualification, only A and B remain
    assert [c.memory.memory_id for c in found.candidates] == ["A", "B"]
    # They get ranks 1 and 2 (not 3 and 4)
    assert [c.rank for c in found.candidates] == [1, 2]
    # Coverage is partial due to unverifiable
    assert found.coverage == "partial"

    body = BodyAuthority(app, authority.proofs)
    app.recall.memories = body

    class QualifiedRoutes:
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
                                ),
                            }
                        )
                        for c in found.candidates
                    ),
                }
            )

    assembly = ContextAssembly(app.recall, QualifiedRoutes(), body, body)

    plan_request = RecallPlanRequest(
        recall_id="filtered_" + uuid4().hex,
        query=request.query,
        selection=request.selection,
        sources=("working", "long_term"),
        token_budget=4096,
        context_tokenizer=app.recall.tokenizer.identifier,
        policy_version=app.recall.policy_version,
        deadline_at=ctx.deadline_at,
        long_term_search=request,
        working_search=request.model_copy(update={"memory_source": "working"}),
    )

    plan = asyncio.run(assembly.plan(ctx, plan_request))

    # Only A and B in final pack
    assert [u.bodies[0].memory.memory_id for u in plan.units] == ["A", "B"]

    # Degradation reasons reflect the coverage loss
    assert set(plan.degradation_reasons) == {"working_top_k", "long_term_top_k"}

    # Rank evidence only for A and B
    assert len(plan.rank_evidence) == 4  # A from 2 sources + B from 2 sources

    # Verify ranks are 1 and 2 (not 3 and 4)
    for e in plan.rank_evidence:
        assert e.source_rank in [1, 2]
        if e.memory.memory_id == "A":
            assert e.source_rank == 1
        else:  # B
            assert e.source_rank == 2

    # Verify "old" and "unknown" don't appear in rendered context
    assert "old" not in plan.rendered_context
    assert "unknown" not in plan.rendered_context


def test_rc_fus_05_wrong_source_cannot_borrow_illegal_candidate_scores(app):
    """Wrong source can't borrow illegal candidate scores to inflate other readable items.

    When one source has unqualified candidates with high scores, those scores must not
    inflate the RRF contributions of qualified candidates from other sources.
    """
    ctx, search, authority, request = setup(
        app, memories=(("excluded_high", (0.99,)), ("valid", (0.8,)))
    )

    # Exclude the high-scoring candidate
    authority.decisions["excluded_high"] = "excluded"

    found = asyncio.run(search.search(ctx, request))

    # Only valid candidate remains
    assert [c.memory.memory_id for c in found.candidates] == ["valid"]
    assert found.candidates[0].rank == 1  # Gets rank 1, not rank 2

    body = BodyAuthority(app, authority.proofs)

    class SingleQualifiedRoute:
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

    assembly = ContextAssembly(app.recall, SingleQualifiedRoute(), body, body)

    plan_request = RecallPlanRequest(
        recall_id="no_borrow_" + uuid4().hex,
        query=request.query,
        selection=request.selection,
        sources=("working", "long_term"),
        token_budget=4096,
        context_tokenizer=app.recall.tokenizer.identifier,
        policy_version=app.recall.policy_version,
        deadline_at=ctx.deadline_at,
        long_term_search=request,
        working_search=request.model_copy(update={"memory_source": "working"}),
    )

    plan = asyncio.run(assembly.plan(ctx, plan_request))

    # Verify valid candidate gets rank 1 contributions from both sources
    evidence = {(e.memory.memory_id, e.source): e for e in plan.rank_evidence}

    valid_working = evidence[("valid", "working")]
    valid_long_term = evidence[("valid", "long_term")]

    # Both should be rank 1 (not rank 2)
    assert valid_working.source_rank == 1
    assert valid_long_term.source_rank == 1

    # Both should have rank 1 contribution: 1/61
    assert valid_working.rrf_contribution == pytest.approx(1/61, rel=0, abs=1e-12)
    assert valid_long_term.rrf_contribution == pytest.approx(1/61, rel=0, abs=1e-12)


def test_rc_fus_06_same_body_different_id_preserves_identity_and_source(app):
    """Same body/body_hash, different ID or source: don't merge by hash, preserve identity per contract.

    When multiple memories have the same body content (same body_hash) but different IDs or sources,
    they must remain distinct in the final Pack. Don't merge by content hash and lose identity/source.
    """
    ctx, search, authority, request = setup(
        app, memories=(("A", (0.9,)), ("B", (0.8,)), ("C", (0.7,)))
    )

    # Make B and C have the same body_hash as A (same content, different IDs)
    shared_body_hash = None
    for manifest, guard in authority.proofs.values():
        if manifest["memory"]["memory_id"] == "A":
            shared_body_hash = guard["body_hash"]
            break

    for manifest, guard in authority.proofs.values():
        if manifest["memory"]["memory_id"] in ["B", "C"]:
            manifest["body_hash"] = shared_body_hash
            guard["body_hash"] = shared_body_hash
            manifest["vector_location"]["content_hash"] = shared_body_hash

    with app.foundation.uow.transaction() as tx:
        for key, row in tx.rows("generation_vectors"):
            if row["hit"]["memory"]["memory_id"] in ["B", "C"]:
                row["hit"]["body_hash"] = shared_body_hash
                tx.write("generation_vectors", key, row)

    found = asyncio.run(search.search(ctx, request))
    body = BodyAuthority(app, authority.proofs)

    class SameBodyRoutes:
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

    assembly = ContextAssembly(app.recall, SameBodyRoutes(), body, body)

    plan_request = RecallPlanRequest(
        recall_id="same_body_" + uuid4().hex,
        query=request.query,
        selection=request.selection,
        sources=("working", "long_term"),
        token_budget=4096,
        context_tokenizer=app.recall.tokenizer.identifier,
        policy_version=app.recall.policy_version,
        deadline_at=ctx.deadline_at,
        long_term_search=request,
        working_search=request.model_copy(update={"memory_source": "working"}),
    )

    plan = asyncio.run(assembly.plan(ctx, plan_request))

    # All three memories should appear as distinct units
    memory_ids = [u.bodies[0].memory.memory_id for u in plan.units]
    assert len(memory_ids) == 3
    assert set(memory_ids) == {"A", "B", "C"}

    # Each memory should have separate rank evidence entries
    evidence_by_memory = {}
    for e in plan.rank_evidence:
        memory_id = e.memory.memory_id
        if memory_id not in evidence_by_memory:
            evidence_by_memory[memory_id] = []
        evidence_by_memory[memory_id].append(e)

    # Each memory should have contributions from both sources
    assert len(evidence_by_memory) == 3
    for memory_id in ["A", "B", "C"]:
        assert len(evidence_by_memory[memory_id]) == 2  # working + long_term

    # Verify identities preserved in final pack
    for unit in plan.units:
        memory = unit.bodies[0].memory
        # Each should maintain its distinct memory_id
        assert memory.memory_id in ["A", "B", "C"]
        # All have same body_hash but different IDs
        assert unit.bodies[0].location.content_hash == shared_body_hash


def test_rc_fus_06_different_source_evidence_same_id_preserves_source(app):
    """Same ID, different source evidence: preserve both source contributions separately.

    When the same memory appears in both working and long_term sources, both contributions
    must be preserved with their respective source labels. Don't lose source attribution.
    """
    ctx, search, authority, request = setup(app)

    found = asyncio.run(search.search(ctx, request))
    body = BodyAuthority(app, authority.proofs)

    class DualSourceRoutes:
        async def search(self, ctx, request):
            # Return m1 from both sources
            candidates = []
            m1 = next(c for c in found.candidates if c.memory.memory_id == "m1")

            rank = 1
            score = 0.9
            candidates.append(
                m1.model_copy(
                    update={
                        "rank": rank,
                        "best_score": score,
                        "hits": tuple(
                            h.model_copy(
                                update={"memory_source": request.memory_source, "score": score}
                            )
                            for h in m1.hits
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

    assembly = ContextAssembly(app.recall, DualSourceRoutes(), body, body)

    plan_request = RecallPlanRequest(
        recall_id="dual_source_" + uuid4().hex,
        query=request.query,
        selection=request.selection,
        sources=("working", "long_term"),
        token_budget=4096,
        context_tokenizer=app.recall.tokenizer.identifier,
        policy_version=app.recall.policy_version,
        deadline_at=ctx.deadline_at,
        long_term_search=request,
        working_search=request.model_copy(update={"memory_source": "working"}),
    )

    plan = asyncio.run(assembly.plan(ctx, plan_request))

    # m1 should appear once in the pack
    assert len(plan.units) == 1
    assert plan.units[0].bodies[0].memory.memory_id == "m1"

    # But should have TWO rank evidence entries (one per source)
    assert len(plan.rank_evidence) == 2

    evidence_by_source = {e.source: e for e in plan.rank_evidence}
    assert set(evidence_by_source.keys()) == {"working", "long_term"}

    # Both contributions should be preserved
    assert evidence_by_source["working"].memory.memory_id == "m1"
    assert evidence_by_source["long_term"].memory.memory_id == "m1"
    assert evidence_by_source["working"].source_rank == 1
    assert evidence_by_source["long_term"].source_rank == 1
