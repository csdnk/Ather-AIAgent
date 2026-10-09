"""AET-36 evidence guard regressions; not real both-source acceptance."""

from copy import deepcopy
from types import SimpleNamespace

import pytest

from aether_agent_memory.remember.contracts.models import MemoryRef, SourceRef
from recall_both_sources_support import assert_both_execution, assert_both_pack, assert_fusion_plan
from unit.test_recall_tenant_isolation_support import pack
from unit.test_recall_working_search_support import observed_execution


def both_execution():
    probe, native = observed_execution()
    long_term = deepcopy(probe.searches[0])
    long_term["request"].memory_source = "long_term"
    long_term["sdk"][0]["filter"] = (
        '(session_id == "s1") and model_space == "actual-space" and '
        '(not exists target["memory_source"] or target["memory_source"] == "long_term")'
    )
    probe.searches.append(long_term)
    return probe, native


def test_both_complete_requires_successful_real_boundary_observations_for_each_route():
    probe, native = both_execution()
    evidence = assert_both_execution(
        probe, "operation", "query", native, {"working": (), "long_term": ()}
    )
    assert evidence["working"]["working_calls"] == 1
    assert evidence["long_term"]["long_term_calls"] == 1


@pytest.mark.parametrize("fault", ["one-route", "failed", "trace", "no-sdk", "source-bypass"])
def test_both_label_cannot_substitute_for_actual_dual_execution(fault):
    probe, native = both_execution()
    if fault == "one-route":
        probe.searches.pop()
    elif fault == "failed":
        probe.searches[1]["result"] = None
    elif fault == "trace":
        probe.searches[1]["trace_id"] = "b" * 32
    elif fault == "no-sdk":
        probe.searches[1]["sdk"] = []
    else:
        probe.searches[1]["sdk"][0]["filter"] += " or true"
    with pytest.raises(AssertionError):
        assert_both_execution(probe, "operation", "query", native, {"working": (), "long_term": ()})


@pytest.mark.parametrize("fault", [None, "version", "coverage", "selected", "provenance"])
def test_both_pack_preserves_exact_authoritative_ref_and_provenance(fault):
    payload = pack(content="完整正文。")
    payload.update(selected_sources=["working", "long_term"])
    payload["coverage"].update(working="complete", long_term="complete")
    item = payload["groups"][0]["items"][0]
    memory = SimpleNamespace(
        ref=MemoryRef.model_validate(item["memory"]),
        content=item["content"],
        sources=tuple(SourceRef.model_validate(s) for s in item["sources"]),
    )
    if fault == "version":
        item["memory"]["version"] += 1
    elif fault == "coverage":
        payload["coverage"]["long_term"] = "unavailable"
        payload.update(outcome="degraded", degradation_reasons=["long_term_unavailable"])
    elif fault == "selected":
        payload["selected_sources"] = ["working"]
        payload["coverage"]["long_term"] = "not_requested"
    elif fault == "provenance":
        item["sources"][0]["source_id"] = "foreign"
    if fault is None:
        assert_both_pack(payload, (memory,))
    else:
        with pytest.raises(AssertionError):
            assert_both_pack(payload, (memory,))


def test_both_fusion_rejects_complete_claim_without_second_route_execution():
    request = SimpleNamespace(memory_source="working")
    plan = SimpleNamespace(
        request=SimpleNamespace(
            sources=("working", "long_term"),
            working_search=request,
            long_term_search=SimpleNamespace(memory_source="long_term"),
        ),
        degradation_reasons=(),
        skipped_group_ids=(),
    )
    with pytest.raises(AssertionError, match="missing route"):
        assert_fusion_plan(plan, [{"request": request}], (), {})
