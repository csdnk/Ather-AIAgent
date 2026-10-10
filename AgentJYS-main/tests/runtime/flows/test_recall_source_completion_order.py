"""AET-43 F3 exact fusion under both barrier-controlled completion orders."""

import asyncio
from pathlib import Path
from tempfile import gettempdir
from uuid import uuid4

import pytest
from test_recall_both_fusion import app as _app
from test_recall_both_fusion import fusion_baseline as _fusion_baseline

from aether_agent_memory.recall.basic.assembly import ContextAssembly
from recall_authorization_support import SafeEvidence
from recall_both_sources_support import SOURCES, assert_fusion_plan
from recall_source_timing_support import OrderedRoutes

app = _app
fusion_baseline = _fusion_baseline

pytestmark = [pytest.mark.integration, pytest.mark.sources, pytest.mark.p1]


def test_rc_src_14_f3_completion_order_does_not_change_fusion(fusion_baseline, app, request):
    evidence = SafeEvidence("AET-43", request.node.name)
    directory = Path(gettempdir()) / "aether-workspace-support" / "AET-43" / uuid4().hex
    request.addfinalizer(lambda: evidence.write(directory))
    evidence.data.update(acceptance="failed", executions=[])
    ctx, facts, request = fusion_baseline
    a, b, c = (facts.candidates[key] for key in (("A", 2), ("B", 1), ("C", 1)))
    assert app.recall.settings.rerank_policy == "disabled"
    expected_ranks = {
        (a.memory, "working"): 1,
        (a.memory, "long_term"): 2,
        (b.memory, "working"): 2,
        (c.memory, "long_term"): 1,
    }

    async def run(first):
        routes = OrderedRoutes((a, b), (c, a))
        evidence.data["executions"].append(
            {"first": first, "timeline": routes.timeline, "deadline": request.deadline_at}
        )
        assembly = ContextAssembly(app.recall, routes, facts, facts)
        task = asyncio.create_task(assembly.plan(ctx, request))
        try:
            # Both routes must be in flight before either is released. A serial
            # implementation fails here instead of silently pretending L finished first.
            await asyncio.wait_for(asyncio.gather(*(e.wait() for e in routes.entered.values())), 2)
            routes.release[first].set()
            await asyncio.wait_for(routes.completed[first].wait(), 2)
            other = next(s for s in SOURCES if s != first)
            assert not routes.completed[other].is_set()
            routes.release[other].set()
            plan = await asyncio.wait_for(task, 2)
            assert [r["source"] for r in routes.timeline if r["stage"] == "completed"] == [
                first,
                other,
            ]
            assert_fusion_plan(plan, routes.calls, (a.memory, c.memory, b.memory), expected_ranks)
            return plan
        finally:
            for event in routes.release.values():
                event.set()
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    working_first = asyncio.run(run("working"))
    long_term_first = asyncio.run(run("long_term"))
    # Same plan identity, inputs, policy, budget and no reranker; compare all
    # persisted plan fields including coverage, exact order and RRF contributions.
    assert working_first.model_dump() == long_term_first.model_dump()
    evidence.data["acceptance"] = "passed"
