"""AET-40 / RC-SRC-10,13: real both-source degradation and F0 contrast."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from tests.integration.test_recall_long_term_native_search import ready_long_term
from tests.integration.test_recall_mixed_coverage import remember, wait_ready
from tests.integration.test_recall_projection_states import diagnose
from tests.integration.test_recall_working_native_search import eventually
from tests.integration.test_recall_working_native_search import working_target as _working_target

from recall_authorization_support import F1_TEXTS, RecallHTTP, assert_denied
from recall_both_sources_support import assert_both_execution, assert_both_pack
from recall_long_term_search_support import REVIEW_TEXT
from recall_mixed_coverage_support import MixedCoverageProbe
from recall_projection_state_support import assert_not_success
from recall_source_failure_support import (
    SourceFault,
    assert_both_empty,
    assert_fault_execution,
    assert_partial_pack,
)
from recall_working_search_support import assert_execution

pytestmark = [pytest.mark.integration, pytest.mark.sources]
working_target = _working_target
QUERY = "用户喝咖啡的习惯和项目评审安排是什么？"
FOREIGN_TEXT = "用户喜欢乌龙茶，每周参加内部项目评审。"
OPTIONS = {"case_id": "AET-40", "mixed_policy": "allow", "foreign_owner": True}


def seed(http, session, texts, native):
    working = []
    for text in texts:
        ref, _ = remember(http, session, text)
        working.append(wait_ready(http, session, ref, text, native))
    long_term = ready_long_term(http, session, texts, native)
    return {"working": tuple(working), "long_term": long_term}


def submit(http, session, evidence):
    operation_id = "source-fault-" + uuid4().hex
    response = http.client.post(
        "/p3/recall",
        headers={"Authorization": f"Bearer {http.maintainer}", "X-Operation-ID": operation_id},
        json={
            "query": QUERY,
            "selection": {"session_id": session},
            "sources": "both",
            "token_budget": 4096,
        },
    )
    evidence.response("POST", "/p3/recall", response)
    lookup = http.get(f"/p3/operation-requests/{operation_id}", params={"kind": "recall.execute"})
    assert lookup.status_code == 200 and lookup.json()["state"] == "found"
    job = lookup.json()["job_id"]
    assert response.headers.get("X-P3-Job-ID") == job
    assert response.headers.get("Location") == f"/p3/operations/{job}"
    evidence.response("GET", f"/p3/operation-requests/{operation_id}", lookup)
    return operation_id, job, response


def result(http, job, response, evidence):
    task = http.poll_job(job)
    reply = http.get(f"/p3/operations/{job}/result")
    evidence.response("GET", f"/p3/operations/{job}/result", reply)
    payload = reply.json()
    evidence.data.setdefault("operation_results", []).append(
        {
            "job_id": job,
            "task_state": task["state"],
            "error_code": task["error_code"],
            "status": reply.status_code,
            "outcome": payload.get("outcome"),
            "selected_sources": payload.get("selected_sources"),
            "coverage": payload.get("coverage"),
            "degradation_reasons": payload.get("degradation_reasons"),
            "code": payload.get("code"),
        }
    )
    if response.status_code == 200:
        assert reply.status_code == 200 and reply.json() == response.json()
    return task, reply


def policy_evidence(runtime, evidence):
    policy = evidence.data["mixed_policy"]
    policy["actual_policy_version"] = runtime.recall.policy_version
    if policy["confirmed"]:
        assert policy["expected_policy_version"] == runtime.recall.policy_version
    else:
        evidence.blocked("blocked_requirement", policy["restriction"])
    return policy


def positive(http, session, memories, native, probe, evidence, actor):
    operation_id, job, response = submit(http, session, evidence)
    task, reply = result(http, job, response, evidence)
    assert task["state"] == "succeeded" and reply.status_code == 200
    pack = assert_both_pack(reply.json(), (*memories["working"], *memories["long_term"]))
    execution = assert_both_execution(
        probe,
        operation_id,
        QUERY,
        native,
        {source: tuple(m.ref for m in values) for source, values in memories.items()},
    )
    evidence.data["executions"].append(
        {
            "operation_id": operation_id,
            "job_id": job,
            "coverage": pack.coverage.model_dump(),
            "outcome": pack.outcome,
            "routes": execution,
        }
    )
    diagnose(http, RecallHTTP(http.client, "fixture-maintainer"), job, actor, evidence)
    return pack


def expire(http, memories, evidence):
    expiry = (
        (datetime.now(UTC) + timedelta(seconds=15))
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )
    for memory in memories:
        snapshot = http.get(f"/p3/remember/{memory.ref.memory_id}")
        assert snapshot.status_code == 200
        response = http.client.post(
            f"/p3/remember/{memory.ref.memory_id}/retention",
            headers={
                "Authorization": f"Bearer {http.maintainer}",
                "X-Operation-ID": "expire-" + uuid4().hex,
            },
            json={
                "expected_version": memory.ref.version,
                "expected_object_revision": snapshot.json()["object_revision"],
                "enabled": False,
                "completed": True,
                "expires_at": expiry,
                "reason": "AET-40 explicit expired candidate",
            },
        )
        evidence.response("POST", f"/p3/remember/{memory.ref.memory_id}/retention", response)
        assert response.status_code == 200
    eventually(lambda: datetime.now(UTC) >= datetime.fromisoformat(expiry.replace("Z", "+00:00")))
    for memory in memories:
        response = http.client.post(
            "/p3/remember/body",
            json=memory.ref.model_dump(mode="json"),
            headers={"Authorization": f"Bearer {http.maintainer}"},
        )
        assert response.status_code == 200
        assert response.json()["outcome"] == "excluded" and response.json()["content"] is None
        assert response.json()["reason_code"] == "expired"
        evidence.data["fixtures"].append(
            {
                "fixture": "expired",
                "ref": memory.ref.model_dump(mode="json"),
                "body_hash": memory.content_hash,
                "expires_at": expiry,
            }
        )


@pytest.mark.p0
@pytest.mark.parametrize("working_target", [OPTIONS], indirect=True)
@pytest.mark.parametrize("actor", ["U01", "U06"])
@pytest.mark.parametrize("failed", ["working", "long_term"])
@pytest.mark.parametrize("kind", ["timeout", "unavailable"])
def test_rc_src_10_single_source_fault_delivers_only_safe_complete_material(
    working_target, monkeypatch, actor, failed, kind
):
    client, native, probe, evidence = working_target
    runtime = probe.runtime
    policy = policy_evidence(runtime, evidence)
    paths = MixedCoverageProbe(runtime, monkeypatch)
    maintainer, http = RecallHTTP(client, "fixture-maintainer"), RecallHTTP(client, actor)
    session = "partial-" + uuid4().hex
    all_memories = seed(maintainer, session, (F1_TEXTS[0], REVIEW_TEXT), native)
    positive(http, session, all_memories, native, probe, evidence, actor)
    expired = tuple(
        m for values in all_memories.values() for m in values if m.content == REVIEW_TEXT
    )
    expire(maintainer, expired, evidence)
    memories = {
        source: tuple(m for m in values if m.content == F1_TEXTS[0])
        for source, values in all_memories.items()
    }
    foreign = seed(RecallHTTP(client, "foreign-owner"), session, (FOREIGN_TEXT,), native)
    foreign_memories = (*foreign["working"], *foreign["long_term"])
    for memory in foreign_memories:
        denial = client.post(
            "/p3/remember/body",
            json=memory.ref.model_dump(mode="json"),
            headers={"Authorization": f"Bearer {actor}"},
        )
        assert_denied(denial, 403, "FORBIDDEN")
        evidence.data["fixtures"].append(
            {
                "fixture": "restricted-owner",
                "ref": memory.ref.model_dump(mode="json"),
                "body_hash": memory.content_hash,
                "projection_state": "ready",
            }
        )
    fault = SourceFault(runtime, session, failed, kind)
    evidence.data["source_faults"] = fault.attempts
    healthy = "long_term" if failed == "working" else "working"
    forbidden = (*memories[failed], *expired, *foreign_memories)
    original_search, original_sdk = runtime.vectors.search, runtime.vectors.client.search
    with fault.installed(monkeypatch):
        operation_id, job, response = submit(http, session, evidence)
        task, reply = result(http, job, response, evidence)
        assert task["state"] == "succeeded" and reply.status_code == 200
        pack = assert_partial_pack(
            reply.json(), memories[healthy], failed, forbidden, runtime.recall.policy_version
        )
        # Same plaintext may legitimately exist in both sources: exact Refs decide attribution.
        observed = assert_fault_execution(
            probe,
            fault,
            operation_id,
            QUERY,
            native,
            tuple(m.ref for m in memories[healthy]),
            tuple(m.ref for m in expired if (m.kind == "working") == (healthy == "working")),
        )
        body_paths = paths.observe(operation_id, probe)
        assert body_paths["working_enumerations"] == 0
        assert all(
            ref in [m.ref.model_dump(mode="json") for m in memories[healthy]]
            for ref in body_paths["body_refs"]
        )
        evidence.data["executions"].append(
            {
                "operation_id": operation_id,
                "job_id": job,
                "coverage": pack.coverage.model_dump(),
                "outcome": pack.outcome,
                "degradation_reasons": list(pack.degradation_reasons),
                "policy_version": pack.policy_version,
                "routes": observed,
                "body_paths": body_paths,
            }
        )
        diagnose(http, maintainer, job, actor, evidence)
        for memory in memories[failed]:
            http.verify_body(memory.ref.model_dump(mode="json"), memory.content)
        frozen = reply.json()
    assert (
        runtime.vectors.search == original_search and runtime.vectors.client.search == original_sdk
    )
    # Restore both real routes and prove the fault did not damage projection or stored Pack.
    restored_op, restored_job, restored_response = submit(http, session, evidence)
    restored_task, restored_result = result(http, restored_job, restored_response, evidence)
    assert restored_task["state"] == "succeeded" and restored_result.status_code == 200
    assert_both_pack(restored_result.json(), (*memories["working"], *memories["long_term"]))
    for source in ("working", "long_term"):
        lane = SimpleNamespace(
            embeddings=probe.embeddings,
            searches=[r for r in probe.searches if r["request"].memory_source == source],
        )
        assert_execution(
            lane,
            restored_op,
            QUERY,
            native,
            source,
            tuple(m.ref for m in memories[source]),
            tuple(m.ref for m in expired if (m.kind == "working") == (source == "working")),
        )
    replay = http.get(f"/p3/operations/{job}/result")
    assert replay.status_code == 200 and replay.json() == frozen
    evidence.data.update(
        control_restored=True, acceptance="passed" if policy["confirmed"] else "limited"
    )


@pytest.mark.p1
@pytest.mark.parametrize("working_target", [OPTIONS], indirect=True)
@pytest.mark.parametrize("actor", ["U01", "U06"])
@pytest.mark.parametrize("failed", ["working", "long_term"])
@pytest.mark.parametrize("kind", ["timeout", "unavailable"])
def test_rc_src_13_f0_normal_empty_differs_from_empty_plus_source_failure(
    working_target, monkeypatch, actor, failed, kind
):
    client, native, probe, evidence = working_target
    runtime = probe.runtime
    policy = policy_evidence(runtime, evidence)
    paths = MixedCoverageProbe(runtime, monkeypatch)
    maintainer, http = RecallHTTP(client, "fixture-maintainer"), RecallHTTP(client, actor)
    # A separate positive scope proves storage is nonempty; F0 is selection-local.
    populated = "positive-" + uuid4().hex
    memories = seed(maintainer, populated, (F1_TEXTS[0],), native)
    positive(http, populated, memories, native, probe, evidence, actor)
    session = "f0-" + uuid4().hex
    operation_id, job, response = submit(http, session, evidence)
    task, reply = result(http, job, response, evidence)
    assert task["state"] == "succeeded" and reply.status_code == 200
    empty = assert_both_empty(reply.json(), runtime.recall.policy_version)
    routes = assert_both_execution(
        probe, operation_id, QUERY, native, {"working": (), "long_term": ()}
    )
    evidence.data["executions"].append(
        {
            "operation_id": operation_id,
            "job_id": job,
            "outcome": empty.outcome,
            "coverage": empty.coverage.model_dump(),
            "routes": routes,
            "fixture": "F0",
        }
    )
    fault = SourceFault(runtime, session, failed, kind)
    evidence.data["source_faults"] = fault.attempts
    with fault.installed(monkeypatch):
        failed_op, failed_job, failed_response = submit(http, session, evidence)
        failed_task, failed_result = result(http, failed_job, failed_response, evidence)
        assert_not_success(failed_response)
        assert_not_success(failed_result)
        assert failed_task["state"] in {"failed", "attention_required"}
        assert failed_task["error_code"] == "DEPENDENCY_UNAVAILABLE"
        assert failed_result.json()["code"] == "DEPENDENCY_UNAVAILABLE"
        routes = assert_fault_execution(probe, fault, failed_op, QUERY, native, ())
        body_paths = paths.observe(failed_op, probe)
        assert body_paths["working_enumerations"] == 0 and not body_paths["body_refs"]
        evidence.data["executions"].append(
            {
                "operation_id": failed_op,
                "job_id": failed_job,
                "fixture": "F0+fault",
                "outcome": "error",
                "error_code": failed_task["error_code"],
                "routes": routes,
                "body_paths": body_paths,
            }
        )
        diagnose(http, maintainer, failed_job, actor, evidence)
        # Fault scoping is executable: the populated session still searches both sources.
        positive(http, populated, memories, native, probe, evidence, actor)
    restored_op, restored_job, restored_response = submit(http, session, evidence)
    restored_task, restored_result = result(http, restored_job, restored_response, evidence)
    assert restored_task["state"] == "succeeded" and restored_result.status_code == 200
    assert_both_empty(restored_result.json(), runtime.recall.policy_version)
    assert_both_execution(probe, restored_op, QUERY, native, {"working": (), "long_term": ()})
    evidence.data.update(
        control_restored=True, acceptance="passed" if policy["confirmed"] else "limited"
    )
