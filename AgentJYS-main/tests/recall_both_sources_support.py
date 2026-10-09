"""AET-36: strict both-source evidence and reusable controlled candidate routes."""

from collections import Counter

from aether_agent_memory.recall.contracts.foundation import MemorySearchResult
from aether_agent_memory.recall.contracts.models import ContextPack
from recall_working_search_support import assert_execution

SOURCES = ("working", "long_term")


def assert_both_pack(payload, memories):
    pack = ContextPack.model_validate(payload)
    assert pack.selected_sources == SOURCES
    assert pack.coverage.working == pack.coverage.long_term == "complete"
    assert pack.outcome == "available" and not pack.degradation_reasons
    items = [item for group in pack.groups for item in group.items]
    assert Counter(item.memory for item in items) == Counter(m.ref for m in memories)
    for item in items:
        memory = next(m for m in memories if m.ref == item.memory)
        assert item.content == memory.content and item.sources == memory.sources
        assert item.representation == "original" and item.content in pack.rendered_context
    return pack


def assert_both_execution(probe, operation_id, query, native, refs_by_source):
    """Complete coverage needs actual successful native/SDK calls for each route."""
    from types import SimpleNamespace

    searches = [r for r in probe.searches if r["operation_id"] == operation_id]
    assert {r["request"].memory_source for r in searches} == set(SOURCES), "missing route"
    evidence = {}
    for source in SOURCES:
        lane = SimpleNamespace(
            embeddings=probe.embeddings,
            searches=[r for r in searches if r["request"].memory_source == source],
        )
        evidence[source] = assert_execution(
            lane, operation_id, query, native, source, refs_by_source[source]
        )
    assert len({r["trace_id"] for r in evidence.values()}) == 1
    return evidence


class FixedCandidateRoutes:
    """MemoryCandidatePort baseline; controls qualified candidates, never ANN ranking.

    Callers supply legal candidate proofs and their B authority separately. Every
    invocation records the actual request and result, including unavailable routes.
    """

    def __init__(self, working, long_term, *, unavailable=None):
        self.routes = {"working": tuple(working), "long_term": tuple(long_term)}
        self.unavailable = unavailable
        self.calls = []

    async def search(self, ctx, request):
        source = request.memory_source
        assert source in SOURCES
        route = self.routes[source]
        assert route
        scope = route[0].memory.scope
        assert all(c.memory.scope == scope for c in route), "same-scope baseline required"
        assert scope.tenant_id == ctx.principal.home_scope.tenant_id
        for key, value in request.selection.model_dump(exclude_none=True).items():
            assert getattr(scope, key) == value
        candidates = []
        if source != self.unavailable:
            for rank, candidate in enumerate(route, 1):
                score = 0.9 if rank == 1 else 0.8
                data = candidate.model_dump()
                data.update(rank=rank, best_score=score)
                data["hits"] = [
                    {**h, "memory_source": source, "score": score} for h in data["hits"]
                ]
                candidates.append(type(candidate).model_validate(data))
        result = MemorySearchResult(
            request=request,
            scope=scope,
            candidates=tuple(candidates),
            examined_chunk_hits=sum(len(c.hits) for c in candidates),
            rounds_used=1 if candidates else 0,
            stop_reason="dependency" if source == self.unavailable else "exhausted",
            coverage="unavailable" if source == self.unavailable else "complete",
        )
        self.calls.append({"operation_id": ctx.operation_id, "request": request, "result": result})
        return result


def assert_fusion_plan(plan, calls, expected_order, expected_ranks):
    """Independent literal Ref/rank expectations; no deriving truth from the plan."""
    assert plan.request.sources == SOURCES
    assert plan.request.working_search is not None and plan.request.long_term_search is not None
    assert not plan.degradation_reasons and not plan.skipped_group_ids
    assert {c["request"].memory_source for c in calls} == set(SOURCES), "missing route"
    for source in SOURCES:
        executions = [c for c in calls if c["request"].memory_source == source]
        assert executions and all(c["result"].coverage == "complete" for c in executions)
        assert all(c["request"] == getattr(plan.request, source + "_search") for c in executions)
    refs = [body.memory for unit in plan.units for body in unit.bodies]
    assert refs == list(expected_order), "exact Ref order/dedup differs"
    assert len(refs) == len(set(refs))
    evidence = {(e.memory, e.source): e for e in plan.rank_evidence}
    assert len(evidence) == len(plan.rank_evidence)
    assert set(evidence) == set(expected_ranks), "source contribution lost or invented"
    for key, rank in expected_ranks.items():
        entry = evidence[key]
        assert entry.source_rank == rank and entry.rrf_k == 60
        expected = {1: 0.01639344262295082, 2: 0.016129032258064516}[rank]
        assert abs(entry.rrf_contribution - expected) <= 1e-12
