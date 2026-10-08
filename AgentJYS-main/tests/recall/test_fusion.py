"""AET-17: pure fusion contracts, independent of backend availability.

F3's worked scores are checked with absolute tolerance 1e-12 (no relative
tolerance). Qualification and persisted planning are covered at their seams
in runtime/flows/test_recall_fusion.py.
"""

import json
from pathlib import Path

import pytest

from aether_agent_memory.recall.basic.components import assemble, fuse
from aether_agent_memory.recall.basic.tokenization import ModelTokenizer
from aether_agent_memory.remember.contracts.models import MemorySnapshot
from aether_agent_memory.runtime.foundation.requests import text_hash


def memory(name, version=1, content=None, tenant="t1"):
    cases = json.loads(
        (Path(__file__).resolve().parents[2] / "contracts/p3/fixtures/cases.json").read_text(
            encoding="utf-8"
        )
    )
    payload = next(
        c["payload"] for c in cases if c["model"] == "remember.MemorySnapshot" and c["valid"]
    )
    payload["ref"].update(memory_id=name, version=version)
    payload["ref"]["scope"]["tenant_id"] = tenant
    payload["content"] = content or f"Original body {name}"
    payload["content_hash"] = text_hash(payload["content"])
    payload["sources"][0]["source_id"] = f"source_{name}"
    return MemorySnapshot.model_validate(payload)


@pytest.fixture
def f3():
    return memory("A", version=2), memory("B"), memory("C")


@pytest.mark.p0
def test_rc_fus_01_one_based_rank_and_fixed_rrf_constant(f3):
    a, b, c = f3
    ranked = fuse([[a, b], [c, a]])
    assert [r.memory.ref.memory_id for r in ranked] == ["A", "C", "B"]
    # Independently worked F3: A=123/3782, C=1/61, B=1/62.
    assert [r.score for r in ranked] == pytest.approx(
        [0.03252247488101534, 0.01639344262295082, 0.016129032258064516],
        rel=0,
        abs=1e-12,
    )


@pytest.mark.p0
@pytest.mark.parametrize("working", [("A", "A", "B"), ("A", "B", "A", "B")])
def test_rc_fus_02_same_route_duplicates_neither_add_score_nor_consume_rank(f3, working):
    a, b, c = f3
    memories = {"A": a, "B": b}
    baseline = fuse([[a, b], [c, a]])
    repeated = fuse([[memories[name] for name in working], [c, c, a, a]])
    assert repeated == baseline


@pytest.mark.p0
@pytest.mark.parametrize("other", [memory("A", version=1), memory("A", tenant="t2")])
def test_rc_fus_03_merge_key_includes_exact_scope_id_and_version(f3, other):
    a, _, _ = f3
    ranked = fuse([[a], [other, a]])
    assert len(ranked) == 2
    assert ranked[0].memory.ref == a.ref
    assert ranked[0].score == pytest.approx(0.03252247488101534, rel=0, abs=1e-12)
    assert ranked[1].memory.ref == other.ref
    assert ranked[1].score == pytest.approx(0.01639344262295082, rel=0, abs=1e-12)


@pytest.mark.p1
def test_rc_fus_06_identical_bodies_keep_distinct_memory_and_source_refs():
    a, b = memory("A", content="相同正文"), memory("B", content="相同正文")
    assert a.content_hash == b.content_hash
    ranked = fuse([[a, b], [b, a]])
    groups, rendered = assemble(ranked, "same_body", 1024, ModelTokenizer("o200k_base"), 10)
    items = [item for group in groups for item in group.items]
    assert {item.memory.memory_id for item in items} == {"A", "B"}
    assert {item.sources[0].source_id for item in items} == {"source_A", "source_B"}
    assert all(item.content == "相同正文" for item in items)
    assert rendered.count("相同正文") == 2


@pytest.mark.p1
def test_rc_fus_07_route_order_and_repeated_hits_preserve_scores_and_ties(f3):
    a, b, c = f3
    baseline = fuse([[a, b], [c, a]])
    assert fuse([[c, c, a, a], [a, a, b]]) == baseline
    # Fixed Ref tie-break, irrespective of which route returns first.
    assert fuse([[b], [c]]) == fuse([[c], [b]])


@pytest.mark.p2
def test_rc_fus_08_relevance_preserves_quotation_negation_and_condition():
    text = "引述：“如果喝咖啡就能治病”仅为假设，并非事实；不能据此停药。"
    hypothesis = memory("hypothesis", content=text).model_copy(update={"model_space": "test_space"})
    unrelated = memory("unrelated")
    ranked = fuse([[hypothesis, unrelated], [hypothesis]])
    assert ranked[0].memory.ref == hypothesis.ref
    assert ranked[0].memory.model_space == "test_space"
    groups, rendered = assemble(ranked, "quotation", 1024, ModelTokenizer("o200k_base"), 10)
    item = groups[0].items[0]
    assert item.content == text and text in rendered
    assert item.sources == hypothesis.sources
    assert item.representation == "original"
    assert "confidence" not in item.model_dump() and "truthfulness" not in item.model_dump()
