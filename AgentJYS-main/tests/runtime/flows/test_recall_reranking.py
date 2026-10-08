"""AET-18 RC-RER-01–05/08 at real planning and public diagnostic boundaries.

Azure/Temporal fixtures and B guards remain real/strict existing fixtures.
Only the external reranking provider is controlled. Score-only responses cannot
prove that a dishonest provider secretly permuted positions; pairing is tested
at the adapter's external model boundary in tests/recall/test_reranking.py.
"""

import asyncio
from threading import Event
from time import monotonic

import pytest
from test_flows import app as azure_app
from test_flows import context, drain, save
from test_generation_assembly import assembly_setup

from aether_agent_memory.recall.basic.components import RankedMemory
from aether_agent_memory.recall.basic.config import RecallSettings
from aether_agent_memory.recall.basic.reranking import CrossEncoderReranker
from aether_agent_memory.recall.contracts.models import RecallRequest
from aether_agent_memory.remember.contracts.models import ConflictGroup
from aether_agent_memory.runtime.contracts.models import ErrorCode
from aether_agent_memory.runtime.foundation.common import FoundationError

app = azure_app
pytestmark = pytest.mark.integration


class Scorer:
    identifier = "fixture:position_scores"

    def __init__(self, scores=(0.5, 0.1, 0.9), error=None, slow=False):
        self.scores, self.error, self.slow = scores, error, slow
        self.seen = []

    async def rerank(self, ctx, query, documents):
        self.seen.append((query, documents))
        if self.slow:
            await asyncio.Event().wait()
        if self.error is not None:
            raise FoundationError(self.error, "controlled model failure")
        return self.scores


def configure(app, policy, scorer):
    app.recall.settings = RecallSettings(
        rerank_policy=policy,
        reranker_model=None if policy == "disabled" else "fixture",
        rerank_timeout_seconds=0.02,
    )
    app.recall.reranker = scorer


def three_candidates(app):
    ctx, assembly, body, request = assembly_setup(app, token_budget=4096)
    request = request.model_copy(
        update={"long_term_search": request.long_term_search.model_copy(update={"memory_top_k": 3})}
    )
    return ctx, assembly, body, request


@pytest.mark.p1
def test_rc_rer_01_disabled_never_calls_model_and_records_disabled(app):
    ctx, assembly, body, request = three_candidates(app)
    scorer = Scorer(error=ErrorCode.DEPENDENCY_UNAVAILABLE)
    configure(app, "disabled", scorer)
    plan = asyncio.run(assembly.plan(ctx, request))
    assert [u.bodies[0].memory.memory_id for u in plan.units] == ["m1", "m2", "m3"]
    assert scorer.seen == [] and plan.degradation_reasons == ()
    # Admit through the real Recall boundary; read stages through Diagnostics,
    # rather than interrogating a persistence table from the test.
    with app.foundation.uow.transaction() as tx:
        record, _ = app.recall.accept_in(tx, ctx, RecallRequest(query=request.query))
    ranked = [
        RankedMemory(body.snapshots[name], score)
        for name, score in (("m1", 0.03), ("m3", 0.02), ("m2", 0.01))
    ]
    returned, reason = asyncio.run(app.recall.rerank(ctx, request.query, record.recall_id, ranked))
    assert returned == ranked and reason is None and scorer.seen == []
    trace = app.foundation.diagnostics.trace(ctx, ctx.trace_id)
    stages = trace["durable_facts"]["recall_stages"]
    rerank = next(s for s in stages if s["stage"] == "rerank")
    assert rerank["details"]["state"] == "disabled"
    assert app.recall.status(ctx, record.recall_id).stage == "rerank"


@pytest.mark.p1
def test_rc_rer_02_scores_stay_bound_to_input_bodies_and_refs(app):
    ctx, assembly, body, request = three_candidates(app)
    scorer = Scorer()
    configure(app, "required", scorer)
    plan = asyncio.run(assembly.plan(ctx, request))
    assert scorer.seen == [
        (request.query, tuple(body.snapshots[n].content for n in ("m1", "m2", "m3")))
    ]
    assert [u.bodies[0].memory.memory_id for u in plan.units] == ["m3", "m1", "m2"]
    assert [u.rank for u in plan.units] == [1, 2, 3]
    assert plan.degradation_reasons == ()


@pytest.mark.p0
@pytest.mark.parametrize("policy", ["required", "fallback"])
@pytest.mark.parametrize("fault", ["unavailable", "deadline", "timeout"])
def test_rc_rer_03_04_only_fallback_recovers_model_dependency_or_timeout(app, policy, fault):
    ctx, assembly, _, request = three_candidates(app)
    code = (
        ErrorCode.DEPENDENCY_UNAVAILABLE if fault == "unavailable" else ErrorCode.DEADLINE_EXCEEDED
    )
    scorer = Scorer(error=None if fault == "timeout" else code, slow=fault == "timeout")
    configure(app, policy, scorer)
    if policy == "required":
        with pytest.raises(FoundationError) as error:
            asyncio.run(assembly.plan(ctx, request))
        assert error.value.code == code
    else:
        plan = asyncio.run(assembly.plan(ctx, request))
        assert [u.bodies[0].memory.memory_id for u in plan.units] == ["m1", "m2", "m3"]
        assert plan.degradation_reasons == ("rerank_" + code.value,)
    assert len(scorer.seen) == 1


@pytest.mark.p0
@pytest.mark.parametrize(
    "scores",
    [
        (float("nan"), 0.1, 0.9),
        (float("inf"), 0.1, 0.9),
        (float("-inf"), 0.1, 0.9),
        (0.2, 0.3),
        (0.1, 0.2, 0.3, 0.4),
    ],
)
def test_rc_rer_05_invalid_output_cannot_become_fallback_plan(app, scores):
    ctx, assembly, _, request = three_candidates(app)
    configure(app, "fallback", Scorer(scores=scores))
    with pytest.raises(FoundationError) as error:
        asyncio.run(assembly.plan(ctx, request))
    assert error.value.code == ErrorCode.CONTRACT_VIOLATION


@pytest.mark.p0
@pytest.mark.parametrize(
    "code",
    [
        ErrorCode.INVALID_ARGUMENT,
        ErrorCode.FORBIDDEN,
        ErrorCode.RESULT_INVALIDATED,
        ErrorCode.CONTRACT_VIOLATION,
    ],
)
def test_rc_rer_04_05_nonrecoverable_failures_never_fallback(app, code):
    ctx, assembly, _, request = three_candidates(app)
    configure(app, "fallback", Scorer(error=code))
    with pytest.raises(FoundationError) as error:
        asyncio.run(assembly.plan(ctx, request))
    assert error.value.code == code


@pytest.mark.p0
@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize("unreadable", [False, True])
def test_rc_rer_08_model_receives_only_guarded_whole_relationship_units(app, reverse, unreadable):
    ctx, assembly, body, request = assembly_setup(app, token_budget=4096)
    members = (body.snapshots["m1"].ref, body.snapshots["m3"].ref)
    body.conflicts = [
        ConflictGroup(
            group_id="conflict",
            members=tuple(reversed(members)) if reverse else members,
            explanation="both facts required",
        )
    ]
    if unreadable:
        body.deleted.add("m3")

    class GuardedScorer(Scorer):
        async def rerank(self, ctx, query, documents):
            assert body.guarded, "current B guards must precede plaintext inference"
            expected = body.guarded[-1].expected
            submitted = {
                snapshot.ref.model_dump_json()
                for snapshot in body.snapshots.values()
                if any(snapshot.content in document for document in documents)
            }
            assert {guard.memory.model_dump_json() for guard in expected} == submitted
            return await super().rerank(ctx, query, documents)

    scorer = GuardedScorer(scores=(0.9,) if unreadable else (0.9, 0.1))
    configure(app, "required", scorer)
    plan = asyncio.run(assembly.plan(ctx, request))
    assert len(scorer.seen) == 1
    documents = scorer.seen[0][1]
    if unreadable:
        assert documents == (body.snapshots["m2"].content,)
        assert [b.memory.memory_id for u in plan.units for b in u.bodies] == ["m2"]
        assert "conflict" in plan.skipped_group_ids
    else:
        ordered = tuple(reversed(members)) if reverse else members
        assert documents == (
            "\n".join(body.snapshots[r.memory_id].content for r in ordered),
            body.snapshots["m2"].content,
        )
        group = next(u for u in plan.units if u.conflict)
        assert {b.memory.memory_id for b in group.bodies} == {"m1", "m3"}
        assert [r.memory_id for r in group.primary_memories] == ["m1"]
        assert len(plan.units) == 2 and plan.units[0].group_id == "conflict"


@pytest.mark.p0
def test_rc_rer_08_related_member_revoked_at_current_guard_never_reaches_model(app, monkeypatch):
    ctx, assembly, body, request = assembly_setup(app, token_budget=4096)
    body.conflicts = [
        ConflictGroup(
            group_id="conflict",
            members=(body.snapshots["m1"].ref, body.snapshots["m3"].ref),
            explanation="both required",
        )
    ]
    scorer = Scorer(scores=(0.9, 0.1))
    configure(app, "required", scorer)
    revalidate = body.revalidate_context

    def revoked_guard(tx, ctx, request):
        body.deleted.add("m3")
        return revalidate(tx, ctx, request)

    monkeypatch.setattr(body, "revalidate_context", revoked_guard)
    with pytest.raises(FoundationError) as error:
        asyncio.run(assembly.plan(ctx, request))
    assert error.value.code == ErrorCode.RESULT_INVALIDATED
    assert scorer.seen == []


@pytest.mark.p1
def test_rc_rer_07_late_kernel_cannot_overwrite_committed_fallback_pack(app):
    save(app, "我喜欢无糖咖啡")
    drain(app)
    entered, release = Event(), Event()

    class SlowModel:
        def tokenizer(self, pairs, *, truncation, padding):
            assert truncation is False and padding is False
            return {"input_ids": [[1, 2, 3] for _ in pairs]}

        def predict(self, pairs, *, batch_size, show_progress_bar):
            entered.set()
            assert release.wait(30), "late kernel was never released"
            return [0.99] * len(pairs)

    provider = CrossEncoderReranker("fixture")
    provider.model = SlowModel()
    configure(app, "fallback", provider)
    ctx = context(app)

    async def run():
        pending = asyncio.create_task(
            app.recall.recall(
                ctx, RecallRequest(query="咖啡", sources="long_term", token_budget=4096)
            )
        )
        assert await asyncio.to_thread(entered.wait, 10)
        pack = await asyncio.wait_for(pending, timeout=10)
        assert pack.outcome == "degraded"
        assert "rerank_DEADLINE_EXCEEDED" in pack.degradation_reasons
        status = app.recall.status(ctx, pack.recall_id)
        assert status.state == "completed" and status.result_available
        release.set()
        end = monotonic() + 5
        while True:
            try:
                await provider.rerank(ctx, "probe", ("capacity reclaimed",))
                break
            except FoundationError as error:
                assert error.code == ErrorCode.DEPENDENCY_UNAVAILABLE
                assert monotonic() < end, "late kernel did not release its slot"
                await asyncio.sleep(0.01)
        assert app.recall.status(ctx, pack.recall_id) == status
        assert app.recall.result(ctx, pack.recall_id) == pack

    try:
        asyncio.run(run())
    finally:
        release.set()
        provider.close()
