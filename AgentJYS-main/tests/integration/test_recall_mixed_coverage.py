"""AET-39 / RC-SRC-09: mixed states inside one selected source, via real HTTP.

Safety checks run for both requested policy profiles even when Q09 is unresolved.
Approved profiles come only from P3_RECALL_MIXED_POLICY_FILE, never the request.
"""

from hashlib import sha256
from uuid import uuid4

import pytest
from tests.integration.test_recall_projection_states import diagnose
from tests.integration.test_recall_working_native_search import (
    catalog,
    eventually,
    recall,
    verify_ready,
)
from tests.integration.test_recall_working_native_search import working_target as _working_target

from aether_agent_memory.remember.contracts.models import MemoryRef
from aether_agent_memory.runtime.contracts.models import TaskOperationView
from recall_authorization_support import F1_TEXTS, RecallHTTP
from recall_mixed_coverage_support import (
    MixedCoverageProbe,
    assert_mixed_observation,
    assert_mixed_pack,
)
from recall_projection_state_support import ProjectionControl, assert_not_success
from recall_working_search_support import assert_execution

pytestmark = [pytest.mark.integration, pytest.mark.sources, pytest.mark.p1]
working_target = _working_target
QUERY = "用户喝咖啡不加糖，也喜欢乌龙茶。"
ARCHIVED_TEXT = "用户喝咖啡不加糖，也喜欢红茶。"


def remember(http, session, text):
    operation_id = "mixed-save-" + uuid4().hex
    receipt, job = http.command(
        "/p3/remember",
        {
            "source": {
                "kind": "text",
                "external_id": operation_id,
                "external_version": "1",
                "occurred_at": "2026-10-09T00:00:00.000Z",
            },
            "selection": {"session_id": session},
            "content": {"kind": "text", "text": text},
        },
        operation_id,
        http.maintainer,
    )
    assert receipt["saved"] and len(receipt["memories"]) == 1
    return receipt["memories"][0], job


def wait_ready(http, session, ref, text, native):
    eventually(
        lambda: any(
            row["ref"] == ref and row["projection_state"] == "ready"
            for row in catalog(http, session)
        )
    )
    return verify_ready(http, ref, text, native.model_space, "working")


def submit_mixed(http, session, evidence):
    operation_id = "mixed-recall-" + uuid4().hex
    response = http.client.post(
        "/p3/recall",
        headers={"Authorization": f"Bearer {http.maintainer}", "X-Operation-ID": operation_id},
        json={
            "query": QUERY,
            "selection": {"session_id": session},
            "sources": "working",
            "token_budget": 4096,
        },
    )
    evidence.response("POST", "/p3/recall", response)
    lookup = http.get(f"/p3/operation-requests/{operation_id}", params={"kind": "recall.execute"})
    evidence.response("GET", f"/p3/operation-requests/{operation_id}", lookup)
    assert lookup.status_code == 200 and lookup.json()["state"] == "found"
    job = lookup.json()["job_id"]
    assert response.headers.get("X-P3-Job-ID") == job
    assert response.headers.get("Location") == f"/p3/operations/{job}"
    return operation_id, job, response


@pytest.mark.parametrize(
    "working_target",
    [{"case_id": "AET-39", "mixed_policy": mode} for mode in ("allow", "deny")],
    indirect=True,
    ids=["partial-allowed", "partial-forbidden"],
)
@pytest.mark.parametrize("actor", ["U01", "U06"])
@pytest.mark.parametrize("state", ["pending", "failed"], ids=["Ready-pending", "Ready-failed"])
def test_rc_src_09_same_source_mixed_coverage(working_target, monkeypatch, actor, state):
    client, native, vectors, evidence = working_target
    runtime = vectors.runtime
    probe = MixedCoverageProbe(runtime, monkeypatch)
    maintainer, http = RecallHTTP(client, "fixture-maintainer"), RecallHTTP(client, actor)
    session = "mixed-" + uuid4().hex
    policy = evidence.data["mixed_policy"]
    policy["actual_policy_version"] = runtime.recall.policy_version
    if policy["confirmed"]:
        assert policy["expected_policy_version"] == runtime.recall.policy_version, (
            "approved policy does not match the installed target version"
        )
    else:
        evidence.blocked("blocked_requirement", policy["restriction"])

    # Ready is prepared before installing the session-scoped publication barrier.
    ready_ref, _ = remember(maintainer, session, F1_TEXTS[1])
    ready = wait_ready(maintainer, session, ready_ref, F1_TEXTS[1], native)
    archived_ref, _ = remember(maintainer, session, ARCHIVED_TEXT)
    wait_ready(maintainer, session, archived_ref, ARCHIVED_TEXT, native)
    archived = client.post(
        f"/p3/remember/{archived_ref['memory_id']}/lifecycle",
        headers={
            "Authorization": "Bearer fixture-maintainer",
            "X-Operation-ID": "archive-" + uuid4().hex,
        },
        json={
            "expected_version": archived_ref["version"],
            "target": "archived",
            "reason": "AET-39 excluded Working distractor",
        },
    )
    evidence.response("POST", "/p3/remember/lifecycle", archived)
    assert archived.status_code == 200
    outside = "outside-" + uuid4().hex
    outside_ref, _ = remember(maintainer, outside, F1_TEXTS[0])
    wait_ready(maintainer, outside, outside_ref, F1_TEXTS[0], native)
    control_pack = recall(
        client,
        native,
        vectors,
        evidence,
        actor,
        session,
        "working",
        (ready,),
        "ready-control",
        query=QUERY,
        discovery_refs=(MemoryRef.model_validate(archived_ref),),
    )
    policy["control_result_policy_version"] = control_pack.policy_version
    assert control_pack.policy_version == runtime.recall.policy_version
    evidence.data["fixtures"].extend(
        [
            {
                "fixture": "Ready",
                "ref": ready_ref,
                "projection_state": "ready",
                "body_hash": ready.content_hash,
            },
            {"fixture": "archived", "ref": archived_ref},
            {"fixture": "outside-selection", "ref": outside_ref},
        ]
    )

    control = ProjectionControl(runtime, session, state)
    evidence.data["projection_control"] = control.calls
    frozen = None
    with control.installed(monkeypatch):
        missing_ref, save_job = remember(maintainer, session, F1_TEXTS[0])
        assert missing_ref != ready_ref
        eventually(control.entered.is_set)
        assert all(c["memory"] == missing_ref for c in control.calls)
        projection_job = control.calls[0]["projection_job_id"]
        if state == "failed":
            failed_task = maintainer.poll_job(projection_job)
            assert failed_task["state"] in {"failed", "attention_required"}
            assert failed_task["error_code"] == "CONTRACT_VIOLATION"
        maintainer.verify_body(missing_ref, F1_TEXTS[0])
        http.verify_body(ready_ref, ready.content)
        active = [
            row
            for row in catalog(maintainer, session)
            if row["kind"] == "working" and row["status"] == "active"
        ]
        assert {row["ref"]["memory_id"] for row in active} == {
            ready_ref["memory_id"],
            missing_ref["memory_id"],
        }
        evidence.data["fixtures"].append(
            {
                "fixture": "F7-" + state,
                "ref": missing_ref,
                "body_hash": sha256(F1_TEXTS[0].encode()).hexdigest(),
                "projection_state": next(
                    row["projection_state"] for row in active if row["ref"] == missing_ref
                ),
            }
        )
        operation_id, job, response = submit_mixed(http, session, evidence)
        execution = {
            "operation_id": operation_id,
            "job_id": job,
            "selected_sources": ["working"],
            "projection_job_id": projection_job,
            "remember_job_id": save_job,
        }
        evidence.data["executions"].append(execution)
        eventually(lambda: probe.observe(operation_id, vectors)["readiness"])
        # The harness timeout only awaits a real search observation, not Q09's terminal policy.
        eventually(
            lambda: any(
                row["operation_id"] == operation_id and row["result"] is not None
                for row in vectors.searches
            )
        )
        execution.update(probe.observe(operation_id, vectors))
        assert_mixed_observation(execution, state, ready)
        execution["native_vector_execution"] = assert_execution(
            vectors,
            operation_id,
            QUERY,
            native,
            "working",
            (ready.ref,),
            (MemoryRef.model_validate(archived_ref),),
        )
        if policy["confirmed"] and policy["requested_mode"] == "allow":
            assert http.poll_job(job)["state"] == "succeeded"
        elif policy["confirmed"] and policy["terminal_policy"] == "reject":
            assert http.poll_job(job)["state"] in {"failed", "attention_required"}
        result = http.get(f"/p3/operations/{job}/result")
        evidence.response("GET", f"/p3/operations/{job}/result", result)
        if response.status_code == 200:
            assert result.status_code == 200 and result.json() == response.json(), (
                "synchronous Pack differs from its original durable result"
            )
        for observed_response in (response, result):
            if observed_response.status_code == 200:
                assert not (policy["confirmed"] and policy["requested_mode"] == "deny"), (
                    "partial Pack delivered under approved deny policy"
                )
                pack = assert_mixed_pack(
                    observed_response.json(),
                    ready,
                    {"ref": missing_ref, "text": F1_TEXTS[0]},
                    state,
                    runtime.recall.policy_version,
                )
                if frozen is not None:
                    assert observed_response.json() == frozen, "original committed Pack changed"
                frozen = observed_response.json()
                execution.update(
                    coverage=pack.coverage.model_dump(),
                    outcome=pack.outcome,
                    degradation_reasons=list(pack.degradation_reasons),
                    delivered_refs=[ready_ref],
                    policy_version=pack.policy_version,
                )
            else:
                assert_not_success(observed_response)
                assert observed_response.json()["code"] in {
                    "REQUEST_IN_PROGRESS",
                    "DEPENDENCY_UNAVAILABLE",
                    "DEADLINE_EXCEEDED",
                }, "mixed projection status was hidden by an unrelated error"
        if policy["confirmed"] and policy["requested_mode"] == "allow":
            assert frozen is not None, "approved allow policy did not deliver a valid partial Pack"
        task_response = http.get(f"/p3/operations/{job}")
        evidence.response("GET", f"/p3/operations/{job}", task_response)
        assert task_response.status_code == 200
        task = TaskOperationView.model_validate(task_response.json())
        execution.update(
            task_state=task.state.value,
            error_code=task.error_code.value if task.error_code else None,
        )
        if policy["confirmed"] and policy["terminal_policy"] == "wait":
            assert task.state.value in {"pending", "running", "retry_wait", "recovery_wait"}
        diagnose(http, maintainer, job, actor, evidence)
        logs = maintainer.get(f"/p3/logs/{execution['native_vector_execution']['trace_id']}")
        evidence.response(
            "GET", "/p3/logs/" + execution["native_vector_execution"]["trace_id"], logs
        )
        assert logs.status_code == 200
        assert not any(c["delegated"] for c in control.calls)
        # A partial body read must not make pending/failed data look Ready.
        http.verify_body(missing_ref, F1_TEXTS[0])
    evidence.data["control_restored"] = control.released.is_set()
    if state == "pending":
        recovered = wait_ready(maintainer, session, missing_ref, F1_TEXTS[0], native)
        recall(
            client,
            native,
            vectors,
            evidence,
            actor,
            session,
            "working",
            (ready, recovered),
            "mixed-released",
            query=QUERY,
        )
        if frozen is not None:
            replay = http.get(f"/p3/operations/{job}/result")
            assert replay.status_code == 200 and replay.json() == frozen
    terminal_restricted = not policy["confirmed"] or (
        policy["requested_mode"] == "deny" and policy["terminal_policy"] is None
    )
    if terminal_restricted and policy["confirmed"]:
        evidence.blocked(
            "blocked_requirement", "Q09: deny wait/rejection terminal policy unspecified"
        )
    evidence.data["acceptance"] = "limited" if terminal_restricted else "passed"
