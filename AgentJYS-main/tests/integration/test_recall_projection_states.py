"""AET-38 / RC-SRC-07,08: real projection states versus F0 and dependency loss.

Q09 limits only timing/terminal-policy acceptance. State and no-fallback assertions
always run. Missing native/Azure prerequisites are blocked_fixture, never passes.
"""

from uuid import uuid4

import pytest
from tests.integration.test_recall_working_native_search import (
    catalog,
    eventually,
    recall,
    verify_ready,
)
from tests.integration.test_recall_working_native_search import working_target as _working_target

from aether_agent_memory.runtime.contracts.models import ErrorCode
from aether_agent_memory.runtime.foundation.common import FoundationError
from recall_authorization_support import F1_TEXTS, RecallHTTP, assert_denied
from recall_projection_state_support import (
    ProjectionControl,
    ProjectionStateProbe,
    assert_not_success,
    assert_state_observation,
)
from recall_working_search_support import assert_execution

pytestmark = [pytest.mark.integration, pytest.mark.sources, pytest.mark.p0]
working_target = _working_target


def prepare_f7(http, session):
    receipt, job = http.command(
        "/p3/remember",
        {
            "source": {
                "kind": "text",
                "external_id": session,
                "external_version": "1",
                "occurred_at": "2026-10-09T00:00:00.000Z",
            },
            "selection": {"session_id": session},
            "content": {"kind": "text", "text": F1_TEXTS[0]},
        },
        session,
        http.maintainer,
    )
    assert receipt["saved"] and len(receipt["memories"]) == 1
    return receipt["memories"][0], job


def submit(http, session, evidence):
    operation_id = "state-recall-" + uuid4().hex
    response = http.client.post(
        "/p3/recall",
        headers={"Authorization": f"Bearer {http.maintainer}", "X-Operation-ID": operation_id},
        json={
            "query": "用户喝咖啡不加糖",  # Literal match also catches lexical fallback.
            "selection": {"session_id": session},
            "sources": "working",
            "token_budget": 1000,
        },
    )
    evidence.response("POST", "/p3/recall", response)
    lookup = http.get(f"/p3/operation-requests/{operation_id}", params={"kind": "recall.execute"})
    evidence.response("GET", "/p3/operation-requests/{operation_id}", lookup)
    assert lookup.status_code == 200
    job = lookup.json()["job_id"]
    assert job and response.headers.get("X-P3-Job-ID") == job
    assert response.headers.get("Location") == f"/p3/operations/{job}"
    return operation_id, job, response


def diagnose(http, maintainer, job_id, actor, evidence):
    response = http.get(f"/p3/tasks/{job_id}")
    evidence.response("GET", f"/p3/tasks/{job_id}", response)
    if actor == "U01":
        assert_denied(response, 403, "FORBIDDEN")
        response = maintainer.get(f"/p3/tasks/{job_id}")
    assert response.status_code == 200
    assert response.json()["task_id"] == job_id
    return response.json()


@pytest.mark.parametrize("working_target", [{"case_id": "AET-38"}], indirect=True)
@pytest.mark.parametrize("actor", ["U01", "U06"])
@pytest.mark.parametrize("state", ["pending", "failed"], ids=["RC-SRC-07", "RC-SRC-08"])
def test_projection_state_is_observable_without_body_fallback(
    working_target, monkeypatch, actor, state
):
    client, native, vector_probe, evidence = working_target
    runtime = vector_probe.runtime
    probe = ProjectionStateProbe(runtime, monkeypatch)
    maintainer = RecallHTTP(client, "fixture-maintainer")
    http = RecallHTTP(client, actor)
    f0, f7 = "f0-" + uuid4().hex, "f7-" + uuid4().hex
    assert catalog(maintainer, f0) == []
    empty = recall(client, native, vector_probe, evidence, actor, f0, "working", (), "f0")
    assert empty.outcome == "empty" and empty.coverage.working == "complete"
    if state == "pending":
        evidence.blocked(
            "blocked_requirement",
            "Q09: exact wait duration and terminal policy only; state/no-fallback checks run",
        )
    control = ProjectionControl(runtime, f7, state)
    evidence.data["projection_control"] = control.calls
    with control.installed(monkeypatch):
        ref, remember_job = prepare_f7(maintainer, f7)
        eventually(control.entered.is_set)
        projection_job = control.calls[0]["projection_job_id"]
        assert projection_job != remember_job, "save and projection jobs must not be conflated"
        assert all(c["memory"] == ref for c in control.calls)
        assert not any(c["delegated"] for c in control.calls)
        if state == "failed":
            task = maintainer.poll_job(projection_job)
            assert task["state"] in {"failed", "attention_required"}
            assert task["error_code"] == "CONTRACT_VIOLATION", "injected cause not locatable"
        else:
            task_response = maintainer.get(f"/p3/operations/{projection_job}")
            assert task_response.status_code == 200
            task = task_response.json()
            assert task["state"] in {"pending", "running", "retry_wait"}
        snapshot = maintainer.get(f"/p3/remember/{ref['memory_id']}")
        assert snapshot.status_code == 200
        snapshot = snapshot.json()
        rows = [m for m in catalog(maintainer, f7) if m["kind"] == "working"]
        assert len(rows) == 1 and rows[0]["ref"] == ref
        assert snapshot["projection_state"] != "ready"
        proof = http.verify_body(ref, F1_TEXTS[0])
        evidence.data["fixtures"].append(
            {**proof, "projection_state": snapshot["projection_state"], "fixture": "F7-" + state}
        )
        operation_id, recall_job, response = submit(http, f7, evidence)
        execution = {
            "operation_id": operation_id,
            "job_id": recall_job,
            "selected_sources": ["working"],
            "remember_job_id": remember_job,
            "projection_job_id": projection_job,
            "projection_task_state": task["state"],
            "projection_error": task["error_code"],
        }
        evidence.data["executions"].append(execution)
        # Wait for observation, not an arbitrary sleep or a chosen Recall terminal policy.
        eventually(lambda: probe.observe(operation_id, vector_probe)["readiness"])
        execution.update(probe.observe(operation_id, vector_probe))
        assert_state_observation(execution, state)
        execution["native_vector_execution"] = assert_execution(
            vector_probe, operation_id, "用户喝咖啡不加糖", native, "working", ()
        )
        value = assert_not_success(response)
        if state == "pending":
            assert value["code"] == "REQUEST_IN_PROGRESS"
        result = http.get(f"/p3/operations/{recall_job}/result")
        evidence.response("GET", f"/p3/operations/{recall_job}/result", result)
        assert_not_success(result)
        diagnose(http, maintainer, recall_job, actor, evidence)
        projection_diagnostic = maintainer.get(f"/p3/tasks/{projection_job}")
        evidence.response("GET", f"/p3/tasks/{projection_job}", projection_diagnostic)
        assert projection_diagnostic.status_code == 200
        logs = maintainer.get(f"/p3/logs/{control.calls[0]['trace_id']}")
        evidence.response("GET", "/p3/logs/" + control.calls[0]["trace_id"], logs)
        assert logs.status_code == 200
        # READ remains usable while the projection is unavailable.
        http.verify_body(ref, F1_TEXTS[0])
    if state == "pending":
        eventually(
            lambda: any(
                m["ref"] == ref and m["projection_state"] == "ready"
                for m in catalog(maintainer, f7)
            )
        )
        assert any(c["delegated"] for c in control.calls), "barrier never released real publish"
        evidence.data["control_restored"] = True
        # Recovery is a positive control, independent of Q09's original-request policy.
        memory = verify_ready(maintainer, ref, F1_TEXTS[0], native.model_space, "working")
        recall(client, native, vector_probe, evidence, actor, f7, "working", (memory,), "released")
    else:
        evidence.data["control_restored"] = control.released.is_set()
    evidence.data["acceptance"] = "limited" if state == "pending" else "passed"


@pytest.mark.parametrize("working_target", [{"case_id": "AET-38"}], indirect=True)
@pytest.mark.parametrize("actor", ["U01", "U06"])
def test_ready_projection_dependency_failure_is_distinct_and_restorable(
    working_target, monkeypatch, actor
):
    client, native, vector_probe, evidence = working_target
    runtime = vector_probe.runtime
    probe = ProjectionStateProbe(runtime, monkeypatch)
    maintainer, http = RecallHTTP(client, "fixture-maintainer"), RecallHTTP(client, actor)
    session = "dependency-" + uuid4().hex
    proofs = maintainer.prepare_f1(session)
    memories = tuple(
        verify_ready(maintainer, p["ref"], text, native.model_space, "working")
        for p, text in zip(proofs, F1_TEXTS, strict=True)
    )
    recall(client, native, vector_probe, evidence, actor, session, "working", memories, "before")
    original = runtime.vectors.client.search
    attempts = []

    def unavailable(*args, **kwargs):
        attempts.append({"stage": "milvus.search", "error_code": "DEPENDENCY_UNAVAILABLE"})
        raise FoundationError(ErrorCode.DEPENDENCY_UNAVAILABLE, "AET-38 owned vector outage")

    with monkeypatch.context() as patch:
        patch.setattr(runtime.vectors.client, "search", unavailable)
        operation_id, job, response = submit(http, session, evidence)
        evidence.data["dependency_injection"] = attempts
        eventually(lambda: attempts and probe.observe(operation_id, vector_probe)["readiness"])
        observed = probe.observe(operation_id, vector_probe)
        evidence.data["executions"].append(
            {"operation_id": operation_id, "job_id": job, **observed}
        )
        assert all(
            r["complete"] and r["pending_count"] == r["failed_count"] == 0
            for r in observed["readiness"]
        ), "dependency loss incorrectly changed index readiness"
        assert observed["body_read_calls"] == 0
        assert all(r["source"] == "working" for r in observed["routes"])
        assert_not_success(response)
        terminal = http.poll_job(job)
        assert terminal["state"] in {"failed", "attention_required"}
        assert terminal["error_code"] == "DEPENDENCY_UNAVAILABLE"
        result = http.get(f"/p3/operations/{job}/result")
        evidence.response("GET", f"/p3/operations/{job}/result", result)
        assert_not_success(result)
        for proof, text in zip(proofs, F1_TEXTS, strict=True):
            http.verify_body(proof["ref"], text)
        diagnose(http, maintainer, job, actor, evidence)
    assert runtime.vectors.client.search == original
    recall(client, native, vector_probe, evidence, actor, session, "working", memories, "restored")
    evidence.data.update(acceptance="passed", control_restored=True)
