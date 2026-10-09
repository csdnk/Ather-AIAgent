"""AET-35 / RC-SRC-02: real native long-term retrieval under adverse Top-1 pressure."""

from uuid import uuid4

import pytest
from tests.integration.test_recall_working_native_search import (
    catalog,
    eventually,
    recall,
    verify_ready,
)
from tests.integration.test_recall_working_native_search import working_target as _working_target

from recall_authorization_support import F1_TEXTS, RecallHTTP
from recall_long_term_search_support import (
    REVIEW_QUERY,
    REVIEW_TEXT,
    assert_owned_index,
    collect_pressure,
    index_pressure_row,
    published_target,
)

pytestmark = [pytest.mark.integration, pytest.mark.sources, pytest.mark.p0]
working_target = _working_target


def remember_review(http, session):
    operation_id = "review-" + uuid4().hex
    receipt, _ = http.command(
        "/p3/remember",
        {
            "source": {
                "kind": "text",
                "external_id": operation_id,
                "external_version": "1",
                "occurred_at": "2026-10-09T00:00:00.000Z",
            },
            "selection": {"session_id": session},
            "content": {"kind": "text", "text": REVIEW_TEXT},
        },
        operation_id,
        http.maintainer,
    )
    assert receipt["saved"] and len(receipt["memories"]) == 1
    return receipt


def ready_long_term(http, session, texts, native):
    def ready():
        rows = [
            m for m in catalog(http, session) if m["kind"] != "working" and m["status"] == "active"
        ]
        if len(rows) == len(texts) and all(m["projection_state"] == "ready" for m in rows):
            return rows
        assert all(m["projection_state"] != "failed" for m in rows)
        return None

    memories = []
    for row in eventually(ready):
        response = http.get(f"/p3/remember/{row['ref']['memory_id']}")
        assert response.status_code == 200
        text = response.json()["content"]
        assert text in texts
        memories.append(verify_ready(http, row["ref"], text, native.model_space, row["kind"]))
    assert {m.content for m in memories} == set(texts)
    return tuple(memories)


@pytest.mark.parametrize(
    "working_target", [{"case_id": "AET-35", "candidate_limit": 1}], indirect=True
)
@pytest.mark.parametrize("actor", ["U01", "U06"])
@pytest.mark.parametrize("axis", ["scope", "space", "source"])
def test_long_term_complete_body_and_filters_precede_top1(working_target, actor, axis):
    client, native, probe, evidence = working_target
    runtime = probe.runtime
    vectors = runtime.vectors
    assert evidence.data["server_candidate_limit"] == runtime.recall.settings.candidate_limit == 1
    maintainer = RecallHTTP(client, "fixture-maintainer")
    session = "review-" + uuid4().hex
    remember_review(maintainer, session)
    proofs = maintainer.prepare_f1(session)
    f1 = tuple(
        verify_ready(maintainer, p["ref"], text, native.model_space, "working")
        for p, text in zip(proofs, F1_TEXTS, strict=True)
    )
    long_term = ready_long_term(maintainer, session, (*F1_TEXTS, REVIEW_TEXT), native)
    f2 = next(m for m in long_term if m.content == REVIEW_TEXT)
    others = tuple(m.ref for m in long_term if m.ref != f2.ref)
    evidence.data["fixtures"].extend(
        {
            "ref": m.ref.model_dump(mode="json"),
            "body_hash": m.content_hash,
            "kind": m.kind,
            "projection_state": m.projection_state,
        }
        for m in (*f1, *long_term)
    )
    recall(
        client,
        native,
        probe,
        evidence,
        actor,
        session,
        "long_term",
        (f2,),
        "review-before",
        query=REVIEW_QUERY,
        discovery_refs=others,
    )
    control = evidence.data["executions"][-1]
    assert control["working_calls"] == 0
    encoding = next(r for r in probe.embeddings if r["operation_id"] == control["operation_id"])
    vector = encoding["result"].items[0].vector
    basis_memory = f2
    if axis == "scope":
        outside_session = "outside-" + uuid4().hex
        remember_review(maintainer, outside_session)
        basis_memory = ready_long_term(maintainer, outside_session, (REVIEW_TEXT,), native)[0]
    elif axis == "source":
        basis_memory = f1[0]
    ctx = runtime.foundation.identity.context(
        "fixture-maintainer",
        operation_id="top1-" + uuid4().hex,
        timeout_seconds=120,
    )
    target = client.portal.call(published_target, vectors, ctx, f2)
    basis = (
        target
        if basis_memory is f2
        else client.portal.call(published_target, vectors, ctx, basis_memory)
    )
    adverse, row = index_pressure_row(basis, vector, axis)

    async def install():
        assert_owned_index(vectors)
        await vectors.call(ctx, "upsert", data=[row])

    try:
        client.portal.call(install)
        # Control seeding and raw probes never substitute for the real HTTP Recall below.
        pressure = client.portal.call(
            collect_pressure, vectors, probe, ctx, vector, target, adverse, axis
        )
        evidence.data["top1_pressure"] = pressure
        pack = recall(
            client,
            native,
            probe,
            evidence,
            actor,
            session,
            "long_term",
            (f2,),
            "review-after",
            query=REVIEW_QUERY,
            discovery_refs=others,
        )
        assert "周二上午" in pack.rendered_context and "遇节假日顺延" in pack.rendered_context
        assert pack.groups[0].items[0].content == REVIEW_TEXT
        final = evidence.data["executions"][-1]
        assert final["working_calls"] == 0 and final["long_term_calls"] > 0
        final_encoding = next(
            r for r in probe.embeddings if r["operation_id"] == final["operation_id"]
        )
        assert final_encoding["result"].items[0].vector == vector, "Top-1 proof used another query"
    finally:
        cleanup = runtime.foundation.identity.context("fixture-maintainer", timeout_seconds=60)

        async def remove():
            assert_owned_index(vectors)
            await vectors.call(cleanup, "delete", ids=[adverse.vector_id])

        client.portal.call(remove)
    evidence.data["acceptance"] = "passed"
