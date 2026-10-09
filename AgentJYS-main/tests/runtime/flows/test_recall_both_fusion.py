"""AET-36 controlled F3 consumer tests at candidate/assembly ports.

These tests use an explicit B contract double and real RF/assembly. The companion
native HTTP suite establishes real Remember/search execution independently.
"""

import asyncio
from hashlib import sha256
from uuid import uuid4

import pytest
from test_flows import app as azure_app

from aether_agent_memory.recall.basic.assembly import ContextAssembly
from aether_agent_memory.recall.contracts.foundation import MemorySearchRequest, RecallPlanRequest
from aether_agent_memory.runtime.contracts.models import Permission, Principal, Scope, ScopeSelector
from recall_both_sources_support import SOURCES, FixedCandidateRoutes, assert_fusion_plan
from recall_fusion_fixture import FusionFacts

pytestmark = [pytest.mark.integration, pytest.mark.sources, pytest.mark.p0]
app = azure_app


@pytest.fixture(params=["U01", "U06"])
def fusion_baseline(app, request):
    principal = Principal(
        principal_id=request.param,
        home_scope=Scope(tenant_id="t1", application_id="app", user_id="user", agent_id="agent"),
        permissions=(Permission.READ,)
        if request.param == "U01"
        else (Permission.READ, Permission.DIAGNOSE),
        auth_epoch=1,
    )
    app.foundation.identity.provision([(sha256(request.param.encode()).hexdigest(), principal)])
    ctx = app.foundation.identity.context(request.param, timeout_seconds=120)
    facts = FusionFacts(app, ctx)
    app.recall.memories = facts
    assert app.recall.settings.rerank_policy == "disabled"
    search = MemorySearchRequest(
        operation_id="F3-" + uuid4().hex,
        purpose="recall",
        query="固定合法双路候选融合",
        selection=ScopeSelector(),
        model_space=app.model_space,
        memory_source="working",
        memory_top_k=10,
        chunk_page_size=10,
        max_chunk_hits=20,
        max_rounds=2,
        deadline_at=ctx.deadline_at,
    )
    request = RecallPlanRequest(
        recall_id="F3-" + uuid4().hex,
        query=search.query,
        selection=search.selection,
        sources=SOURCES,
        token_budget=4096,
        context_tokenizer=app.recall.tokenizer.identifier,
        policy_version=app.recall.policy_version,
        deadline_at=ctx.deadline_at,
        working_search=search,
        long_term_search=search.model_copy(update={"memory_source": "long_term"}),
    )
    return ctx, facts, request


def test_f3_exact_a_v2_is_packed_once_with_both_contributions(fusion_baseline, app):
    ctx, facts, request = fusion_baseline
    a, b, c = (facts.candidates[key] for key in (("A", 2), ("B", 1), ("C", 1)))
    routes = FixedCandidateRoutes((a, b), (c, a))
    assembly = ContextAssembly(app.recall, routes, facts, facts)
    plan = asyncio.run(assembly.plan(ctx, request))
    assert_fusion_plan(
        plan,
        routes.calls,
        (a.memory, c.memory, b.memory),
        {
            (a.memory, "working"): 1,
            (a.memory, "long_term"): 2,
            (b.memory, "working"): 2,
            (c.memory, "long_term"): 1,
        },
    )
    assert plan.rendered_context.count(facts.snapshots[a.memory].content) == 1
    # Equal bodies remain distinct, with each item's own original source provenance.
    assert facts.snapshots[b.memory].content == facts.snapshots[c.memory].content
    assert plan.rendered_context.count(facts.snapshots[b.memory].content) == 2
    for unit in plan.units:
        for body in unit.bodies:
            assert body == facts.bodies[body.memory]
    assert plan.tokens_used < request.token_budget


def test_different_versions_of_same_id_are_not_merged_as_exact_ref(fusion_baseline, app):
    ctx, facts, request = fusion_baseline
    a2, a1 = facts.candidates["A", 2], facts.candidates["A", 1]
    # Each route contains one legal exact version; cross-route Ref equality differs.
    routes = FixedCandidateRoutes((a2,), (a1,))
    plan = asyncio.run(ContextAssembly(app.recall, routes, facts, facts).plan(ctx, request))
    refs = [b.memory for u in plan.units for b in u.bodies]
    assert set(refs) == {a2.memory, a1.memory} and len(refs) == 2
    assert {(e.memory, e.source) for e in plan.rank_evidence} == {
        (a2.memory, "working"),
        (a1.memory, "long_term"),
    }


@pytest.mark.parametrize("unavailable", SOURCES)
def test_one_successful_route_cannot_produce_complete_both_plan(fusion_baseline, app, unavailable):
    ctx, facts, request = fusion_baseline
    a, b, c = (facts.candidates[key] for key in (("A", 2), ("B", 1), ("C", 1)))
    routes = FixedCandidateRoutes((a, b), (c, a), unavailable=unavailable)
    plan = asyncio.run(ContextAssembly(app.recall, routes, facts, facts).plan(ctx, request))
    assert plan.request.sources == SOURCES
    assert {c["request"].memory_source for c in routes.calls} == set(SOURCES)
    assert sum(c["result"].coverage == "complete" for c in routes.calls) == 1
    assert any(reason.startswith(unavailable + "_") for reason in plan.degradation_reasons)
    assert all(e.source != unavailable for e in plan.rank_evidence)
    with pytest.raises(AssertionError):
        assert_fusion_plan(plan, routes.calls, (a.memory, c.memory, b.memory), {})
