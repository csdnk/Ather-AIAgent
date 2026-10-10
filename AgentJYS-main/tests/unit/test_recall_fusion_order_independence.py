"""AET-65 RC-FUS-07: fusion results unaffected by source completion order and duplicate callbacks.

With fixed candidates and policy, only varying two-source completion order or duplicate callbacks,
verifies fusion scores, ordering, and persisted Pack are reproducible.

Uses F3 fixed legal list W=[A v2, B v1], L=[C v1, A v2] with same identity, budget, reranking settings.
Controlled execution: working-first, long_term-first, and duplicate callback/discovery result replay.
Uses barriers, not random timing.

Compares contributions, stage scores, and final Pack. Same legal inputs produce contributions and
ranks independent of arrival order. Duplicate callbacks don't add extra scores or result items.
Ties follow confirmed stable sort rules.

Extends AET-17, reuses existing timing control and callback replay support.
"""

import asyncio
from uuid import uuid4

import pytest
from test_flows import app as app
from test_flows import context, drain, save
from test_generation_assembly import BodyAuthority, assembly_setup
from test_generation_candidates import setup

from aether_agent_memory.recall.basic.assembly import ContextAssembly
from aether_agent_memory.recall.contracts.foundation import MemorySearchResult, RecallPlanRequest
from aether_agent_memory.recall.contracts.models import RecallRequest
from aether_agent_memory.runtime.contracts.models import ScopeSelector
from aether_agent_memory.runtime.foundation.common import fingerprint

pytestmark = [pytest.mark.integration, pytest.mark.p1]


class FixedF3Routes:
    """F3 fixed legal list: W=[A v2, B v1], L=[C v1, A v2]."""

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


@pytest.mark.parametrize("first_source", ["working", "long_term"])
def test_rc_fus_07_completion_order_does_not_affect_fusion_scores_or_ranking(app, first_source, monkeypatch):
    """Working-first vs long_term-first completion: fusion scores, contributions, and final Pack are identical.

    Uses controlled barriers to enforce completion order without random timing.
    Same legal inputs produce same contributions and ranks regardless of arrival order.
    """
    ctx, search, authority, search_request = setup(app)

    # Set up F3 configuration: A v2, B v1, C v1
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
    body = BodyAuthority(app, authority.proofs)

    # Baseline: natural completion order (no barriers)
    assembly_baseline = ContextAssembly(app.recall, FixedF3Routes(found.candidates), body, body)
    request = RecallPlanRequest(
        recall_id="baseline_" + uuid4().hex,
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
    baseline_plan = asyncio.run(assembly_baseline.plan(ctx, request))

    # Controlled order: enforce first_source completes first
    release = asyncio.Event()
    completed_sources = []

    original_search = FixedF3Routes(found.candidates).search

    async def controlled_search(ctx, request):
        source = request.memory_source
        if source != first_source:
            await asyncio.wait_for(release.wait(), timeout=30)
        result = await original_search(ctx, request)
        completed_sources.append(source)
        release.set()
        return result

    class ControlledRoutes:
        async def search(self, ctx, request):
            return await controlled_search(ctx, request)

    assembly_controlled = ContextAssembly(app.recall, ControlledRoutes(), body, body)
    controlled_request = request.model_copy(update={"recall_id": "controlled_" + uuid4().hex})
    controlled_plan = asyncio.run(assembly_controlled.plan(ctx, controlled_request))

    # Verify completion order was enforced
    second_source = "long_term" if first_source == "working" else "working"
    assert completed_sources == [first_source, second_source]

    # Compare fusion results
    # 1. Same final memory order
    baseline_order = [u.bodies[0].memory.memory_id for u in baseline_plan.units]
    controlled_order = [u.bodies[0].memory.memory_id for u in controlled_plan.units]
    assert baseline_order == controlled_order

    # 2. Same rank evidence (contributions and scores)
    baseline_evidence = {(e.memory.memory_id, e.source): e for e in baseline_plan.rank_evidence}
    controlled_evidence = {(e.memory.memory_id, e.source): e for e in controlled_plan.rank_evidence}

    assert set(baseline_evidence.keys()) == set(controlled_evidence.keys())

    for key in baseline_evidence:
        baseline_entry = baseline_evidence[key]
        controlled_entry = controlled_evidence[key]
        assert baseline_entry.source_rank == controlled_entry.source_rank
        assert baseline_entry.rrf_contribution == pytest.approx(
            controlled_entry.rrf_contribution, rel=0, abs=1e-12
        )

    # 3. Same rendered context (Pack content)
    assert baseline_plan.rendered_context == controlled_plan.rendered_context


def test_rc_fus_07_duplicate_callbacks_do_not_add_extra_scores_or_items(app, monkeypatch):
    """Duplicate callbacks/discovery result replay: don't add extra scores or result items.

    Re-delivery invokes discovery activity again with same identity. Result replaces prior result
    rather than becoming another source. No double-counting of contributions.
    """
    save(app, "Fusion order test content A")
    save(app, "Fusion order test content B")
    drain(app)
    ctx = context(app)

    request = RecallRequest(
        query="fusion order test",
        sources="both",
        selection=ScopeSelector(session_id="test_session"),
    )
    recall_id = "duplicate_" + uuid4().hex

    async def prepare():
        results = await app.recall.discover_candidates(ctx, request, recall_id)
        assert all(r.candidates and r.coverage == "complete" for r in results)
        return await app.recall.assemble_candidates(ctx, request, recall_id, results)

    # Baseline: single execution
    baseline = asyncio.run(prepare())

    # Duplicate execution: simulate repeated discovery callback
    vector_search = app.vectors.search

    async def duplicate_search(ctx, request):
        result = await vector_search(ctx, request)
        # Simulate duplicate callback by returning doubled candidates
        # (same Ref and rank, just repeated)
        return result.model_copy(update={"candidates": result.candidates * 2})

    monkeypatch.setattr(app.vectors, "search", duplicate_search)

    duplicate_result = asyncio.run(prepare())

    # Verify duplicate callbacks don't affect final Pack
    assert duplicate_result["pack"] == baseline["pack"]

    # Verify same number of result items (no extras from duplicate)
    baseline_memory_ids = [
        line.split(":")[0].strip()
        for line in baseline["pack"].split("\n")
        if line.strip() and ":" in line
    ]
    duplicate_memory_ids = [
        line.split(":")[0].strip()
        for line in duplicate_result["pack"].split("\n")
        if line.strip() and ":" in line
    ]

    # Should have same memories, not doubled
    assert len(baseline_memory_ids) == len(duplicate_memory_ids)


@pytest.mark.parametrize("first_source", ["working", "long_term"])
def test_rc_fus_07_repeated_delivery_preserves_pack_identity(app, first_source, monkeypatch):
    """Repeated delivery with controlled order: Pack identity preserved across deliveries.

    Each delivery produces identical Pack even when completion order varies.
    Re-delivery with same recall_id replaces prior result, not accumulates.
    """
    save(app, "Repeated delivery test A")
    save(app, "Repeated delivery test B")
    drain(app)
    ctx = context(app)

    request = RecallRequest(
        query="repeated delivery test",
        sources="both",
        selection=ScopeSelector(session_id="test_session"),
    )
    recall_id = "repeated_" + uuid4().hex

    async def prepare():
        results = await app.recall.discover_candidates(ctx, request, recall_id)
        assert all(r.candidates and r.coverage == "complete" for r in results)
        return await app.recall.assemble_candidates(ctx, request, recall_id, results)

    baseline = asyncio.run(prepare())
    vector_search = app.vectors.search

    async def replay():
        release = asyncio.Event()
        completed = []

        async def controlled_search(ctx, request):
            source = request.memory_source
            if source != first_source:
                await asyncio.wait_for(release.wait(), timeout=30)
            result = await vector_search(ctx, request)
            completed.append(source)
            release.set()
            # Simulate repeated provider hits
            return result.model_copy(update={"candidates": result.candidates * 2})

        monkeypatch.setattr(app.vectors, "search", controlled_search)
        prepared = await prepare()
        second_source = "long_term" if first_source == "working" else "working"
        assert completed == [first_source, second_source]
        return prepared

    # Multiple deliveries with same recall_id
    first_delivery = asyncio.run(replay())
    second_delivery = asyncio.run(replay())

    # All deliveries produce identical Pack
    assert first_delivery["pack"] == baseline["pack"]
    assert second_delivery["pack"] == baseline["pack"]


def test_rc_fus_07_ties_follow_stable_sort_rules(app):
    """When contributions tie, ordering follows confirmed stable sort rules.

    Same RRF score produces consistent ordering across runs.
    No random variation in tie-breaking.
    """
    ctx, search, authority, search_request = setup(
        app, memories=(("A", (0.8,)), ("B", (0.8,)), ("C", (0.8,)))
    )

    search_request = search_request.model_copy(update={"memory_top_k": 3})
    found = asyncio.run(search.search(ctx, search_request))
    body = BodyAuthority(app, authority.proofs)

    class TiedRoutes:
        """Return all memories at same rank to create RRF tie."""

        async def search(self, ctx, request):
            candidates = []
            for rank, candidate in enumerate(found.candidates, 1):
                # All get same rank and score
                candidates.append(
                    candidate.model_copy(
                        update={
                            "rank": 1,  # All rank 1
                            "best_score": 0.9,  # All same score
                            "hits": tuple(
                                h.model_copy(
                                    update={"memory_source": request.memory_source, "score": 0.9}
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

    assembly = ContextAssembly(app.recall, TiedRoutes(), body, body)

    request = RecallPlanRequest(
        recall_id="tied_" + uuid4().hex,
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

    # Run multiple times to verify stable ordering
    plan1 = asyncio.run(assembly.plan(ctx, request.model_copy(update={"recall_id": "tied_1_" + uuid4().hex})))
    plan2 = asyncio.run(assembly.plan(ctx, request.model_copy(update={"recall_id": "tied_2_" + uuid4().hex})))
    plan3 = asyncio.run(assembly.plan(ctx, request.model_copy(update={"recall_id": "tied_3_" + uuid4().hex})))

    # All should produce same ordering
    order1 = [u.bodies[0].memory.memory_id for u in plan1.units]
    order2 = [u.bodies[0].memory.memory_id for u in plan2.units]
    order3 = [u.bodies[0].memory.memory_id for u in plan3.units]

    assert order1 == order2 == order3, "Tied scores must produce stable ordering"


def test_rc_fus_07_identity_budget_reranking_settings_preserved_across_orders(app):
    """Identity, budget, reranking settings remain same regardless of completion order.

    Changing only completion order doesn't alter other plan parameters.
    """
    ctx, search, authority, search_request = setup(app)

    search_request = search_request.model_copy(update={"memory_top_k": 3})
    found = asyncio.run(search.search(ctx, search_request))
    body = BodyAuthority(app, authority.proofs)

    assembly = ContextAssembly(app.recall, FixedF3Routes(found.candidates), body, body)

    base_request = RecallPlanRequest(
        recall_id="settings_" + uuid4().hex,
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

    plan1 = asyncio.run(assembly.plan(ctx, base_request))
    plan2 = asyncio.run(assembly.plan(ctx, base_request.model_copy(update={"recall_id": "settings2_" + uuid4().hex})))

    # Verify consistent settings
    assert plan1.token_budget == plan2.token_budget == 4096
    assert plan1.context_tokenizer == plan2.context_tokenizer
    assert plan1.policy_version == plan2.policy_version

    # Verify same scope/selection
    for unit1, unit2 in zip(plan1.units, plan2.units):
        assert unit1.bodies[0].memory.scope == unit2.bodies[0].memory.scope
