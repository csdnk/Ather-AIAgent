"""Fault checks for real Top-1 evidence assertions; not native/backend acceptance."""

from copy import deepcopy

import pytest

from aether_agent_memory.remember.contracts.models import ProjectionTarget
from recall_long_term_search_support import assert_top1_pressure, assert_top1_requests
from unit.test_recall_tenant_isolation_support import hit


def pressure(axis="source"):
    local = hit("T-A", 0.6, 2)
    local["target"]["memory_source"] = "long_term"
    foreign = deepcopy(local)
    foreign["target"]["vector_id"] = "f" * 64
    foreign["score"], foreign["rank"] = 0.95, 1
    if axis == "scope":
        foreign["target"]["memory"]["scope"]["session_id"] = "outside-session"
    elif axis == "space":
        foreign["target"]["model_space"] = "wrong-space"
    else:
        foreign["target"]["memory_source"] = "working"
    raw = [foreign, local]
    filtered = [{**local, "rank": 1}]
    return raw, filtered, ProjectionTarget.model_validate(local["target"]), axis


@pytest.mark.parametrize("axis", ["scope", "space", "source"])
def test_strictly_higher_ineligible_hit_must_lose_to_the_legal_target(axis):
    raw, filtered, target, axis = pressure(axis)
    assert_top1_pressure(raw, raw[:1], filtered, target, axis)


@pytest.mark.parametrize("fault", ["tie", "missing-raw", "missing-filtered", "foreign-wins"])
def test_favorable_or_post_truncation_observations_do_not_prove_pre_filtering(fault):
    raw, filtered, target, axis = pressure()
    if fault == "tie":
        raw[0]["score"] = 0.6
    elif fault == "missing-raw":
        raw = []
    elif fault == "missing-filtered":
        filtered = []
    else:
        filtered = [raw[0]]
    with pytest.raises(AssertionError):
        assert_top1_pressure(raw, raw[:1], filtered, target, axis)


def search_requests():
    raw, _, target, _ = pressure()
    pair = {
        "collection_name": "owned",
        "data": [[1.0, 0.0]],
        "anns_field": "vector",
        "filter": 'vector_id in ["'
        + target.vector_id
        + '", "'
        + raw[0]["target"]["vector_id"]
        + '"]',
        "limit": 2,
        "search_params": {"metric_type": "IP"},
        "consistency_level": "Strong",
    }
    filtered = {
        **pair,
        "limit": 1,
        "filter": '(tenant_id == "T-A" and application_id == "App" and user_id == "same-user"'
        ' and agent_id == "same-agent" and session_id == "same-session")'
        ' and model_space == "native-space" and '
        '(not exists target["memory_source"] or target["memory_source"] == "long_term")',
    }
    return pair, {**pair, "limit": 1}, filtered, target, raw[0]["target"]["vector_id"]


def test_k1_is_an_actual_backend_request_with_global_scope_space_source_filters():
    pair, raw_top, filtered, target, adverse = search_requests()
    assert_top1_requests(pair, raw_top, filtered, target, adverse, (1.0, 0.0), "owned")


@pytest.mark.parametrize("fault", ["k", "vector", "collection", "scope-or", "no-source"])
def test_request_labels_alone_cannot_establish_filtering_before_k1(fault):
    pair, raw_top, filtered, target, adverse = search_requests()
    if fault == "k":
        filtered["limit"] = 1000
    elif fault == "vector":
        filtered["data"] = [[0.0, 1.0]]
    elif fault == "collection":
        filtered["collection_name"] = "other"
    elif fault == "scope-or":
        filtered["filter"] = "(" + filtered["filter"] + ") or true"
    else:
        filtered["filter"] = filtered["filter"].split(" and (not exists")[0]
    with pytest.raises(AssertionError):
        assert_top1_requests(pair, raw_top, filtered, target, adverse, (1.0, 0.0), "owned")
