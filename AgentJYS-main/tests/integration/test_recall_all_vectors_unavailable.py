"""AET-42 / RC-SRC-12: body availability cannot mask total vector failure."""

from contextlib import ExitStack
from uuid import uuid4

import pytest
from tests.integration.test_recall_long_term_native_search import ready_long_term
from tests.integration.test_recall_projection_states import diagnose
from tests.integration.test_recall_required_sources import positive
from tests.integration.test_recall_working_native_search import recall, verify_ready
from tests.integration.test_recall_working_native_search import working_target as _working_target

from recall_all_vectors_support import assert_no_fallback, observe_discovery
from recall_authorization_support import F1_TEXTS, RecallHTTP
from recall_projection_state_support import assert_not_success
from recall_required_sources_support import AccessEventWitness, assert_failed_consistently
from recall_source_failure_support import SourceFault

pytestmark = [pytest.mark.integration, pytest.mark.sources, pytest.mark.p0]
working_target = _working_target
QUERY = "用户喝咖啡加糖吗，喜欢什么茶？"


@pytest.mark.parametrize("working_target", [{"case_id": "AET-42", "max_items": 2}], indirect=True)
@pytest.mark.parametrize("actor", ["U01", "U06"])
@pytest.mark.parametrize("source", ["working", "long_term", "both"])
@pytest.mark.parametrize("kind", ["unavailable", "timeout"])
def test_rc_src_12_all_selected_vectors_fail_despite_readable_bodies(
    working_target, monkeypatch, actor, source, kind
):
    client, native, probe, evidence = working_target
    runtime = probe.runtime
    http, maintainer = RecallHTTP(client, actor), RecallHTTP(client, "fixture-maintainer")
    session = "all-vectors-" + uuid4().hex
    proofs = maintainer.prepare_f1(session)
    memories = {
        "working": tuple(
            verify_ready(maintainer, proof["ref"], text, native.model_space, "working")
            for proof, text in zip(proofs, F1_TEXTS, strict=True)
        ),
        "long_term": ready_long_term(maintainer, session, F1_TEXTS, native),
    }
    selected = ("working", "long_term") if source == "both" else (source,)
    witness = AccessEventWitness(runtime, monkeypatch)

    def control(label):
        if source == "both":
            return positive(http, session, memories, native, probe, witness, evidence)
        return recall(
            client,
            native,
            probe,
            evidence,
            actor,
            session,
            source,
            memories[source],
            label,
            query=QUERY,
        )

    control("before-vector-fault")
    faults = [SourceFault(runtime, session, selected_source, kind) for selected_source in selected]
    operation_id = "all-vectors-failed-" + uuid4().hex
    originals = runtime.vectors.search, runtime.vectors.client.search
    with ExitStack() as stack:
        for fault in faults:
            stack.enter_context(fault.installed(monkeypatch))
        with monkeypatch.context() as observations:
            calls = observe_discovery(runtime, observations)
            # These public body reads occur while every selected vector route is broken.
            bodies = [
                maintainer.verify_body(m.ref.model_dump(mode="json"), m.content)
                for selected_source in selected
                for m in memories[selected_source]
            ]
            response = client.post(
                "/p3/recall",
                headers={"Authorization": f"Bearer {actor}", "X-Operation-ID": operation_id},
                json={
                    "query": QUERY,
                    "selection": {"session_id": session},
                    "sources": source,
                    "token_budget": 4096,
                },
            )
            evidence.response("POST", "/p3/recall", response)
            lookup = http.get(
                f"/p3/operation-requests/{operation_id}", params={"kind": "recall.execute"}
            )
            assert lookup.status_code == 200 and lookup.json()["state"] == "found"
            job = lookup.json()["job_id"]
            terminal = http.poll_job(job)
            status = http.get(f"/p3/operations/{job}")
            assert status.status_code == 200
            recall_id = status.json()["subject"]["object_id"]
            record = http.get(f"/p3/recalls/{recall_id}")
            results = [
                (f"/p3/operations/{job}/result", http.get(f"/p3/operations/{job}/result")),
                (f"/p3/recalls/{recall_id}/result", http.get(f"/p3/recalls/{recall_id}/result")),
            ]
            events = witness.observe(operation_id)
            searches = [r for r in probe.searches if r["operation_id"] == operation_id]
            attempts = [
                a for fault in faults for a in fault.attempts if a["operation_id"] == operation_id
            ]
            evidence.data["executions"].append(
                {
                    "operation_id": operation_id,
                    "job_id": job,
                    "recall_id": recall_id,
                    "selected_sources": list(selected),
                    "faults": attempts,
                    "body_controls_during_fault": bodies,
                    "discovery_calls": calls,
                    "terminal": terminal,
                    "record": record.json(),
                    "events": events,
                    "vector_attempts": [
                        {"source": r["request"].memory_source, "returned": r["result"] is not None}
                        for r in searches
                    ],
                }
            )
            for path, reply in [
                (f"/p3/operations/{job}", status),
                (f"/p3/recalls/{recall_id}", record),
                *results,
            ]:
                evidence.response("GET", path, reply)
            assert {r["request"].memory_source for r in searches} == set(selected)
            assert searches and all(r["result"] is None for r in searches)
            assert {a["source"] for a in attempts} == set(selected)
            assert {a["trace_id"] for a in attempts} == {r["trace_id"] for r in searches}
            encodings = [r for r in probe.embeddings if r["operation_id"] == operation_id]
            assert encodings and all(r["result"] is not None and r["native"] for r in encodings)
            assert all(r["request"].texts == (QUERY,) for r in encodings)
            vectors = [tuple(r["result"].items[0].vector) for r in encodings]
            assert all(tuple(r["request"].vector) in vectors for r in searches)
            assert_no_fallback(calls, operation_id)
            assert record.status_code == 200
            assert_failed_consistently(status.json(), record.json(), events, job)
            assert_not_success(response)
            for _, reply in results:
                assert_not_success(reply)
                assert reply.status_code == 503 and reply.json()["code"] == "DEPENDENCY_UNAVAILABLE"
            diagnose(http, maintainer, job, actor, evidence)
    assert (runtime.vectors.search, runtime.vectors.client.search) == originals
    control("after-vector-recovery")
    replay = http.get(f"/p3/operations/{job}/result")
    assert replay.status_code == 503 and replay.json()["code"] == "DEPENDENCY_UNAVAILABLE"
    assert witness.observe(operation_id) == events
    evidence.data.update(control_restored=True, acceptance="passed")
