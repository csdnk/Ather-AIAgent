"""RC-AUTH-09 strict fixture assertions, separate from real sharing acceptance."""

import json
from copy import deepcopy
from hashlib import sha256

import pytest

from recall_authorization_support import F1_TEXTS
from recall_shared_read_support import (
    GrantReceipt,
    SharedReadCase,
    assert_active_grant,
    assert_shared_pack,
    prepare_shared_grant,
)
from unit.test_recall_tenant_isolation_support import maintained_case_and_stage, pack


def sharing_case():
    original, _ = maintained_case_and_stage()
    data = original.model_dump(mode="json")
    a, b = data.pop("actors")
    data.pop("request")
    memories = {}
    for name in ("shared", "neighbor", "other-scope", "other-tenant", "u08-local", "u09-local"):
        actor = deepcopy(a)
        for field in ("name", "credential_env"):
            actor.pop(field)
        actor["memory"]["memory_id"] = name + "-memory"
        actor["text"] = (
            F1_TEXTS[0]
            if name == "shared"
            else F1_TEXTS[1]
            if name == "neighbor"
            else "PRIVATE_" + name
        )
        actor["body_hash"] = sha256(actor["text"].encode()).hexdigest()
        actor["sources"][0].update(source_id=name + "-source", content_hash=actor["body_hash"])
        if name == "other-scope":
            actor["memory"]["scope"]["application_id"] = "OtherApp"
        elif name == "other-tenant":
            actor["memory"]["scope"]["tenant_id"] = "OtherTenant"
        elif name in {"u08-local", "u09-local"}:
            actor["memory"]["scope"].update(user_id=name, agent_id=name, application_id=name)
        actor["maintainer_env"] = name.replace("-", "_").upper() + "_MAINT"
        memories[name] = actor
    data.update(
        memories=memories,
        query="private",
        q18_capability_id="owned-grant-controller",
        control_request="/private/tmp/grant-request.json",
        control_receipts="/private/tmp/grant-receipts.json",
        success_directory="/private/tmp/shared-success",
        principals={
            key: {
                "principal_id": key,
                "home_scope": memories[label]["memory"]["scope"],
                "permissions": ["memory:read", "memory:write", "memory:correct", "memory:delete"],
                "auth_epoch": 1,
            }
            for key, label in (("U01", "shared"), ("U08", "u08-local"), ("U09", "u09-local"))
        },
        credential_envs={"U01": "OWNER_TOKEN", "U08": "SHARED_TOKEN", "U09": "CONTROL_TOKEN"},
    )
    data["grant"] = {
        "grant_id": "one-read-grant",
        "grantee_id": "U08",
        "grantee_tenant_id": "T-A",
        "resource": {
            "owner": "remember",
            "object_type": "memory",
            "object_id": "shared-memory",
            "scope": memories["shared"]["memory"]["scope"],
            "version": None,
        },
        "permissions": ["memory:read"],
        "revision": 1,
    }
    return data


def test_grant_is_only_read_for_one_resource_and_an_independent_same_tenant_subject():
    case = SharedReadCase.model_validate(sharing_case())
    assert case.grant.grantee_id == "U08"


@pytest.mark.parametrize("fault", ["write", "resource", "grantee", "same-owner", "tenant"])
def test_widened_or_ownership_confounded_sharing_fixture_is_rejected(fault):
    data = sharing_case()
    if fault == "write":
        data["grant"]["permissions"].append("memory:write")
    elif fault == "resource":
        data["grant"]["resource"]["object_id"] = "neighbor-memory"
    elif fault == "grantee":
        data["grant"]["grantee_id"] = "U09"
    elif fault == "same-owner":
        data["principals"]["U08"]["home_scope"] = data["principals"]["U01"]["home_scope"]
    else:
        data["principals"]["U08"]["home_scope"]["tenant_id"] = "OtherTenant"
    with pytest.raises(ValueError):
        SharedReadCase.model_validate(data)


def qualification(memory, case, decision="allowed"):
    target = {
        "memory": memory.memory.model_dump(mode="json"),
        "generation": memory.projection_generation,
        "model_space": case.model_space,
        "body_hash": memory.body_hash,
        "chunk_index": 0,
        "vector_id": "a" * 64,
        "input_hash": "b" * 64,
        "memory_source": "working",
    }
    result = {
        "target": target,
        "decision": decision,
        "reason_code": decision,
        "manifest": None,
        "guard": None,
    }
    if decision == "allowed":
        result["guard"] = {
            "memory": target["memory"],
            "object_revision": 1,
            "relations_revision": 1,
            "authorization_epoch": 1,
            "body_hash": memory.body_hash,
            "checked_at": "2026-10-09T00:00:00.000Z",
        }
        result["manifest"] = {
            "memory": target["memory"],
            "generation": memory.projection_generation,
            "body_hash": memory.body_hash,
            "model_space": case.model_space,
            "dimensions": 2,
            "chunker_version": "v1",
            "embedding_tokenizer": "native",
            "expected_chunk_count": 1,
            "chunks": [
                {
                    "chunk_index": 0,
                    "vector_id": "a" * 64,
                    "input_hash": "b" * 64,
                    "start_char": 0,
                    "end_char": len(memory.text),
                    "verified": True,
                }
            ],
            "state": "ready",
            "task_id": "actual-projection-task",
            "published_at": "2026-10-09T00:00:00.000Z",
            "vector_location": {
                "kind": "vector",
                "provider_id": "milvus",
                "provider_instance_id": "owned",
                "namespace": "owned",
                "object_key": "vector-key",
                "generation": memory.projection_generation,
                "content_hash": memory.body_hash,
            },
        }
    return result


def grant_receipt(case):
    return {
        "run_id": case.run_id,
        "control_operation_id": "share-" + case.run_id,
        "q18_capability_id": case.q18_capability_id,
        "entrypoint": "Identity.provision",
        "evidence_id": "real-authority-receipt",
        "observer_principal_id": "operator",
        "source_sha": case.source_sha,
        "config_hash": case.configuration.config_hash,
        "image_digest": case.image_digest,
        "backend_binding": case.backend_binding,
        "model_binding": case.model_binding,
        "configuration_revision": 2,
        "observed_at": "2026-10-09T00:00:00.000Z",
        "grants_for_u08": [case.grant.model_dump(mode="json")],
        "grants_for_u09": [],
        "qualification": qualification(case.memories["shared"], case),
        "no_grant_qualification": qualification(case.memories["shared"], case, "excluded"),
        "unshared_qualification": qualification(case.memories["neighbor"], case, "excluded"),
        "qualification_principals": {
            key: case.principals[name].model_dump(mode="json")
            for key, name in (("shared", "U08"), ("no-grant", "U09"), ("unshared", "U08"))
        },
        "permission_checks": [
            {
                "principal_id": "U08",
                "permission": p,
                "memory": case.memories["shared"].memory.model_dump(mode="json"),
                "allowed": p == "memory:read",
            }
            for p in ("memory:read", "memory:write", "memory:correct", "memory:delete")
        ],
    }


def test_active_grant_receipt_requires_current_version_and_authority_not_intent():
    case = SharedReadCase.model_validate(sharing_case())
    assert_active_grant(GrantReceipt.model_validate(grant_receipt(case)), case, "operator")


@pytest.mark.parametrize(
    "fault",
    [
        "version",
        "generation",
        "other-grant",
        "no-grant",
        "mutation",
        "observer",
        "operation",
        "principal",
    ],
)
def test_stale_or_overbroad_authority_receipt_is_rejected(fault):
    case = SharedReadCase.model_validate(sharing_case())
    raw = grant_receipt(case)
    if fault == "version":
        raw["qualification"]["target"]["memory"]["version"] = 2
    elif fault == "generation":
        raw["qualification"]["target"]["generation"] = "old"
    elif fault == "other-grant":
        raw["grants_for_u08"].append(case.grant.model_dump(mode="json"))
    elif fault == "no-grant":
        raw["grants_for_u09"] = [case.grant.model_dump(mode="json")]
    elif fault == "mutation":
        raw["permission_checks"][1]["allowed"] = True
    elif fault == "observer":
        raw["observer_principal_id"] = "other-observer"
    elif fault == "principal":
        raw["qualification_principals"]["no-grant"] = case.principals["U01"].model_dump(mode="json")
    else:
        raw["control_operation_id"] = "stale-run"
    with pytest.raises((ValueError, AssertionError)):
        assert_active_grant(GrantReceipt.model_validate(raw), case, "operator")


@pytest.mark.parametrize("fault", [None, "neighbor", "version", "ownership", "body"])
def test_shared_delivery_keeps_recipient_pack_scope_and_exact_owner_ref(fault):
    case = SharedReadCase.model_validate(sharing_case())
    memory = case.memories["shared"]
    chosen = case.memories["neighbor"] if fault == "neighbor" else memory
    payload = pack(content=chosen.text, refs=[chosen.memory.model_dump(mode="json")])
    payload["scope"] = case.principals["U08"].home_scope.model_dump(mode="json")
    item = payload["groups"][0]["items"][0]
    item["sources"] = [s.model_dump(mode="json") for s in chosen.sources]
    if fault == "version":
        item["memory"]["version"] = 2
    elif fault == "ownership":
        payload["scope"] = memory.memory.scope.model_dump(mode="json")
    elif fault == "body":
        payload["rendered_context"] += case.memories["neighbor"].text
    if fault:
        with pytest.raises(AssertionError):
            assert_shared_pack(payload, case, "U08", memory)
    else:
        assert_shared_pack(payload, case, "U08", memory)


def controller_case(tmp_path):
    data = sharing_case()
    data.update(
        control_request=str(tmp_path / "request.json"),
        control_receipts=str(tmp_path / "receipts.json"),
        success_directory=str(tmp_path / "saved"),
        maintenance_export=str(tmp_path / "stages.json"),
    )
    return SharedReadCase.model_validate(data)


def test_reusable_preparation_publishes_one_read_grant_and_requires_actual_receipt(tmp_path):
    case = controller_case(tmp_path)
    case.control_receipts.write_text(json.dumps([grant_receipt(case)]), encoding="utf-8")
    receipt = prepare_shared_grant(case, "operator", timeout=0)
    request = json.loads(case.control_request.read_text(encoding="utf-8"))
    assert request["entrypoint"] == "Identity.provision"
    assert request["grant"]["permissions"] == ["memory:read"]
    assert request["grant"]["resource"]["object_id"] == "shared-memory"
    assert receipt.evidence_id == "real-authority-receipt"
    assert not list(tmp_path.glob("*.pending"))


def test_intent_without_receipt_is_blocked_and_does_not_manufacture_success(tmp_path):
    case = controller_case(tmp_path)
    with pytest.raises(RuntimeError, match="blocked_fixture.*receipt"):
        prepare_shared_grant(case, "operator", timeout=0)
    assert case.control_request.is_file()
    assert not case.control_receipts.exists()


def test_missing_q18_is_an_explicit_fixture_blocker(tmp_path):
    case = controller_case(tmp_path).model_copy(update={"q18_capability_id": None})
    with pytest.raises(RuntimeError, match="blocked_fixture.*Q18"):
        prepare_shared_grant(case, "operator", timeout=0)
    assert not case.control_request.exists()
