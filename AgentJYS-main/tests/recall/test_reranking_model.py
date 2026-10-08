"""RC-RER-02 real CrossEncoder comparison, enabled by a predownloaded model.

Set P3_TEST_RERANK_MODEL_DIR to an external local BAAI/bge-reranker-base
directory at the revision pinned in configs/recall.rerank.local.json. Install
the recall-rerank extra first. With a configured path, missing/broken assets
or dependencies fail rather than falling back to a model double.
"""

import asyncio
import os
from pathlib import Path

import pytest

from aether_agent_memory.recall.basic.reranking import CrossEncoderReranker
from aether_agent_memory.runtime.contracts.models import ErrorCode
from aether_agent_memory.runtime.foundation.common import FoundationError


@pytest.mark.integration
@pytest.mark.p1
def test_rc_rer_02_real_cross_encoder_pair_scores_match_direct_inference():
    path = os.environ.get("P3_TEST_RERANK_MODEL_DIR")
    if not path:
        pytest.skip("set P3_TEST_RERANK_MODEL_DIR to enable the real-model comparison")
    directory = Path(path).resolve()
    assert directory.is_dir(), "the configured real model directory does not exist"
    assert not directory.is_relative_to(Path(__file__).resolve().parents[2])
    from sentence_transformers import CrossEncoder

    query = "用户喝咖啡时是否加糖？"
    documents = ("用户喝咖啡不加糖。", "用户喜欢乌龙茶。", "如果喝咖啡治病只是未经证实的假设。")
    model = CrossEncoder(str(directory), device="cpu", max_length=512, trust_remote_code=False)
    expected = tuple(
        float(s)
        for s in model.predict(
            [(query, document) for document in documents], batch_size=8, show_progress_bar=False
        )
    )
    provider = CrossEncoderReranker(str(directory), max_length=512)
    try:
        actual = asyncio.run(provider.rerank(None, query, documents))
        assert actual == pytest.approx(expected, rel=1e-6, abs=1e-6)
        assert len(actual) == len(documents)
        assert (await_health := asyncio.run(provider.health(None)))["loaded"] is True
        assert await_health["model_id"] == provider.identifier
        # Measure pair wrapping with the real tokenizer. Find exact boundaries
        # rather than assuming how this model splits words or inserts tokens.
        boundary_documents = {}
        for words in range(1, 514):
            document = "test " * words
            encoded = model.tokenizer([(query, document)], truncation=False, padding=False)
            length = len(encoded["input_ids"][0])
            if length in {511, 512, 513}:
                boundary_documents[length] = document
            if len(boundary_documents) == 3:
                break
        assert set(boundary_documents) == {511, 512, 513}, "real pair boundaries not constructed"
        for length in (511, 512):
            document = boundary_documents[length]
            direct = model.predict([(query, document)], show_progress_bar=False)
            scores = asyncio.run(provider.rerank(None, query, (document,)))
            assert scores == pytest.approx((float(direct[0]),), rel=1e-6, abs=1e-6)
        with pytest.raises(FoundationError) as oversized:
            asyncio.run(provider.rerank(None, query, (boundary_documents[513],)))
        assert oversized.value.code == ErrorCode.INVALID_ARGUMENT
    finally:
        provider.close()
