"""AET-37 guards reject false auto evidence; not native acceptance."""

from copy import deepcopy
from types import SimpleNamespace

import pytest

from aether_agent_memory.recall.contracts.models import VectorSearchResult
from aether_agent_memory.runtime.contracts.models import ErrorCode
from aether_agent_memory.runtime.foundation.common import FoundationError
from recall_auto_sources_support import (
    AutoSearchControl,
    assert_auto_execution,
    assert_auto_selection_record,
    assert_auto_trace,
)
from unit.test_recall_both_sources_support import both_execution


def diagnostic_example(variant):
    # Independently stated RC-SRC-04/05 expectations, not computed by the resolver.
    examples = {
        "session-only": ({"session_id": "session"}, ("working", "long_term")),
        "task-only": ({"task_id": "task"}, ("working", "long_term")),
        "session-and-task": (
            {"session_id": "session", "task_id": "task"},
            ("working", "long_term"),
        ),
        "no-context": ({}, ("long_term",)),
    }
    selection, sources = examples[variant]
    scope = {"session_id": None, "task_id": None, **selection}
    stage = {
        "recall_id": "original-recall",
        "trace_id": "a" * 32,
        "subject": {"object_id": "original-recall", "scope": scope},
        "recorded_at": "2026-10-10T00:00:00.000Z",
    }
    trace = {
        "durable_facts": {
            "truncated": False,
            "tasks": [{"task_id": "original-job", "state": "succeeded"}],
            "recall_stages": [
                {**deepcopy(stage), "stage": "discover", "details": {}},
                {
                    **deepcopy(stage),
                    "stage": "assemble",
                    "details": {},
                },
            ],
        }
    }
    pack = SimpleNamespace(
        recall_id="original-recall",
        selected_sources=sources,
        policy_version="installed-policy-v1",
        coverage=SimpleNamespace(
            working="complete" if "working" in sources else "not_requested", long_term="complete"
        ),
    )
    return trace, pack, selection


@pytest.mark.parametrize("variant", ["session-only", "task-only", "session-and-task", "no-context"])
def test_recorded_context_plan_and_original_policy_explain_auto_resolution(variant):
    trace, pack, selection = diagnostic_example(variant)
    evidence = assert_auto_trace(
        trace, pack, "original-job", variant, selection, "installed-policy-v1"
    )
    assert evidence["basis"] == variant and evidence["recall_id"] == "original-recall"


@pytest.mark.parametrize(
    "fault",
    [
        "unresolved-auto",
        "wrong-context",
        "wrong-policy",
        "missing-discover",
        "late-discover",
        "foreign-recall",
        "foreign-job",
        "truncated",
    ],
)
def test_response_labels_or_unlinked_diagnostics_do_not_prove_auto_selection(fault):
    trace, pack, selection = diagnostic_example("session-only")
    facts = trace["durable_facts"]
    if fault == "unresolved-auto":
        pack.selected_sources = ("auto",)
    elif fault == "wrong-context":
        facts["recall_stages"][0]["subject"]["scope"]["session_id"] = None
    elif fault == "wrong-policy":
        pack.policy_version = "unrelated-policy"
    elif fault == "missing-discover":
        facts["recall_stages"].pop(0)
    elif fault == "late-discover":
        facts["recall_stages"][0]["recorded_at"] = "2026-10-10T00:00:01.000Z"
    elif fault == "foreign-recall":
        facts["recall_stages"][0]["subject"]["object_id"] = "another-recall"
    elif fault == "foreign-job":
        facts["tasks"][0]["task_id"] = "another-job"
    else:
        facts["truncated"] = True
    with pytest.raises(AssertionError):
        assert_auto_trace(
            trace, pack, "original-job", "session-only", selection, "installed-policy-v1"
        )


@pytest.mark.parametrize(
    "fault", [None, "missing-sources", "unresolved-auto", "missing-policy", "wrong-policy"]
)
def test_required_plan_diagnostics_reject_missing_or_unresolved_metadata(fault):
    trace, pack, selection = diagnostic_example("session-only")
    # Target capability fixture, explicitly distinct from current empty details.
    details = {"sources": ["working", "long_term"], "policy_version": "installed-policy-v1"}
    trace["durable_facts"]["recall_stages"][0]["details"] = details
    if fault == "missing-sources":
        details.pop("sources")
    elif fault == "unresolved-auto":
        details["sources"] = ["auto"]
    elif fault == "missing-policy":
        details.pop("policy_version")
    elif fault == "wrong-policy":
        details["policy_version"] = "another-policy"
    args = (trace, pack, "original-job", "session-only", selection, "installed-policy-v1")
    if fault is None:
        assert_auto_selection_record(*args)
    else:
        with pytest.raises(AssertionError):
            assert_auto_selection_record(*args)


async def test_port_control_is_operation_bound_and_forwards_uncontrolled_results(monkeypatch):
    actual = VectorSearchResult(candidates=(), coverage="complete")
    delegated = []

    async def search(ctx, request):
        delegated.append((ctx.operation_id, request.memory_source))
        return actual

    vectors = SimpleNamespace(search=search)
    control = AutoSearchControl(vectors, monkeypatch)
    control.inject("changed-operation", "working", "unavailable")
    request = SimpleNamespace(memory_source="working")
    other = SimpleNamespace(operation_id="other-operation", trace_id="a" * 32)
    assert await vectors.search(other, request) is actual
    ctx = SimpleNamespace(operation_id="changed-operation", trace_id="b" * 32)
    with pytest.raises(FoundationError) as failure:
        await vectors.search(ctx, request)
    assert failure.value.code == ErrorCode.DEPENDENCY_UNAVAILABLE
    assert delegated == [("other-operation", "working")]
    assert control.calls[-1]["original"] is control.calls[-1]["result"] is None
    assert await vectors.search(ctx, SimpleNamespace(memory_source="long_term")) is actual


def test_recent_working_search_attempt_invalidates_no_context_long_term_only_claim():
    probe, native = both_execution()
    control = SimpleNamespace(
        calls=[{**row, "original": row["result"], "injection": None} for row in probe.searches]
    )
    with pytest.raises(AssertionError, match="route mismatch"):
        assert_auto_execution(
            probe,
            control,
            "operation",
            "query",
            native,
            ("long_term",),
            {"working": (), "long_term": ()},
            {"session_id": "s1"},
        )
