"""AET-18: adapter contracts with a strict external CrossEncoder model fixture.

These tests verify the adapter, not model quality. PairTokenizer counts three
special tokens plus whitespace tokens; it is deliberately independent of the
ContextPack tokenizer. The optional real-model comparison lives separately.
"""

import asyncio
from threading import Event
from time import monotonic

import pytest

from aether_agent_memory.recall.basic.reranking import CrossEncoderReranker
from aether_agent_memory.runtime.contracts.models import ErrorCode
from aether_agent_memory.runtime.foundation.common import FoundationError


class PairTokenizer:
    def __init__(self):
        self.seen = []

    def __call__(self, pairs, *, truncation, padding):
        assert truncation is False and padding is False
        self.seen.append(pairs)
        return {"input_ids": [list(range(3 + len(q.split()) + len(d.split()))) for q, d in pairs]}


class Model:
    """Only the external library's tokenizer/predict boundary is replaced."""

    def __init__(self, scores=(0.2, 0.9)):
        self.tokenizer = PairTokenizer()
        self.scores = scores
        self.seen = []

    def predict(self, pairs, *, batch_size, show_progress_bar):
        self.seen.append(pairs)
        return self.scores


@pytest.fixture
def provider():
    reranker = CrossEncoderReranker("fixture", max_length=8)
    reranker.model = Model()
    try:
        yield reranker
    finally:
        reranker.close()


@pytest.mark.p0
@pytest.mark.parametrize("words,allowed", [(3, True), (4, True), (5, False)])
def test_rc_rer_06_pair_limit_includes_query_and_special_tokens_without_truncation(
    provider, words, allowed
):
    # Query=1 word + document=3/4/5 words + wrapper=3 -> L-1/L/L+1 for L=8.
    query, document = "query", " ".join(["body"] * words)
    provider.model.scores = (0.75,)
    if allowed:
        assert asyncio.run(provider.rerank(None, query, (document,))) == (0.75,)
        assert provider.model.seen == [[(query, document)]]
    else:
        with pytest.raises(FoundationError) as error:
            asyncio.run(provider.rerank(None, query, (document,)))
        assert error.value.code == ErrorCode.INVALID_ARGUMENT
        assert provider.model.seen == []
    assert provider.model.tokenizer.seen == [[(query, document)]]


@pytest.mark.p1
def test_rc_rer_02_adapter_returns_scores_in_exact_pair_input_order(provider):
    assert asyncio.run(provider.rerank(None, "q", ("first", "second"))) == (0.2, 0.9)
    assert provider.model.seen == [[("q", "first"), ("q", "second")]]


@pytest.mark.p0
@pytest.mark.parametrize(
    "scores",
    [
        (float("nan"), 0.2),
        (float("inf"), 0.2),
        (float("-inf"), 0.2),
        (0.2,),
        (0.2, 0.3, 0.4),
        ("not-a-score", 0.2),
        None,
    ],
)
def test_rc_rer_05_adapter_rejects_nonfinite_or_mismatched_scores(provider, scores):
    provider.model.scores = scores
    with pytest.raises(FoundationError) as error:
        asyncio.run(provider.rerank(None, "q", ("first", "second")))
    assert error.value.code == ErrorCode.CONTRACT_VIOLATION


@pytest.mark.p1
def test_rc_rer_07_timed_out_kernel_keeps_capacity_then_recovers(provider):
    entered, release = Event(), Event()

    class SlowModel(Model):
        def predict(self, pairs, *, batch_size, show_progress_bar):
            self.seen.append(pairs)
            if len(self.seen) == 1:
                entered.set()
                assert release.wait(10), "test failed to release the late CPU kernel"
                return (0.1,)
            return (0.9,)

    provider.model = SlowModel()

    async def run():
        pending = asyncio.create_task(provider.rerank(None, "q", ("old",)))
        assert await asyncio.to_thread(entered.wait, 5)
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(pending, timeout=0.01)
        assert pending.cancelled()
        for _ in range(5):
            with pytest.raises(FoundationError) as error:
                await provider.rerank(None, "q", ("new",))
            assert error.value.code == ErrorCode.DEPENDENCY_UNAVAILABLE
        assert len(provider.model.seen) == 1  # N=1: no queued or overlapping kernels.
        release.set()
        end = monotonic() + 5
        while True:
            try:
                scores = await provider.rerank(None, "q", ("new",))
                break
            except FoundationError as error:
                assert error.code == ErrorCode.DEPENDENCY_UNAVAILABLE
                assert monotonic() < end, "physical inference slot was never reclaimed"
                await asyncio.sleep(0.01)
        assert scores == (0.9,) and pending.cancelled()
        assert (await provider.health(None))["model_id"] == "cross_encoder:fixture@default"
        provider.close()
        assert (await provider.health(None))["state"] == "unavailable"
        with pytest.raises(FoundationError) as closed:
            await provider.rerank(None, "q", ("after_close",))
        assert closed.value.code == ErrorCode.DEPENDENCY_UNAVAILABLE

    try:
        asyncio.run(run())
    finally:
        release.set()
