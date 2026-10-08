"""AET-17: fusion at candidate, persisted-plan and concurrent discovery seams.

Uses test-owned Azure/Temporal fixtures. Fixed candidate routes isolate fusion;
qualification tests use real MemoryCandidates search with the existing B double.
No persistence or production authorization is replaced.
"""

import asyncio
import copy
from uuid import uuid4

import pytest
from test_flows import app as azure_app
from test_flows import context, drain, save
from test_generation_assembly import BodyAuthority, assembly_setup
from test_generation_candidates import setup

from aether_agent_memory.recall.basic.assembly import ContextAssembly
from aether_agent_memory.recall.contracts.foundation import MemorySearchResult, RecallPlanRequest
from aether_agent_memory.recall.contracts.models import RecallRequest
from aether_agent_memory.runtime.contracts.models import ScopeSelector
from aether_agent_memory.runtime.foundation.common import fingerprint

pytestmark = pytest.mark.integration
app = azure_app


class FixedRoutes:
    """Validated candidate-port fixture: W=[A v2,B], L=[C,A v2]."""

    def __init__(self, candidates):
        self.candidates = {c.memory.memory_id: c for c in candidates}

    async def search(self, ctx, request):
        names = ("m1", "m2") if request.memory_source == "working" else ("m3", "m1")
        candidates = []
        for rank, name in enumerate(names, 1):
            candidate = self.candidates[name]
            # Source scores are fixture inputs, independent of expected RRF scores.
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


@pytest.mark.p0
def test_rc_fus_01_03_exact_version_cross_source_plan_has_one_contribution_per_route(app):
    ctx, search, authority, search_request = setup(app)
    # Publish fixture A at v2; both sources must cite that exact qualified version.
    for manifest, guard in authority.proofs.values():
        if manifest["memory"]["memory_id"] == "m1":
            manifest["memory"]["version"] = guard["memory"]["version"] = 2
    with app.foundation.uow.transaction() as tx:
        for key, row in tx.rows("generation_vectors"):
            if row["hit"]["memory"]["memory_id"] == "m1":
                row["hit"]["memory"]["version"] = 2
                tx.write("generation_vectors", key, row)
    body = BodyAuthority(app, authority.proofs)
    app.recall.memories = body
    # A v1 remains physically searchable with a higher score. B excludes it,
    # independently of the current v2 publication, before fusion assigns rank.
    with app.foundation.uow.transaction() as tx:
        key, row = next(
            (key, row)
            for key, row in tx.rows("generation_vectors")
            if row["hit"]["memory"]["memory_id"] == "m1"
        )
        stale = copy.deepcopy(row)
        stale_id = fingerprint(["stale", key])
        stale["hit"]["memory"]["version"] = 1
        stale["hit"].update(vector_id=stale_id, score=0.99)
        stale["vector"] = [0.99, 0.0]
        tx.write("generation_vectors", stale_id, stale)
    authority.proofs[stale_id] = copy.deepcopy(authority.proofs[key])
    stale_manifest, stale_guard = authority.proofs[stale_id]
    stale_manifest["memory"]["version"] = stale_guard["memory"]["version"] = 1
    stale_manifest["chunks"][stale["hit"]["chunk_index"]]["vector_id"] = stale_id
    original_qualify = authority.qualify

    async def qualify(ctx, targets, purpose):
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
            if result.target.memory.version == 1 and result.target.memory.memory_id == "m1"
            else result
            for result in results
        )

    authority.qualify = qualify
    search_request = search_request.model_copy(update={"memory_top_k": 3})
    found = asyncio.run(search.search(ctx, search_request))
    assert len(found.candidates) == 3
    assert any(
        t.memory.version == 1 and t.memory.memory_id == "m1"
        for batch in authority.calls
        for t in batch
    )
    assert next(c for c in found.candidates if c.memory.memory_id == "m1").memory.version == 2
    assembly = ContextAssembly(app.recall, FixedRoutes(found.candidates), body, body)
    request = RecallPlanRequest(
        recall_id="f3_" + uuid4().hex,
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
    assert [u.bodies[0].memory.memory_id for u in plan.units] == ["m1", "m3", "m2"]
    evidence = {(e.memory.memory_id, e.source): e for e in plan.rank_evidence}
    assert set(evidence) == {
        ("m1", "working"),
        ("m1", "long_term"),
        ("m2", "working"),
        ("m3", "long_term"),
    }
    for key, rank in {
        ("m1", "working"): 1,
        ("m1", "long_term"): 2,
        ("m2", "working"): 2,
        ("m3", "long_term"): 1,
    }.items():
        entry = evidence[key]
        assert entry.rrf_k == 60 and entry.source_rank == rank
        assert entry.rrf_contribution == pytest.approx(
            0.01639344262295082 if rank == 1 else 0.016129032258064516,
            rel=0,
            abs=1e-12,
        )
        if key[0] == "m1":
            assert entry.memory.version == 2


@pytest.mark.p1
@pytest.mark.parametrize("source", ["working", "long_term"])
def test_rc_fus_04_single_source_keeps_memory_order_without_chunk_rrf(app, source):
    ctx, assembly, body, request = assembly_setup(app, sources=(source,), token_budget=4096)
    plan = asyncio.run(assembly.plan(ctx, request))
    expected = ["m1"] if source == "working" else ["m1", "m2"]
    assert [u.bodies[0].memory.memory_id for u in plan.units] == expected
    assert plan.rank_evidence == ()
    # m1 has two chunks, m2 one; each logical memory occupies only one unit.
    assert len(plan.units) == len(expected)


@pytest.mark.p0
def test_rc_fus_05_excluded_and_unverifiable_hits_do_not_consume_fusion_rank(app):
    ctx, search, authority, request = setup(
        app, memories=(("old", (0.99,)), ("unknown", (0.95,)), ("A", (0.8,)), ("B", (0.7,)))
    )
    authority.decisions.update(old="excluded", unknown="unverifiable")
    found = asyncio.run(search.search(ctx, request))
    assert [c.memory.memory_id for c in found.candidates] == ["A", "B"]
    assert [c.rank for c in found.candidates] == [1, 2]
    assert found.coverage == "partial"
    # Consume the real qualified list at the planning boundary, preserving coverage.
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
    assert [u.bodies[0].memory.memory_id for u in plan.units] == ["A", "B"]
    assert set(plan.degradation_reasons) == {"working_top_k", "long_term_top_k"}
    assert len(plan.rank_evidence) == 4
    assert all(e.source_rank == (1 if e.memory.memory_id == "A" else 2) for e in plan.rank_evidence)
    assert "old" not in plan.rendered_context and "unknown" not in plan.rendered_context


@pytest.mark.p1
@pytest.mark.parametrize("first", ["working", "long_term"])
def test_rc_fus_07_completion_order_and_replayed_discovery_preserve_pack(app, first, monkeypatch):
    save(app, "我喜欢无糖咖啡")
    save(app, "我喜欢无糖红茶")
    drain(app)
    ctx = context(app)
    request = RecallRequest(
        query="我喜欢无糖饮品", sources="both", selection=ScopeSelector(session_id="session_1")
    )
    recall_id = "callbacks_" + uuid4().hex

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
            if source != first:
                await asyncio.wait_for(release.wait(), timeout=30)
            result = await vector_search(ctx, request)
            completed.append(source)
            release.set()
            # Repeated provider hits retain their original Ref and rank.
            return result.model_copy(update={"candidates": result.candidates * 2})

        monkeypatch.setattr(app.vectors, "search", controlled_search)
        prepared = await prepare()
        assert completed == [first, "long_term" if first == "working" else "working"]
        return prepared

    # Re-delivery invokes the discovery activity again with the same identity;
    # its result replaces the prior result rather than becoming another source.
    first_delivery = asyncio.run(replay())
    repeated_delivery = asyncio.run(replay())
    assert first_delivery["pack"] == repeated_delivery["pack"] == baseline["pack"]
