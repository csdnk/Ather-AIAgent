"""RC-AUTH-06 fixture/observation checks; not live authorization evidence."""

from copy import deepcopy
from hashlib import sha256

import pytest

from aether_agent_memory.recall.contracts.models import RecallRequest
from aether_agent_memory.runtime.foundation.common import fingerprint
from recall_selection_intersection_support import (
    SelectionIntersectionCase,
    SelectionStageObservation,
    assert_selection_pack,
    assert_selection_pressure,
    assert_selection_stage,
)
from unit.test_recall_tenant_isolation_support import maintained_case_and_stage, pack


def selection_case():
    original, _ = maintained_case_and_stage()
    deployment = original.model_dump(mode="json")
    deployment["request"]["selection"] = {}
    first = deployment["actors"][0]
    scope = first["memory"]["scope"]
    principal_scope = dict(scope, session_id=None, task_id=None)
    memories = []
    for index, (session, task) in enumerate(
        (("S1", "J1"), ("S1", "J2"), ("S2", "J1"), ("S2", "J2"))
    ):
        memory = deepcopy(first)
        memory["memory"]["scope"].update(session_id=session, task_id=task)
        memory["memory"]["memory_id"] = f"owned-memory-{index}"
        memory["text"] = f"PRIVATE_OWN_{index}"
        memory["body_hash"] = sha256(memory["text"].encode()).hexdigest()
        memory["sources"][0].update(
            source_id=f"owned-source-{index}", content_hash=memory["body_hash"]
        )
        memories.append(memory)
    deployment["actors"][0] = memories[0]
    foreign = deployment["actors"][1]
    foreign["memory"]["scope"] = dict(memories[0]["memory"]["scope"], user_id="other-user")
    foreign["memory"]["memory_id"] = "restricted-memory"
    return {
        "deployment": deployment,
        "principal_scope": principal_scope,
        "memories": memories,
        "authorization_evidence_id": "owned-qualification",
        "expected_refs": {
            key: memories[index]["memory"]
            for key, index in (("s1", 0), ("j1", 0), ("s1j1", 0), ("s2", 3), ("j2", 3))
        },
    }


def test_two_sessions_and_two_tasks_belong_to_one_unbound_subject():
    case = SelectionIntersectionCase.model_validate(selection_case())
    assert len(case.memories) == 4


@pytest.mark.parametrize(
    "fault", ["subject", "grid", "bound-home", "foreign-owned", "wrong-expected"]
)
def test_confounded_authorization_or_selection_fixture_is_rejected(fault):
    data = selection_case()
    if fault == "subject":
        data["memories"][3]["memory"]["scope"]["agent_id"] = "other-agent"
    elif fault == "grid":
        data["memories"][3]["memory"]["scope"]["task_id"] = "J1"
    elif fault == "bound-home":
        data["principal_scope"]["session_id"] = "S1"
    elif fault == "foreign-owned":
        data["deployment"]["actors"][1]["memory"]["scope"]["user_id"] = "same-user"
    else:
        data["expected_refs"]["s1"] = data["memories"][3]["memory"]
    with pytest.raises(ValueError):
        SelectionIntersectionCase.model_validate(data)


def selection_probe(case):
    _, raw = maintained_case_and_stage()
    hits = []
    # Authorized S2J2 is global Top1 outside S1, then the unauthorized owner.
    for rank, (actor, score) in enumerate(
        (
            (case.memories[3], 0.99),
            (case.deployment.actors[1], 0.95),
            (case.memories[0], 0.8),
            (case.memories[1], 0.7),
            (case.memories[2], 0.6),
        ),
        1,
    ):
        hit = deepcopy(raw["candidates"][0])
        hit.update(rank=rank, score=score)
        hit["target"].update(
            memory=actor.memory.model_dump(mode="json"),
            body_hash=actor.body_hash,
            generation=actor.projection_generation,
        )
        hits.append(hit)
    return hits


def test_actual_global_top1_outside_selection_does_not_displace_the_legal_target():
    case = SelectionIntersectionCase.model_validate(selection_case())
    assert_selection_pressure(
        selection_probe(case), case, case.selector("s1"), case.expected_refs["s1"]
    )


@pytest.mark.parametrize("fault", ["favorable", "hash", "missing", "foreign-low"])
def test_pressure_receipt_requires_both_selection_and_authorization_challenges(fault):
    case = SelectionIntersectionCase.model_validate(selection_case())
    hits = selection_probe(case)
    if fault == "favorable":
        hits[0]["target"]["memory"] = case.memories[1].memory.model_dump(mode="json")
    elif fault == "hash":
        hits[0]["target"]["body_hash"] = "0" * 64
    elif fault == "missing":
        hits.pop()
    else:
        hits[1]["score"] = 0.1
    with pytest.raises(AssertionError):
        assert_selection_pressure(hits, case, case.selector("s1"), case.expected_refs["s1"])


def selection_stage(case, key="s1"):
    _, raw = maintained_case_and_stage()
    expected = case.expected_refs["s1" if key == "empty" else key]
    owner = next(a for a in case.memories if a.memory == expected)
    raw.update(
        selection=case.selector(key).model_dump(mode="json"),
        authorized_refs=[a.memory.model_dump(mode="json") for a in case.memories],
        eligible_refs=[r.model_dump(mode="json") for r in case.eligible(case.selector(key))],
        authorization_evidence_id=case.authorization_evidence_id,
        selection_contract_id=None,
    )
    for boundary in ("qualified", "body_reads", "result_refs"):
        raw[boundary] = [expected.model_dump(mode="json")]
    raw["candidates"][0]["target"].update(
        memory=expected.model_dump(mode="json"),
        body_hash=owner.body_hash,
        generation=owner.projection_generation,
    )
    for event in raw["events"]:
        event["subject"].update(
            object_id=expected.memory_id, scope=expected.scope.model_dump(mode="json")
        )
        event["payload"]["memory"] = expected.model_dump(mode="json")
        event["payload_hash"] = fingerprint(event["payload"])
    return raw


@pytest.mark.parametrize(
    "fault", [None, "selection", "authorization", "eligibility", "model", "body"]
)
def test_stage_evidence_proves_the_intersection_and_not_just_the_tenant(fault):
    case = SelectionIntersectionCase.model_validate(selection_case())
    raw = selection_stage(case)
    if fault == "selection":
        raw["selection"] = case.selector("s2").model_dump(mode="json")
    elif fault == "authorization":
        raw["authorized_refs"][-1] = case.deployment.actors[1].memory.model_dump(mode="json")
    elif fault == "eligibility":
        raw["eligible_refs"] = [case.memories[3].memory.model_dump(mode="json")]
    elif fault in {"model", "body"}:
        raw["model_input_refs" if fault == "model" else "body_reads"] = [
            case.deployment.actors[1].memory.model_dump(mode="json")
        ]
    stage = SelectionStageObservation.model_validate(raw)
    request = case.deployment.request.model_copy(update={"selection": case.selector("s1")})

    def check():
        assert_selection_stage(
            stage,
            case,
            request,
            case.expected_refs["s1"],
            "original",
            "job-a",
            "job-a",
            "1" * 32,
            "legal-operator",
        )

    if fault:
        with pytest.raises(AssertionError):
            check()
    else:
        check()


@pytest.mark.parametrize("fault", [None, "outside-session", "foreign-owner", "empty", "scope"])
def test_pack_requires_exact_ref_body_and_effective_scope(fault):
    case = SelectionIntersectionCase.model_validate(selection_case())
    owner = case.memories[0]
    chosen = (
        case.memories[3]
        if fault == "outside-session"
        else case.deployment.actors[1]
        if fault == "foreign-owner"
        else owner
    )
    payload = pack(content=chosen.text, refs=[chosen.memory.model_dump(mode="json")])
    payload["scope"] = case.result_scope(case.selector("s1")).model_dump(mode="json")
    payload["groups"][0]["items"][0]["sources"] = [
        s.model_dump(mode="json") for s in chosen.sources
    ]
    if fault == "empty":
        payload.update(groups=[], outcome="empty", rendered_context="", tokens_used=0)
    elif fault == "scope":
        payload["scope"]["task_id"] = "J1"
    if fault:
        with pytest.raises(AssertionError):
            assert_selection_pack(payload, case, case.selector("s1"), owner.memory)
    else:
        assert_selection_pack(payload, case, case.selector("s1"), owner.memory)


def test_unknown_empty_policy_checks_safety_without_assuming_all_eligible_or_current_session():
    case = SelectionIntersectionCase.model_validate(selection_case())
    raw = selection_stage(case, "empty")
    raw["eligible_refs"] = [case.memories[0].memory.model_dump(mode="json")]
    stage = SelectionStageObservation.model_validate(raw)
    assert_selection_stage(
        stage,
        case,
        case.deployment.request,
        None,
        "original",
        "job-a",
        "job-a",
        "1" * 32,
        "legal-operator",
    )
    raw["model_input_refs"] = [case.deployment.actors[1].memory.model_dump(mode="json")]
    with pytest.raises(AssertionError):
        assert_selection_stage(
            SelectionStageObservation.model_validate(raw),
            case,
            case.deployment.request,
            None,
            "original",
            "job-a",
            "job-a",
            "1" * 32,
            "legal-operator",
        )


@pytest.mark.parametrize("fault", [None, "source-sha", "foreign", "expected"])
def test_empty_extent_requires_an_authoritative_target_bound_contract(fault):
    data = selection_case()
    refs = [a["memory"] for a in data["memories"]]
    data["empty_contract"] = {
        "evidence_id": "Q01-reviewed",
        "source_sha": data["deployment"]["source_sha"],
        "eligible_refs": refs,
        "expected_ref": refs[0],
    }
    if fault == "source-sha":
        data["empty_contract"]["source_sha"] = "0" * 40
    elif fault == "foreign":
        data["empty_contract"]["eligible_refs"] = [data["deployment"]["actors"][1]["memory"]]
    elif fault == "expected":
        data["empty_contract"]["expected_ref"] = data["deployment"]["actors"][1]["memory"]
    if fault:
        with pytest.raises(ValueError):
            SelectionIntersectionCase.model_validate(data)
    else:
        assert SelectionIntersectionCase.model_validate(data).empty_contract is not None


def test_omitted_selection_is_not_normalized_to_an_empty_object():
    assert RecallRequest.model_fields["selection"].is_required()
    with pytest.raises(ValueError):
        RecallRequest.model_validate({"query": "private", "sources": "working"})
