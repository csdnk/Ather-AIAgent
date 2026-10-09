"""AET-36 real both-source execution; no controlled ANN results in this lane."""

from uuid import uuid4

import pytest
from tests.integration.test_recall_long_term_native_search import ready_long_term, remember_review
from tests.integration.test_recall_working_native_search import (
    verify_ready,
)
from tests.integration.test_recall_working_native_search import (
    working_target as _working_target,
)

from recall_authorization_support import F1_TEXTS, RecallHTTP, assert_denied
from recall_both_sources_support import SOURCES, assert_both_execution, assert_both_pack
from recall_long_term_search_support import REVIEW_TEXT

pytestmark = [pytest.mark.integration, pytest.mark.sources, pytest.mark.p0]
working_target = _working_target
QUERY = "用户的饮食偏好和项目评审安排是什么？"


@pytest.mark.parametrize("working_target", [{"case_id": "AET-36"}], indirect=True)
@pytest.mark.parametrize("actor", ["U01", "U06"])
def test_both_plans_execute_real_native_search_and_report_matching_coverage(working_target, actor):
    client, native, probe, evidence = working_target
    maintainer = RecallHTTP(client, "fixture-maintainer")
    session = "both-" + uuid4().hex
    proofs = maintainer.prepare_f1(session)
    receipt = remember_review(maintainer, session)
    working = tuple(
        verify_ready(maintainer, proof["ref"], text, native.model_space, "working")
        for proof, text in zip(proofs, F1_TEXTS, strict=True)
    )
    # Wait for the real F2 Working input too, using the public catalog barrier.
    from tests.integration.test_recall_working_native_search import catalog, eventually

    f2_ref = receipt["memories"][0]
    eventually(
        lambda: any(
            m["ref"] == f2_ref and m["projection_state"] == "ready"
            for m in catalog(maintainer, session)
        )
    )
    working += (verify_ready(maintainer, f2_ref, REVIEW_TEXT, native.model_space, "working"),)
    long_term = ready_long_term(maintainer, session, (*F1_TEXTS, REVIEW_TEXT), native)
    memories = (*working, *long_term)
    evidence.data["fixtures"] = [
        {
            "ref": m.ref.model_dump(mode="json"),
            "kind": m.kind,
            "body_hash": m.content_hash,
            "projection_state": m.projection_state,
        }
        for m in memories
    ]
    assert probe.runtime.recall.settings.rerank_policy == "disabled"
    operation_id = "both-" + uuid4().hex
    http = RecallHTTP(client, actor)
    payload, job_id = http.command(
        "/p3/recall",
        {
            "query": QUERY,
            "selection": {"session_id": session},
            "sources": "both",
            "token_budget": 4096,
        },
        operation_id,
        actor,
    )
    pack = assert_both_pack(payload, memories)
    observed = assert_both_execution(
        probe,
        operation_id,
        QUERY,
        native,
        {"working": tuple(m.ref for m in working), "long_term": tuple(m.ref for m in long_term)},
    )
    result = http.get(f"/p3/recalls/{pack.recall_id}/result")
    assert result.status_code == 200 and result.json() == payload
    task = http.get(f"/p3/tasks/{job_id}")
    if actor == "U01":
        assert_denied(task, 403, "FORBIDDEN")
    else:
        assert task.status_code == 200
        assert task.json()["task_id"] == job_id and task.json()["initiator_id"] == actor
        trace_id = observed["working"]["trace_id"]
        logs = http.get(f"/p3/logs/{trace_id}")
        assert logs.status_code == 200
        evidence.response("GET", f"/p3/logs/{trace_id}", logs)
    evidence.response("GET", f"/p3/tasks/{job_id}", task)
    evidence.data["executions"].append(
        {
            "operation_id": operation_id,
            "job_id": job_id,
            "recall_id": pack.recall_id,
            "selected_sources": list(SOURCES),
            "coverage": pack.coverage.model_dump(),
            "routes": observed,
        }
    )
    evidence.data["acceptance"] = "passed"
