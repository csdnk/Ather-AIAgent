"""AET-37 auto selection assertions and operation-bound vector fault control."""

from types import SimpleNamespace

from aether_agent_memory.runtime.contracts.models import ErrorCode
from aether_agent_memory.runtime.foundation.common import FoundationError
from recall_working_search_support import assert_execution

# Literal expectations from RC-SRC-04/05, independent of product resolution code.
AUTO_CASES = {
    "session-only": (("session_id",), ("working", "long_term")),
    "task-only": (("task_id",), ("working", "long_term")),
    "session-and-task": (("session_id", "task_id"), ("working", "long_term")),
    "no-context": ((), ("long_term",)),
}


class AutoSearchControl:
    """Uncontrolled routes always delegate; faults apply to one exact operation.

    Empty-hit control delegates real ANN first, then suppresses that route's hits.
    Unavailability is injected before delegation; it is an attempted port call,
    never claimed as a successful SDK search.
    """

    def __init__(self, vectors, monkeypatch):
        delegate = vectors.search
        self.calls = []
        self.operation_id = self.source = self.mode = None

        async def search(ctx, request):
            row = {
                "operation_id": ctx.operation_id,
                "trace_id": ctx.trace_id,
                "request": request,
                "result": None,
                "original": None,
                "injection": None,
            }
            self.calls.append(row)
            if ctx.operation_id == self.operation_id and request.memory_source == self.source:
                row["injection"] = self.mode
                if self.mode == "unavailable":
                    raise FoundationError(ErrorCode.DEPENDENCY_UNAVAILABLE, "AET-37 source outage")
            original = await delegate(ctx, request)
            row["original"] = original
            row["result"] = (
                original.model_copy(update={"candidates": ()})
                if row["injection"] == "empty"
                else original
            )
            return row["result"]

        monkeypatch.setattr(vectors, "search", search)

    def inject(self, operation_id, source, mode):
        assert source in {"working", "long_term"} and mode in {"empty", "unavailable"}
        self.operation_id, self.source, self.mode = operation_id, source, mode


def assert_auto_execution(
    probe,
    control,
    operation_id,
    query,
    native,
    expected_sources,
    refs_by_source,
    selection,
    *,
    changed_source=None,
    mode=None,
):
    calls = [r for r in control.calls if r["operation_id"] == operation_id]
    assert {r["request"].memory_source for r in calls} == set(expected_sources), "route mismatch"
    assert all(r["request"].selection.model_dump(exclude_none=True) == selection for r in calls)
    assert len({r["trace_id"] for r in calls}) == 1
    execution = {}
    for source in expected_sources:
        rows = [r for r in calls if r["request"].memory_source == source]
        lane = SimpleNamespace(
            embeddings=probe.embeddings,
            searches=[
                r
                for r in probe.searches
                if r["operation_id"] == operation_id and r["request"].memory_source == source
            ],
        )
        if source == changed_source and mode == "unavailable":
            assert all(r["injection"] == "unavailable" and r["result"] is None for r in rows)
            assert not lane.searches, "injected outage must not masquerade as successful search"
            execution[source] = {"attempts": len(rows), "sdk_calls": 0, "state": "unavailable"}
        else:
            execution[source] = assert_execution(
                lane, operation_id, query, native, source, refs_by_source[source]
            )
            assert all(r["original"].coverage == r["result"].coverage == "complete" for r in rows)
            if source == changed_source and mode == "empty":
                assert all(r["injection"] == "empty" and not r["result"].candidates for r in rows)
                assert any(r["original"].candidates for r in rows), (
                    "hit-count control had no effect"
                )
                execution[source]["delivered_candidate_count"] = 0
            else:
                assert all(r["injection"] is None and r["result"] is r["original"] for r in rows)
    return next(iter({r["trace_id"] for r in calls})), execution


def assert_auto_trace(trace, pack, job_id, variant, selection, policy_version):
    """Check current generation diagnostics and linked original result policy.

    There is no published auto reason-code field. The recorded scope is the
    causal input; labels in AUTO_CASES are test case names, not product codes.
    """
    keys, sources = AUTO_CASES[variant]
    assert set(selection) == set(keys)
    assert pack.selected_sources == sources and pack.policy_version == policy_version
    facts = trace["durable_facts"]
    assert not facts["truncated"], "incomplete selection evidence"
    assert any(t["task_id"] == job_id and t["state"] == "succeeded" for t in facts["tasks"])
    stages = [r for r in facts["recall_stages"] if r["recall_id"] == pack.recall_id]
    planned = [r for r in stages if r["stage"] == "discover"]
    assert len(planned) == 1
    plan = planned[0]
    assert plan["subject"]["object_id"] == pack.recall_id
    scope = plan["subject"]["scope"]
    assert {k: scope.get(k) for k in ("session_id", "task_id")} == {
        "session_id": selection.get("session_id"),
        "task_id": selection.get("task_id"),
    }
    assembled = [r for r in stages if r["stage"] == "assemble"]
    assert len(assembled) == 1 and plan["recorded_at"] <= assembled[0]["recorded_at"]
    return {
        "basis": variant,
        "context": {k: scope[k] for k in keys},
        "selected_sources": list(sources),
        "policy_version": pack.policy_version,
        "policy_origin": "original_result_linked_by_recall_id",
        "trace_id": plan["trace_id"],
        "recall_id": pack.recall_id,
    }


def assert_auto_selection_record(trace, pack, job_id, variant, selection, policy_version):
    """Requirement regression for currently missing generation plan metadata.

    Expected field names reuse RecallPlanRequest.sources/policy_version; this is
    a required extension of discover.details, not a claim it exists today. The
    reason is its recorded causal scope, not an invented auto reason code.
    """
    evidence = assert_auto_trace(trace, pack, job_id, variant, selection, policy_version)
    details = next(
        r["details"]
        for r in trace["durable_facts"]["recall_stages"]
        if r["recall_id"] == pack.recall_id and r["stage"] == "discover"
    )
    assert "sources" in details, "AET-37 gap: generation diagnostics omit resolved source plan"
    assert details["sources"] == list(AUTO_CASES[variant][1]), "unresolved auto is not a plan"
    assert "policy_version" in details, "AET-37 gap: generation diagnostics omit plan policy"
    assert details["policy_version"] == policy_version
    return evidence
