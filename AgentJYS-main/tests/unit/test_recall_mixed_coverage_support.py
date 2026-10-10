"""Mixed-coverage guard checks; never substitutes for native-chain acceptance."""

import json
from copy import deepcopy
from types import SimpleNamespace

import pytest

from aether_agent_memory.remember.contracts.models import MemoryRef, SourceRef
from recall_mixed_coverage_support import (
    assert_mixed_observation,
    assert_mixed_pack,
    mixed_policy,
)
from unit.test_recall_tenant_isolation_support import pack


def partial_pack(state):
    payload = pack(content="完整的可读茶偏好。")
    payload.update(outcome="degraded", degradation_reasons=["working_index_" + state])
    payload["coverage"]["working"] = "partial"
    item = payload["groups"][0]["items"][0]
    ready = SimpleNamespace(
        ref=MemoryRef.model_validate(item["memory"]),
        content=item["content"],
        sources=tuple(SourceRef.model_validate(row) for row in item["sources"]),
    )
    missing = {"ref": {**item["memory"], "memory_id": "unready"}, "text": "未就绪咖啡正文。"}
    return payload, ready, missing


@pytest.mark.parametrize("state", ["pending", "failed"])
def test_valid_partial_pack_keeps_exact_ready_body_and_missing_state(state):
    payload, ready, missing = partial_pack(state)
    assert_mixed_pack(payload, ready, missing, state, "test")


@pytest.mark.parametrize(
    "fault",
    [
        "complete",
        "wrong-reason",
        "missing-ref",
        "truncated",
        "foreign-scope",
        "body-fallback",
        "policy-version",
        "other-source",
    ],
)
def test_partial_pack_rejects_false_coverage_unsafe_content_and_policy_substitution(fault):
    payload, ready, missing = partial_pack("pending")
    item = payload["groups"][0]["items"][0]
    if fault == "complete":
        payload["coverage"]["working"] = "complete"
    elif fault == "wrong-reason":
        payload["degradation_reasons"] = ["working_index_failed"]
    elif fault == "missing-ref":
        item["memory"] = missing["ref"]
    elif fault == "truncated":
        item["content"] = "截断"
    elif fault == "foreign-scope":
        item["memory"]["scope"]["session_id"] = "outside-selection"
    elif fault == "body-fallback":
        payload["rendered_context"] += missing["text"]
    elif fault == "policy-version":
        payload["policy_version"] = "unapproved"
    else:
        payload["selected_sources"] = ["working", "long_term"]
        payload["coverage"]["long_term"] = "complete"
    with pytest.raises((AssertionError, ValueError)):
        assert_mixed_pack(payload, ready, missing, "pending", "test")


def test_missing_approval_does_not_become_an_implicit_allow_or_deny_policy():
    for mode in ("allow", "deny"):
        settings, evidence = mixed_policy(None, mode)
        assert settings == {} and not evidence["confirmed"]
        assert evidence["requested_mode"] == mode and "Q09" in evidence["restriction"]


def test_policy_manifest_rejects_invented_server_toggle_and_records_approval(tmp_path):
    profile = {
        "settings": {},
        "policy_version": "approved-version",
        "decision_reference": "Q09-approved-decision",
    }
    manifest = {"profiles": {"allow": deepcopy(profile), "deny": deepcopy(profile)}}
    path = tmp_path / "mixed-policy.json"
    path.write_text(json.dumps(manifest))
    settings, evidence = mixed_policy(path, "allow")
    assert settings == {} and evidence["confirmed"]
    assert evidence["decision_reference"] == "Q09-approved-decision"
    assert evidence["expected_policy_version"] == "approved-version"
    assert len(evidence["manifest_sha256"]) == 64
    manifest["profiles"]["allow"]["settings"] = {"allow_partial": True}
    path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="allow_partial"):
        mixed_policy(path, "allow")


@pytest.mark.parametrize("fault", ["complete", "wrong-state", "enumeration", "unready-body"])
def test_readiness_and_body_routes_cannot_hide_missing_projection(fault):
    _, ready, missing = partial_pack("pending")
    observation = {
        "readiness": [
            {
                "source": "working",
                "ready_count": 1,
                "pending_count": 1,
                "failed_count": 0,
                "complete": False,
            }
        ],
        "working_enumerations": 0,
        "body_refs": [ready.ref.model_dump(mode="json")],
        "routes": [{"source": "working"}],
    }
    if fault == "complete":
        observation["readiness"][0]["complete"] = True
    elif fault == "wrong-state":
        observation["readiness"][0].update(pending_count=0, failed_count=1)
    elif fault == "enumeration":
        observation["working_enumerations"] = 1
    else:
        observation["body_refs"].append(missing["ref"])
    with pytest.raises(AssertionError):
        assert_mixed_observation(observation, "pending", ready)
