"""Strict fixture assertions for AET-24, separate from live HTTP acceptance."""

from copy import deepcopy

import pytest

from recall_scope_isolation_support import (
    ScopeIsolationCase,
    ScopeStageObservation,
    assert_no_implicit_sharing,
    assert_scope_pressure,
)
from recall_tenant_isolation_support import assert_stage_isolation
from unit.test_recall_tenant_isolation_support import maintained_case_and_stage


def scope_case(dimension="application_id"):
    original, _ = maintained_case_and_stage()
    data = original.model_dump(mode="json")
    data["dimension"] = dimension
    data["authorization_evidence_id"] = "maintained-no-share-receipt"
    a, b = data["actors"]
    b["memory"]["scope"] = dict(a["memory"]["scope"])
    b["memory"]["scope"][dimension] += "-other"
    b["memory"]["memory_id"] = "other-memory"
    return data


@pytest.mark.parametrize("dimension", ["application_id", "user_id", "agent_id"])
def test_fixture_changes_exactly_one_authorization_dimension(dimension):
    case = ScopeIsolationCase.model_validate(scope_case(dimension))
    assert case.dimension == dimension


@pytest.mark.parametrize(
    "fault",
    ["tenant", "two-dimensions", "no-difference", "shared-id", "same-body", "client-selection"],
)
def test_confounded_or_non_private_fixture_is_rejected(fault):
    data = scope_case()
    a, b = data["actors"]
    if fault == "tenant":
        b["memory"]["scope"]["tenant_id"] += "-other"
    elif fault == "two-dimensions":
        b["memory"]["scope"]["agent_id"] += "-other"
    elif fault == "no-difference":
        b["memory"]["scope"] = deepcopy(a["memory"]["scope"])
    elif fault == "shared-id":
        b["memory"]["memory_id"] = a["memory"]["memory_id"]
    elif fault == "same-body":
        b["text"], b["body_hash"] = a["text"], a["body_hash"]
    else:
        data["request"]["selection"]["application_id"] = "other"
    with pytest.raises(ValueError):
        ScopeIsolationCase.model_validate(data)


def probe(case):
    _, raw = maintained_case_and_stage()
    hits = []
    for actor, score, rank in ((case.actors[1], 0.95, 1), (case.actors[0], 0.6, 2)):
        hit = deepcopy(raw["candidates"][0])
        hit.update(score=score, rank=rank)
        hit["target"].update(
            memory=actor.memory.model_dump(mode="json"),
            generation=actor.projection_generation,
            body_hash=actor.body_hash,
        )
        hits.append(hit)
    return hits


@pytest.mark.parametrize("dimension", ["application_id", "user_id", "agent_id"])
def test_same_tenant_adverse_top1_is_bound_to_the_exact_dimension(dimension):
    case = ScopeIsolationCase.model_validate(scope_case(dimension))
    assert_scope_pressure(probe(case), case, case.actors[0])


@pytest.mark.parametrize("fault", ["tie", "local-first", "hash", "generation", "space", "missing"])
def test_favorable_or_unbound_scope_pressure_is_rejected(fault):
    case = ScopeIsolationCase.model_validate(scope_case())
    hits = probe(case)
    if fault == "tie":
        hits[1]["score"] = hits[0]["score"]
    elif fault == "local-first":
        hits.reverse()
    elif fault == "missing":
        hits.pop()
    else:
        field = {"hash": "body_hash", "generation": "generation", "space": "model_space"}[fault]
        hits[0]["target"][field] = "0" * 64 if fault == "hash" else "stale"
    with pytest.raises(AssertionError):
        assert_scope_pressure(hits, case, case.actors[0])


def scope_stage():
    _, raw = maintained_case_and_stage()
    raw.update(authorization_evidence_id="maintained-no-share-receipt", sharing_grant_ids=[])
    return raw


@pytest.mark.parametrize("fault", ["none", "implicit-grant", "unbound-policy"])
def test_no_share_control_consumes_bound_qualification_evidence(fault):
    case = ScopeIsolationCase.model_validate(scope_case())
    raw = scope_stage()
    if fault == "implicit-grant":
        raw["sharing_grant_ids"] = ["unexpected-grant"]
    elif fault == "unbound-policy":
        raw["authorization_evidence_id"] = "another-policy"
    stage = ScopeStageObservation.model_validate(raw)
    if fault == "none":
        assert_no_implicit_sharing(stage, case)
    else:
        with pytest.raises(AssertionError):
            assert_no_implicit_sharing(stage, case)


@pytest.mark.parametrize("dimension", ["application_id", "user_id", "agent_id"])
@pytest.mark.parametrize(
    "boundary", [None, "qualified", "body_reads", "model_input_refs", "result_refs"]
)
def test_same_tenant_foreign_ref_cannot_cross_content_boundaries(dimension, boundary):
    case = ScopeIsolationCase.model_validate(scope_case(dimension))
    raw = scope_stage()
    if boundary:
        raw[boundary] = [case.actors[1].memory.model_dump(mode="json")]
    stage = ScopeStageObservation.model_validate(raw)

    def check():
        return assert_stage_isolation(
            stage, case, case.actors[0], "original", "job-a", "job-a", "1" * 32, "legal-operator"
        )

    if boundary:
        with pytest.raises(AssertionError):
            check()
    else:
        assert check() == {"event-read", "event-packed"}
