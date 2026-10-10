"""AET-41 / RC-SRC-11: server-required sources forbid any partial success.

This lane does not import or run the allow-degradation ticket's scenarios.
Missing strict capability remains a product gap; no request-body policy override.
"""

from uuid import uuid4

import pytest
from tests.integration.test_recall_long_term_native_search import ready_long_term
from tests.integration.test_recall_projection_states import diagnose
from tests.integration.test_recall_working_native_search import recall, verify_ready
from tests.integration.test_recall_working_native_search import working_target as _working_target

from aether_agent_memory.recall.contracts.models import ContextPack
from recall_authorization_support import F1_TEXTS, RecallHTTP
from recall_both_sources_support import assert_both_execution
from recall_projection_state_support import assert_not_success
from recall_required_sources_support import AccessEventWitness, assert_failed_consistently
from recall_source_failure_support import SourceFault, assert_fault_execution

pytestmark = [pytest.mark.integration, pytest.mark.sources, pytest.mark.p1]
working_target = _working_target
QUERY = "用户喝咖啡加糖吗，喜欢什么茶？"


def request(http, session, evidence):
    operation_id = "required-sources-" + uuid4().hex
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
    evidence.response("GET", f"/p3/operation-requests/{operation_id}", lookup)
    assert lookup.status_code == 200 and lookup.json()["state"] == "found"
    job = lookup.json()["job_id"]
    assert response.headers.get("X-P3-Job-ID") == job
    assert response.headers.get("Location") == f"/p3/operations/{job}"
    return operation_id, job, response


def positive(http, session, memories, native, vectors, witness, evidence):
    operation_id, job, response = request(http, session, evidence)
    task = http.poll_job(job)
    result = http.get(f"/p3/operations/{job}/result")
    evidence.response("GET", f"/p3/operations/{job}/result", result)
    assert task["state"] == "succeeded" and result.status_code == 200
    if response.status_code == 200:
        assert result.json() == response.json()
    pack = ContextPack.model_validate(result.json())
    assert pack.selected_sources == ("working", "long_term")
    assert pack.coverage.working == pack.coverage.long_term == "complete"
    assert pack.outcome == "available" and not pack.degradation_reasons
    assert pack.policy_version == vectors.runtime.recall.policy_version
    items = [item for group in pack.groups for item in group.items]
    assert len(items) == vectors.runtime.recall.settings.max_items == 2
    legal = {memory.ref: memory for values in memories.values() for memory in values}
    for item in items:
        assert item.memory in legal
        memory = legal[item.memory]
        assert item.content == memory.content and item.sources == memory.sources
        assert item.representation == "original" and item.content in pack.rendered_context
    routes = assert_both_execution(
        vectors,
        operation_id,
        QUERY,
        native,
        {source: tuple(m.ref for m in values) for source, values in memories.items()},
    )
    events = witness.observe(operation_id)
    assert events["packed_committed_count"] == len(items), "positive event witness is not working"
    packed_refs = [
        row["memory"]
        for row in events["committed_events"]
        if row["stage"] == "packed" and row["outcome"] == "succeeded"
    ]
    assert all(item.memory.model_dump(mode="json") in packed_refs for item in items)
    evidence.data["executions"].append(
        {
            "operation_id": operation_id,
            "job_id": job,
            "recall_id": pack.recall_id,
            "outcome": pack.outcome,
            "coverage": pack.coverage.model_dump(),
            "policy_version": pack.policy_version,
            "routes": routes,
            "events": events,
        }
    )
    return pack


@pytest.mark.parametrize(
    "working_target",
    [{"case_id": "AET-41", "strict_policy": True, "max_items": 2}],
    indirect=True,
)
@pytest.mark.parametrize("actor", ["U01", "U06"])
@pytest.mark.parametrize("failed", ["working", "long_term"])
@pytest.mark.parametrize("kind", ["timeout", "unavailable"])
def test_rc_src_11_required_source_fault_has_no_pack_or_packed_event(
    working_target, monkeypatch, actor, failed, kind
):
    client, native, vectors, evidence = working_target
    runtime = vectors.runtime
    witness = AccessEventWitness(runtime, monkeypatch)
    policy = evidence.data["strict_policy"]
    policy["actual_policy_version"] = runtime.recall.policy_version
    if policy["confirmed"]:
        assert policy["expected_policy_version"] == runtime.recall.policy_version
    else:
        evidence.blocked("blocked_requirement", policy["restriction"])
    assert runtime.recall.settings.max_items == 2, "fixture must fill the server's item limit"
    maintainer, http = RecallHTTP(client, "fixture-maintainer"), RecallHTTP(client, actor)
    session = "required-" + uuid4().hex
    proofs = maintainer.prepare_f1(session)
    memories = {
        "working": tuple(
            verify_ready(maintainer, proof["ref"], text, native.model_space, "working")
            for proof, text in zip(proofs, F1_TEXTS, strict=True)
        ),
        "long_term": ready_long_term(maintainer, session, F1_TEXTS, native),
    }
    evidence.data["fixtures"] = [
        {
            "ref": memory.ref.model_dump(mode="json"),
            "kind": memory.kind,
            "body_hash": memory.content_hash,
            "projection_state": memory.projection_state,
        }
        for values in memories.values()
        for memory in values
    ]
    healthy = "long_term" if failed == "working" else "working"
    # The healthy source alone can fill the deployed result limit with complete bodies.
    healthy_pack = recall(
        client,
        native,
        vectors,
        evidence,
        actor,
        session,
        healthy,
        memories[healthy],
        "healthy-enough",
        query=QUERY,
    )
    assert (
        sum(len(group.items) for group in healthy_pack.groups) == runtime.recall.settings.max_items
    )
    positive(http, session, memories, native, vectors, witness, evidence)
    fault = SourceFault(runtime, session, failed, kind)
    evidence.data["source_faults"] = fault.attempts
    original_search, original_sdk = runtime.vectors.search, runtime.vectors.client.search
    with fault.installed(monkeypatch):
        operation_id, job, response = request(http, session, evidence)
        terminal = http.poll_job(job)
        status = http.get(f"/p3/operations/{job}")
        assert status.status_code == 200
        recall_id = status.json()["subject"]["object_id"]
        record = http.get(f"/p3/recalls/{recall_id}")
        operation_result = http.get(f"/p3/operations/{job}/result")
        recall_result = http.get(f"/p3/recalls/{recall_id}/result")
        for path, reply in (
            (f"/p3/operations/{job}", status),
            (f"/p3/recalls/{recall_id}", record),
            (f"/p3/operations/{job}/result", operation_result),
            (f"/p3/recalls/{recall_id}/result", recall_result),
        ):
            evidence.response("GET", path, reply)
        events = witness.observe(operation_id)
        observed = assert_fault_execution(
            vectors, fault, operation_id, QUERY, native, tuple(m.ref for m in memories[healthy])
        )
        execution = {
            "operation_id": operation_id,
            "job_id": job,
            "recall_id": recall_id,
            "selected_sources": ["working", "long_term"],
            "failed_source": failed,
            "task_state": terminal["state"],
            "error_code": terminal["error_code"],
            "result_ref": terminal["result_ref"],
            "routes": observed,
            "events": events,
            "policy_version": runtime.recall.policy_version,
            "record": record.json() if record.status_code == 200 else None,
        }
        evidence.data["executions"].append(execution)
        # Record all observations before asserting: an incorrectly committed Pack must be visible.
        assert record.status_code == 200
        assert_failed_consistently(status.json(), record.json(), events, job)
        for reply in (response, operation_result, recall_result):
            assert_not_success(reply)
        assert operation_result.status_code == 503
        assert operation_result.json()["code"] == "DEPENDENCY_UNAVAILABLE"
        assert recall_result.json()["code"] == "DEPENDENCY_UNAVAILABLE"
        diagnose(http, maintainer, job, actor, evidence)
        trace_id = observed["healthy"]["trace_id"]
        logs = maintainer.get(f"/p3/logs/{trace_id}")
        evidence.response("GET", f"/p3/logs/{trace_id}", logs)
        assert logs.status_code == 200
    assert (
        runtime.vectors.search == original_search and runtime.vectors.client.search == original_sdk
    )
    # Recovery uses a new operation. It must not turn the failed operation into success.
    positive(http, session, memories, native, vectors, witness, evidence)
    replay = http.get(f"/p3/operations/{job}/result")
    assert replay.status_code == 503 and replay.json()["code"] == "DEPENDENCY_UNAVAILABLE"
    assert witness.observe(operation_id) == events
    evidence.data.update(
        control_restored=True, acceptance="passed" if policy["confirmed"] else "limited"
    )
