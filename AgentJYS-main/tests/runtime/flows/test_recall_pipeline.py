"""Behavior at pipeline boundaries; providers are deterministic fault-injection doubles."""

import asyncio
from threading import Event

import pytest
from test_flows import app as app
from test_flows import context, drain, facts, recall, save

from aether_agent_memory.recall.basic.components import fuse
from aether_agent_memory.recall.basic.config import RecallSettings
from aether_agent_memory.recall.basic.reranking import CrossEncoderReranker
from aether_agent_memory.recall.basic.tokenization import ModelTokenizer
from aether_agent_memory.recall.contracts.models import (
    RecallRequest,
    VectorCandidate,
    VectorSearchResult,
)
from aether_agent_memory.remember.basic.projection import projection_target
from aether_agent_memory.remember.contracts.models import CorrectionRequest, DeleteRequest
from aether_agent_memory.runtime.contracts.models import ScopeSelector
from aether_agent_memory.runtime.foundation.common import FoundationError


class Scores:
    identifier = "test_scores"

    def __init__(self, score=None, delay=0, hook=None):
        self.score, self.delay, self.hook = score, delay, hook
        self.seen = []

    async def rerank(self, ctx, query, documents):
        self.seen.extend(documents)
        if self.hook:
            self.hook()
        await asyncio.sleep(self.delay)
        return tuple(self.score if self.score is not None else i for i in range(len(documents)))


def configure(app, provider, policy="required", timeout=5):
    app.recall.settings = RecallSettings(
        reranker_model="test", rerank_policy=policy, rerank_timeout_seconds=timeout
    )
    app.recall.reranker = provider


def test_fusion_duplicates_do_not_inflate_or_shift_ranks(app):
    first = save(app, "我喜欢无糖拿铁")
    second = save(app, "我喜欢无糖红茶")
    drain(app)
    a = app.remember.get(context(app), facts(app, first)[0].memory_id)
    b = app.remember.get(context(app), facts(app, second)[0].memory_id)
    fused = fuse([[a, a, b], [b]])
    assert fused[0].memory.ref == b.ref
    assert fused[0].score == pytest.approx(1 / 62 + 1 / 61)
    assert fused[1].score == pytest.approx(1 / 61)


def test_real_token_count_and_special_token_text():
    tokenizer = ModelTokenizer("o200k_base")
    text = "[1] 用户喜欢拿铁，不加糖。<|endoftext|>\n"
    assert tokenizer.count(text) > 0
    assert tokenizer.count(text) < len(text.encode())
    assert tokenizer.count("") == 0


def test_reranker_receives_only_current_authorized_candidates(app):
    save(app, "我喜欢咖啡拿铁")
    save(app, "我喜欢咖啡美式")
    save(app, "另一个用户喜欢咖啡", user="bob")
    drain(app)
    scorer = Scores()
    configure(app, scorer)
    pack = recall(app, "咖啡")
    assert len(scorer.seen) == 2
    assert all("另一个用户" not in text for text in scorer.seen)
    assert pack.groups[0].items[0].content == scorer.seen[-1]
    with app.foundation.uow.transaction() as tx:
        stages = [row for _, row in tx.rows("recall_stages") if row["recall_id"] == pack.recall_id]
    assert next(s for s in stages if s["stage"] == "rerank")["details"]["state"] == "succeeded"


@pytest.mark.parametrize("policy", ["required", "fallback"])
def test_reranker_timeout_policy_is_honest(app, policy):
    save(app)
    drain(app)
    configure(app, Scores(delay=0.1), policy, timeout=0.01)
    if policy == "required":
        with pytest.raises(FoundationError) as failure:
            recall(app)
        assert failure.value.code == "DEADLINE_EXCEEDED"
    else:
        pack = recall(app)
        assert pack.outcome == "degraded"
        assert "rerank:DEADLINE_EXCEEDED" in pack.degradation_reasons


def test_invalid_scores_cannot_silently_fallback(app):
    save(app)
    drain(app)
    configure(app, Scores(score=float("nan")), "fallback")
    with pytest.raises(FoundationError) as failure:
        recall(app)
    assert failure.value.code == "CONTRACT_VIOLATION"


def test_delete_during_rerank_blocks_context_commit(app):
    receipt = save(app)
    drain(app)
    memory = app.remember.get(context(app), facts(app, receipt)[0].memory_id)

    def delete():
        app.remember.delete(
            context(app),
            memory.ref.memory_id,
            DeleteRequest(expected_revision=memory.object_revision, reason="delete"),
        )

    configure(app, Scores(hook=delete))
    with pytest.raises(FoundationError) as failure:
        recall(app)
    assert failure.value.code == "RESULT_INVALIDATED"


def test_source_timeout_preserves_working_and_records_reason(app, monkeypatch):
    save(app)
    drain(app)
    app.recall.sources.settings = RecallSettings(source_timeout_seconds=0.01)

    original = app.vectors.search

    async def slow(ctx, request):
        if request.memory_source == "long_term":
            await asyncio.sleep(1)
        return await original(ctx, request)

    monkeypatch.setattr(app.vectors, "search", slow)
    pack = recall(app, sources="both", selection=ScopeSelector(session_id="session_1"))
    assert pack.outcome == "degraded"
    assert "long_term:source_timeout" in pack.degradation_reasons
    with pytest.raises(FoundationError) as failure:
        recall(app)
    assert failure.value.code == "DEPENDENCY_UNAVAILABLE"


def test_cancelled_request_ends_persisted_execution(app, monkeypatch):
    entered = asyncio.Event()

    async def slow(*args):
        entered.set()
        await asyncio.sleep(10)

    monkeypatch.setattr(app.vectors, "search", slow)

    async def run():
        ctx = context(app, operation="cancel")
        pending = asyncio.create_task(
            app.recall.recall(
                ctx, RecallRequest(query="咖啡", selection=ScopeSelector(), sources="long_term")
            )
        )
        await entered.wait()
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending

    asyncio.run(run())
    with app.foundation.uow.transaction() as tx:
        rows = [r for _, r in tx.rows("recall_requests")]
    assert rows[-1]["record"]["state"] == "failed"
    assert rows[-1]["record"]["reason"] == "EXECUTION_INTERRUPTED"


def test_timeout_does_not_release_cross_encoder_cpu_slot(monkeypatch):
    provider = CrossEncoderReranker("unused-test-model")
    entered, release = Event(), Event()

    def compute(query, documents):
        entered.set()
        release.wait(5)
        return (1.0,)

    monkeypatch.setattr(provider, "compute", compute)

    async def run():
        pending = asyncio.create_task(provider.rerank(None, "q", ("d",)))
        await asyncio.to_thread(entered.wait, 2)
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending
        with pytest.raises(FoundationError) as failure:
            await provider.rerank(None, "q", ("d",))
        assert failure.value.code == "DEPENDENCY_UNAVAILABLE"

    try:
        asyncio.run(run())
    finally:
        release.set()
        provider.close()


def test_stale_candidates_trigger_bounded_refill(app, monkeypatch):
    from test_flows import source

    receipt = save(app)
    drain(app)
    old = app.remember.get(context(app), facts(app, receipt)[0].memory_id)
    app.remember.correct(
        context(app),
        old.ref.memory_id,
        CorrectionRequest(
            expected_version=1,
            content="我现在喜欢无糖红茶",
            source=source("change"),
            reason="change",
        ),
    )
    drain(app)
    new = app.remember.get(context(app), old.ref.memory_id)
    seen = []

    async def search(ctx, req):
        seen.append(req.limit)
        old_target = projection_target(old.ref, old.content_hash, app.model_space)
        items = [VectorCandidate(target=old_target, rank=i + 1, score=1.0) for i in range(20)]
        if req.limit > 20:
            items.append(
                VectorCandidate(
                    target=projection_target(new.ref, new.content_hash, app.model_space),
                    rank=21,
                    score=0.8,
                )
            )
        return VectorSearchResult(candidates=tuple(items), coverage="complete")

    monkeypatch.setattr(app.vectors, "search", search)
    pack = recall(app, "红茶")
    assert seen == [20, 40]
    assert pack.groups[0].items[0].memory.version == 2


def test_revoke_during_rerank_fails_closed(app):
    save(app)
    drain(app)

    def revoke():
        with app.foundation.uow.transaction() as tx:
            row = tx.read("identities", "alice")
            tx.write("identities", "alice", {**row, "enabled": False})

    configure(app, Scores(hook=revoke))
    with pytest.raises(FoundationError) as failure:
        recall(app)
    assert failure.value.code == "FORBIDDEN"
    with app.foundation.uow.transaction() as tx:
        assert all(r["pack"] is None for _, r in tx.rows("recall_requests"))


def test_vector_partial_reason_survives_discovery_budget_limit(app, monkeypatch):
    receipt = save(app)
    drain(app)
    item = app.remember.get(context(app), facts(app, receipt)[0].memory_id)
    stale = item.ref.model_copy(update={"version": item.ref.version + 1})
    target = projection_target(stale, item.content_hash, app.model_space)
    app.recall.sources.settings = RecallSettings(candidate_limit=20, max_discovery=20)

    async def partial(ctx, request):
        return VectorSearchResult(
            candidates=tuple(
                VectorCandidate(target=target, rank=i + 1, score=1.0) for i in range(20)
            ),
            coverage="partial",
            reason="backend_partial",
        )

    monkeypatch.setattr(app.vectors, "search", partial)
    with pytest.raises(FoundationError) as error:
        recall(app)
    assert error.value.code == "DEPENDENCY_UNAVAILABLE"
    assert "backend_partial" in str(error.value)
    assert "discovery_budget_exhausted" in str(error.value)
