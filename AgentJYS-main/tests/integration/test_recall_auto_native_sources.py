"""AET-37 / RC-SRC-04,05: auto resolves context before source results arrive.

Real Remember, native query encoding and Azure search. Only the named vector
port empty/outage controls are injected; missing prerequisites fail explicitly.
"""

import asyncio
from uuid import uuid4

import pytest
from tests.integration.test_recall_long_term_native_search import ready_long_term
from tests.integration.test_recall_working_native_search import catalog, eventually, verify_ready
from tests.integration.test_recall_working_native_search import working_target as _working_target

from aether_agent_memory.recall.contracts.models import ContextPack
from aether_agent_memory.runtime.contracts.models import ErrorCode
from aether_agent_memory.runtime.foundation.common import FoundationError
from recall_authorization_support import F1_TEXTS, RecallHTTP, assert_denied
from recall_auto_sources_support import (
    AUTO_CASES,
    AutoSearchControl,
    assert_auto_execution,
    assert_auto_selection_record,
    assert_auto_trace,
)

pytestmark = [pytest.mark.integration, pytest.mark.sources, pytest.mark.p0]
working_target = _working_target
QUERY = "用户的咖啡加糖习惯与饮茶偏好是什么？"


@pytest.fixture
def auto_target(working_target, monkeypatch):
    client, native, probe, evidence = working_target
    maintainer = RecallHTTP(client, "fixture-maintainer")
    context = {"session_id": "auto-session-" + uuid4().hex, "task_id": "auto-task-" + uuid4().hex}
    refs = []
    for text in F1_TEXTS:
        operation_id = "auto-prepare-" + uuid4().hex
        receipt, _ = maintainer.command(
            "/p3/remember",
            {
                "source": {
                    "kind": "text",
                    "external_id": operation_id,
                    "external_version": "1",
                    "occurred_at": "2026-10-10T00:00:00.000Z",
                },
                "selection": context,
                "content": {"kind": "text", "text": text},
            },
            operation_id,
            maintainer.maintainer,
        )
        assert receipt["saved"] and len(receipt["memories"]) == 1
        refs.append(receipt["memories"][0])
    eventually(
        lambda: all(
            any(
                row["ref"] == ref and row["projection_state"] == "ready"
                for row in catalog(maintainer, context["session_id"])
            )
            for ref in refs
        )
    )
    working = tuple(
        verify_ready(maintainer, ref, text, native.model_space, "working")
        for ref, text in zip(refs, F1_TEXTS, strict=True)
    )
    long_term = ready_long_term(maintainer, context["session_id"], F1_TEXTS, native)
    memories = {"working": working, "long_term": long_term}
    for lane in memories.values():
        assert all(
            m.ref.scope.session_id == context["session_id"]
            and m.ref.scope.task_id == context["task_id"]
            for m in lane
        )
    evidence.data["fixtures"] = [
        {
            "ref": m.ref.model_dump(mode="json"),
            "kind": m.kind,
            "body_hash": m.content_hash,
            "projection_state": m.projection_state,
        }
        for lane in memories.values()
        for m in lane
    ]
    control = AutoSearchControl(probe.runtime.vectors, monkeypatch)
    return client, native, probe, evidence, context, memories, control


def run_auto(target, actor, variant, *, changed_source=None, mode=None):
    client, native, probe, evidence, context, memories, control = target
    keys, selected = AUTO_CASES[variant]
    selection = {key: context[key] for key in keys}
    operation_id = "auto-query-" + uuid4().hex
    if mode:
        control.inject(operation_id, changed_source, mode)
    http = RecallHTTP(client, actor)
    payload, job_id = http.command(
        "/p3/recall",
        {"query": QUERY, "selection": selection, "sources": "auto", "token_budget": 4096},
        operation_id,
        actor,
    )
    pack = ContextPack.model_validate(payload)
    assert pack.selected_sources == selected, "auto must resolve before source outcomes"
    assert pack.policy_version == probe.runtime.recall.policy_version
    expected = [m for source in selected if source != changed_source for m in memories[source]]
    items = [i for g in pack.groups for i in g.items]
    assert {i.memory for i in items} == {m.ref for m in expected}
    for item in items:
        memory = next(m for m in expected if m.ref == item.memory)
        assert item.content == memory.content and item.sources == memory.sources
        assert item.representation == "original" and item.content in pack.rendered_context
    if mode == "unavailable":
        assert pack.outcome == "degraded"
        assert getattr(pack.coverage, changed_source) == "unavailable"
        assert pack.degradation_reasons == (changed_source + "_dependency",)
    else:
        assert pack.outcome == "available" and not pack.degradation_reasons
    for source in ("working", "long_term"):
        expected_coverage = (
            "not_requested"
            if source not in selected
            else "unavailable"
            if source == changed_source and mode == "unavailable"
            else "complete"
        )
        assert getattr(pack.coverage, source) == expected_coverage
    result = http.get(f"/p3/recalls/{pack.recall_id}/result")
    assert result.status_code == 200 and result.json() == payload
    trace_id, observed = assert_auto_execution(
        probe,
        control,
        operation_id,
        QUERY,
        native,
        selected,
        {source: tuple(m.ref for m in lane) for source, lane in memories.items()},
        selection,
        changed_source=changed_source,
        mode=mode,
    )
    ctx = probe.runtime.foundation.identity.context(actor, timeout_seconds=60)

    async def diagnostic():
        return await asyncio.to_thread(probe.runtime.foundation.diagnostics.trace, ctx, trace_id)

    task = http.get(f"/p3/tasks/{job_id}")
    evidence.response("GET", f"/p3/tasks/{job_id}", task)
    if actor == "U01":
        assert_denied(task, 403, "FORBIDDEN")
        with pytest.raises(FoundationError) as denied:
            client.portal.call(diagnostic)
        assert denied.value.code == ErrorCode.FORBIDDEN
        selection_evidence = None
    else:
        assert task.status_code == 200
        assert task.json()["task_id"] == job_id and task.json()["initiator_id"] == actor
        trace = client.portal.call(diagnostic)
        selection_evidence = assert_auto_trace(
            trace, pack, job_id, variant, selection, probe.runtime.recall.policy_version
        )
        assert selection_evidence["trace_id"] == trace_id
    evidence.data["executions"].append(
        {
            "operation_id": operation_id,
            "job_id": job_id,
            "recall_id": pack.recall_id,
            "trace_id": trace_id,
            "variant": variant,
            "selected_sources": list(pack.selected_sources),
            "policy_version": pack.policy_version,
            "coverage": pack.coverage.model_dump(),
            "degradation_reasons": list(pack.degradation_reasons),
            "routes": observed,
            "selection_evidence": selection_evidence,
            "changed_source": changed_source,
            "mode": mode,
        }
    )
    return pack, observed, job_id


@pytest.mark.parametrize("working_target", [{"case_id": "AET-37"}], indirect=True)
@pytest.mark.parametrize("actor", ["U01", "U06"])
@pytest.mark.parametrize("variant", list(AUTO_CASES))
def test_auto_resolves_each_context_and_executes_only_the_selected_sources(
    auto_target, actor, variant
):
    pack, observed, _ = run_auto(auto_target, actor, variant)
    if variant == "no-context":
        assert "working" not in observed
        # Recent, genuinely Ready Working memories exist in the same home scope.
        assert len(auto_target[5]["working"]) == 2
        assert pack.coverage.working == "not_requested"
    auto_target[3].data["acceptance"] = "passed"


@pytest.mark.parametrize("working_target", [{"case_id": "AET-37"}], indirect=True)
@pytest.mark.parametrize("actor", ["U01", "U06"])
@pytest.mark.parametrize("variant", ["session-only", "task-only", "session-and-task"])
@pytest.mark.parametrize("changed_source", ["working", "long_term"])
@pytest.mark.parametrize("mode", ["empty", "unavailable"])
def test_auto_choice_is_not_rewritten_by_hit_count_or_source_outage(
    auto_target,
    actor,
    variant,
    changed_source,
    mode,
):
    before, _, _ = run_auto(auto_target, actor, variant)
    after, _, _ = run_auto(auto_target, actor, variant, changed_source=changed_source, mode=mode)
    assert before.recall_id != after.recall_id, "independent requests required"
    assert before.selected_sources == after.selected_sources == ("working", "long_term")
    assert before.policy_version == after.policy_version
    assert len(after.groups) < len(before.groups), "perturbation must affect delivered results"
    auto_target[3].data["acceptance"] = "passed"


@pytest.mark.parametrize("working_target", [{"case_id": "AET-37"}], indirect=True)
@pytest.mark.parametrize("variant", list(AUTO_CASES))
def test_auto_diagnostics_persist_resolved_plan_with_causal_scope_and_policy(auto_target, variant):
    # Known product capability gap, kept separate from execution/coverage tests.
    pack, observed, job_id = run_auto(auto_target, "U06", variant)
    client, _, probe, evidence, context, _, _ = auto_target
    ctx = probe.runtime.foundation.identity.context("U06", timeout_seconds=60)
    # The normal lanes expose trace_id; no failed route is injected in this test.
    trace_id = next(iter(observed.values()))["trace_id"]

    async def diagnostic():
        return await asyncio.to_thread(probe.runtime.foundation.diagnostics.trace, ctx, trace_id)

    keys, _ = AUTO_CASES[variant]
    try:
        assert_auto_selection_record(
            client.portal.call(diagnostic),
            pack,
            job_id,
            variant,
            {key: context[key] for key in keys},
            probe.runtime.recall.policy_version,
        )
    except AssertionError as error:
        if str(error).startswith("AET-37 gap:"):
            evidence.blocked("blocked_requirement", str(error))
        raise
    evidence.data["acceptance"] = "passed"
