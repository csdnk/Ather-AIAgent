"""Strict RC-AUTH-07/08 peer contracts, never live authorization evidence."""

from copy import deepcopy

import pytest

from aether_agent_memory.recall.contracts.models import RecallRequest
from recall_forged_authorization_support import (
    INDEX_VARIANTS,
    REQUEST_VARIANTS,
    ForgeryCase,
    ForgeryStageObservation,
    assert_authority_veto,
    assert_no_index_disclosure,
    forged_request,
)
from unit.test_recall_tenant_isolation_support import maintained_case_and_stage


def forgery_case():
    case, _ = maintained_case_and_stage()
    data = case.model_dump(mode="json")
    a, b = data["actors"]
    b["memory"]["memory_id"] = "restricted-memory"
    for field in ("application_id", "user_id", "agent_id"):
        b["memory"]["scope"][field] += "-foreign"
    data.update(
        principal={
            "principal_id": "U01",
            "home_scope": a["memory"]["scope"],
            "permissions": ["memory:read"],
            "auth_epoch": 7,
        },
        q06_mapping_id="qualification-body-rerank-mapping",
        q18_capability_id="owned-injector",
    )
    data["server_settings"].update(rerank_policy="required", reranker_model="bound-model")
    return data


@pytest.mark.parametrize("variant", REQUEST_VARIANTS)
def test_request_injection_uses_real_schema_and_never_becomes_a_trusted_identity(variant):
    case = ForgeryCase.model_validate(forgery_case())
    payload, status = forged_request(case, variant)
    if status == 422:
        with pytest.raises(ValueError):
            RecallRequest.model_validate(payload)
    else:
        assert RecallRequest.model_validate(payload).selection != case.request.selection
    assert case.principal.principal_id == "U01" and case.principal.auth_epoch == 7


def injected_stage(case, variant="index-scope"):
    _, raw = maintained_case_and_stage()
    owner, foreign = case.actors
    local = raw["candidates"][0]
    local["target"]["body_hash"] = owner.body_hash
    spoof = deepcopy(local)
    spoof.update(rank=1, score=0.99)
    spoof["target"].update(
        memory=foreign.memory.model_dump(mode="json"),
        body_hash=foreign.body_hash,
        generation=foreign.projection_generation,
    )
    spoof["target"]["memory"]["scope"] = owner.memory.scope.model_dump(mode="json")
    local_probe = deepcopy(local)
    local_probe.update(rank=2, score=0.6)
    entity = {"target": deepcopy(spoof["target"])}
    if variant == "index-grant":
        entity["grant"] = {"principal_id": "U01", "permission": "memory:read"}
    elif variant == "index-auth_epoch":
        entity["auth_epoch"] = 999
    raw.update(
        trusted_principal=case.principal.model_dump(mode="json"),
        q06_mapping_id=case.q06_mapping_id,
        body_read_attempts=[owner.memory.model_dump(mode="json")],
        injection_kind=variant,
        q18_capability_id=case.q18_capability_id,
        injection_evidence_id="owned-injection",
        injection_operation_id="original",
        raw_index_hit={"entity": entity, "distance": 0.99},
        index_hits=[spoof, local_probe],
        qualification_results=[
            {
                "target": spoof["target"],
                "decision": "excluded",
                "reason_code": "not_authorized",
                "manifest": None,
                "guard": None,
            }
        ],
        stage_sequence=[1, 2, 3, 4, 5],
        model_input_refs=[owner.memory.model_dump(mode="json")],
    )
    return raw


@pytest.mark.parametrize("variant", INDEX_VARIANTS)
def test_strict_peer_accepts_internal_discovery_only_with_authoritative_veto(variant):
    case = ForgeryCase.model_validate(forgery_case())
    stage = ForgeryStageObservation.model_validate(injected_stage(case, variant))
    assert_authority_veto(stage, case, variant)


@pytest.mark.parametrize(
    "fault",
    [
        "epoch",
        "scope",
        "read-attempt",
        "qualified",
        "model",
        "result",
        "decision",
        "order",
        "operation",
        "mapping",
    ],
)
def test_strict_peer_rejects_forged_authority_or_any_post_veto_body_path(fault):
    case = ForgeryCase.model_validate(forgery_case())
    raw = injected_stage(case)
    if fault == "epoch":
        raw["trusted_principal"]["auth_epoch"] = 999
    elif fault == "scope":
        raw["trusted_principal"]["home_scope"] = case.actors[1].memory.scope.model_dump(mode="json")
    elif fault in {"read-attempt", "qualified", "model", "result"}:
        key = {
            "read-attempt": "body_read_attempts",
            "model": "model_input_refs",
            "result": "result_refs",
        }.get(fault, fault)
        raw[key] = [raw["index_hits"][0]["target"]["memory"]]
    elif fault == "decision":
        raw["qualification_results"][0]["decision"] = "unverifiable"
    elif fault == "order":
        raw["stage_sequence"] = [2, 1, 3, 4, 5]
    elif fault == "operation":
        raw["injection_operation_id"] = "different-operation"
    else:
        raw["q06_mapping_id"] = "unbound-mapping"
    with pytest.raises(AssertionError):
        assert_authority_veto(ForgeryStageObservation.model_validate(raw), case, "index-scope")


@pytest.mark.parametrize(
    "payload",
    [
        {"debug": {"raw_vector": [0.1, 0.2]}},
        {"index_payload": {}},
        {"diagnostic": {"memory_id": "restricted-memory"}},
    ],
)
def test_ordinary_diagnostics_must_not_disclose_index_or_excluded_objects(payload):
    with pytest.raises(AssertionError):
        assert_no_index_disclosure(payload, ("restricted-memory",))
