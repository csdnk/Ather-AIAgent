"""RC-AUTH-14 strict matrix/response tests, separately counted from live acceptance."""

import json
from hashlib import sha256

import httpx
import pytest

from aether_agent_memory.recall.contracts.models import ContextPack
from recall_ownership_matrix_support import (
    VARIANTS,
    MatrixRule,
    OwnershipCase,
    assert_metadata,
    assert_rule,
    assert_safety,
    assert_visible,
    endpoint,
    read_policy,
)
from recall_shared_read_support import SavedSharedPack
from unit.test_recall_shared_read_support import sharing_case
from unit.test_recall_tenant_isolation_support import pack
from unit.test_recall_tenant_lifecycle_support import lifecycle_case, original_pack


def matrix_case(tmp_path, dimension="user_id"):
    sharing = sharing_case()
    fields = (
        "run_id",
        "query",
        "operator_env",
        "configuration",
        "server_settings",
        "source_sha",
        "image_digest",
        "backend_binding",
        "model_binding",
        "model_space",
    )
    raw = {key: sharing[key] for key in fields if key != "query"}
    raw.update(
        memories=[sharing["memories"]["shared"], sharing["memories"]["neighbor"]],
        foreign_dimension=dimension,
        request={
            "query": sharing["query"],
            "sources": "working",
            "selection": {"session_id": "same-session"},
        },
        maintenance_export=str(tmp_path / "stage"),
        success_directory=str(tmp_path / "F12"),
        policy_file=str(tmp_path / "policy.json"),
    )
    scope = sharing["principals"]["U01"]["home_scope"]
    raw["principals"] = {
        name: {
            "principal_id": name,
            "home_scope": {**scope, **({dimension: "foreign"} if name == "U02" else {})},
            "permissions": ["maintenance:diagnose"] + ([] if name == "U07" else ["memory:read"]),
            "auth_epoch": 1,
        }
        for name in ("U01", "U06", "U07", "U02")
    }
    raw["credential_envs"] = {name: name + "_TOKEN" for name in raw["principals"]}
    return OwnershipCase.model_validate(raw)


def original(case):
    coffee = case.memories[0]
    payload = pack(content=coffee.text, refs=[coffee.memory.model_dump(mode="json")])
    payload["scope"] = case.principals["U01"].home_scope.model_dump(mode="json")
    payload["groups"][0]["items"][0]["sources"] = [
        s.model_dump(mode="json") for s in coffee.sources
    ]
    return SavedSharedPack(
        operation_id="original-op",
        job_id="original-job",
        trace_id="1" * 32,
        grant_evidence_id=None,
        pack=ContextPack.model_validate(payload),
    )


def response(payload, status=200):
    return httpx.Response(status, json=payload)


def filtered_trace(saved):
    return {
        "trace_id": saved.trace_id,
        "records": [],
        "next_after": None,
        "coverage": "retained_records_only",
        "last_pruned_at": None,
        "local_dropped_records": 0,
        "overview": {
            "span_count": 0,
            "failed_span_count": 0,
            "open_span_count": 0,
            "open_span_ids": [],
            "open_meaning": "running_or_interrupted; inspect durable task state",
        },
    }


def test_complete_identity_surface_matrix_includes_separate_user_and_agent_scope_cases():
    assert len(VARIANTS) == len(set(VARIANTS)) == 35
    assert {v.split(":")[0] for v in VARIANTS} == {"U01", "U06", "U07", "U02-user", "U02-agent"}


@pytest.mark.parametrize("viewer", ["U07", "U02-user", "U02-agent"])
@pytest.mark.parametrize("surface", ["recall-result", "operation-result"])
def test_diagnose_and_foreign_read_cannot_return_original_body(tmp_path, viewer, surface):
    case = matrix_case(tmp_path)
    saved = original(case)
    with pytest.raises(AssertionError):
        assert_safety(
            response(saved.pack.model_dump(mode="json")), case, saved, viewer, surface, ()
        )


@pytest.mark.parametrize("viewer", ["U01", "U06"])
def test_read_and_current_scope_may_deliver_exact_original_pack(tmp_path, viewer):
    case = matrix_case(tmp_path)
    saved = original(case)
    payload = saved.pack.model_dump(mode="json")
    rule = MatrixRule(decision="visible", status=200, allowed_fields=tuple(payload))
    result = response(payload)
    assert_safety(result, case, saved, viewer, "recall-result", ())
    assert_rule(result, rule, "recall-result", saved, case, viewer)


@pytest.mark.parametrize(
    "surface", ["diagnostic-task", "job", "record", "trace-list", "trace-detail"]
)
def test_even_legitimate_diagnostics_never_carry_body_or_arbitrary_output(tmp_path, surface):
    case = matrix_case(tmp_path)
    saved = original(case)
    with pytest.raises(AssertionError):
        assert_safety(
            response({"nested": {"output": {"content": case.memories[0].text}}}),
            case,
            saved,
            "U01",
            surface,
            (),
        )


@pytest.mark.parametrize("viewer", ["U06", "U07", "U02-user", "U02-agent"])
def test_same_scope_diagnose_does_not_grant_another_initiators_trace(tmp_path, viewer):
    case = matrix_case(tmp_path)
    saved = original(case)
    page = filtered_trace(saved)
    rule = MatrixRule(decision="filtered", status=200, allowed_fields=tuple(page))
    assert_safety(response(page), case, saved, viewer, "trace-detail", ())
    assert_rule(response(page), rule, "trace-detail", saved, case, viewer)
    page["overview"]["span_count"] = 1
    with pytest.raises(AssertionError):
        assert_safety(response(page), case, saved, viewer, "trace-detail", ())


@pytest.mark.parametrize("viewer", ["U06", "U07", "U02-user", "U02-agent"])
def test_original_trace_cannot_leak_in_any_list_page(tmp_path, viewer):
    case = matrix_case(tmp_path)
    saved = original(case)
    assert_safety(response({"items": [], "next_before": 10}), case, saved, viewer, "trace-list", ())
    with pytest.raises(AssertionError):
        assert_safety(
            response({"items": [{"trace_id": saved.trace_id}]}),
            case,
            saved,
            viewer,
            "trace-list",
            (),
        )


@pytest.mark.parametrize(
    "payload",
    [
        {"code": "FORBIDDEN", "message": "secret-bearer-credential"},
        {"code": "FORBIDDEN", "message": "original-job"},
        {"code": "FORBIDDEN", "debug": {"body": "hidden"}},
        {"code": "FORBIDDEN", "trace": "1" * 32},
    ],
)
def test_public_denial_must_not_echo_original_objects_or_credentials(tmp_path, payload):
    case = matrix_case(tmp_path)
    with pytest.raises(AssertionError):
        assert_safety(
            response(payload, 403),
            case,
            original(case),
            "U07",
            "job",
            ("secret-bearer-credential",),
        )


def test_q06_field_allowlist_and_typed_contract_reject_diagnostic_extras(tmp_path):
    case = matrix_case(tmp_path)
    saved = original(case)
    typed = original_pack(lifecycle_case(tmp_path)).job.model_dump(mode="json")
    rule = MatrixRule(decision="visible", status=200, allowed_fields=tuple(typed))
    assert_rule(response(typed), rule, "job", saved, case, "U06")
    typed["credential_sha256"] = "a" * 64
    with pytest.raises(AssertionError):
        assert_rule(response(typed), rule, "job", saved, case, "U06")


def policy_payload(case):
    data = {
        "q01_evidence_id": "Q01-confirmed",
        "q06_evidence_id": "Q06-confirmed",
        "source_sha": case.source_sha,
        "rules": {
            "U07:operation-result": {
                "decision": "denied",
                "status": 403,
                "code": "FORBIDDEN",
                "allowed_fields": ["code", "message"],
            }
        },
    }
    data["policy_sha256"] = sha256(
        json.dumps(data, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return data


def test_positive_diagnostic_control_also_rejects_credentials_in_response_headers(tmp_path):
    case = matrix_case(tmp_path)
    saved = original(case)
    result = httpx.Response(
        200, json=filtered_trace(saved), headers={"X-Control-Note": "secret-maintenance-bearer"}
    )
    with pytest.raises(AssertionError, match="credential leak"):
        assert_safety(result, case, saved, "U01", "trace-detail", ("secret-maintenance-bearer",))


def test_empty_original_trace_can_be_polled_but_cannot_prove_positive_control(tmp_path):
    case = matrix_case(tmp_path)
    saved = original(case)
    page = filtered_trace(saved)
    assert_visible(page, "trace-detail", saved, case, "U01", require_trace_records=False)
    with pytest.raises(AssertionError, match="evidence missing"):
        assert_visible(page, "trace-detail", saved, case, "U01")


@pytest.mark.parametrize("missing", ["file", "Q01", "Q06", "cell"])
def test_missing_record_visibility_or_http_policy_blocks_only_its_cell(tmp_path, missing):
    case = matrix_case(tmp_path)
    data = policy_payload(case)
    if missing in {"Q01", "Q06"}:
        data["q01_evidence_id" if missing == "Q01" else "q06_evidence_id"] = None
    elif missing == "cell":
        data["rules"] = {}
    data.pop("policy_sha256")
    data["policy_sha256"] = sha256(
        json.dumps(data, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    if missing != "file":
        case.policy_file.write_text(json.dumps(data))
    _, rule = read_policy(case, "U07:operation-result")
    assert rule is None
    # Safety remains enforceable before the caller records blocked_requirement.
    with pytest.raises(AssertionError):
        assert_safety(
            response(original(case).pack.model_dump(mode="json")),
            case,
            original(case),
            "U07",
            "operation-result",
            (),
        )


def test_confirmed_policy_is_source_bound_and_not_inferred_from_actual_response(tmp_path):
    case = matrix_case(tmp_path)
    data = policy_payload(case)
    case.policy_file.write_text(json.dumps(data))
    policy, rule = read_policy(case, "U07:operation-result")
    assert policy and rule
    assert_rule(
        response({"code": "FORBIDDEN", "message": "denied"}, 403),
        rule,
        "operation-result",
        original(case),
        case,
        "U07",
    )
    data["rules"]["U07:operation-result"]["status"] = 404
    data["rules"]["U07:operation-result"]["code"] = "NOT_FOUND"
    case.policy_file.write_text(json.dumps(data))
    with pytest.raises(AssertionError):
        read_policy(case, "U07:operation-result")


@pytest.mark.parametrize(
    "fault", ["extra-permission", "other-scope-diag", "two-dimensions", "shared-id"]
)
def test_fixture_must_distinguish_diagnose_scope_and_initiator_ownership(tmp_path, fault):
    raw = matrix_case(tmp_path).model_dump(mode="json")
    if fault == "extra-permission":
        raw["principals"]["U07"]["permissions"].append("memory:read")
    elif fault == "other-scope-diag":
        raw["principals"]["U06"]["home_scope"]["agent_id"] = "foreign"
    elif fault == "two-dimensions":
        raw["principals"]["U02"]["home_scope"]["agent_id"] = "foreign"
    else:
        raw["memories"][1]["memory"]["memory_id"] = raw["memories"][0]["memory"]["memory_id"]
    with pytest.raises(ValueError):
        OwnershipCase.model_validate(raw)


def test_endpoints_use_original_ids_and_no_fabricated_trace_route(tmp_path):
    saved = original(matrix_case(tmp_path))
    assert endpoint("trace-detail", saved) == "/p3/logs/" + saved.trace_id
    assert endpoint("diagnostic-task", saved) == "/p3/tasks/" + saved.job_id
    assert endpoint("operation-result", saved) == "/p3/operations/" + saved.job_id + "/result"
    with pytest.raises(AssertionError):
        assert_metadata({"unexpected": {"input": "sensitive"}})
