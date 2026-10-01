"""Diagnostic outages must not change business facts or leak other runs."""

from contextlib import closing
from uuid import uuid4

import httpx
import pytest

from aether_agent_memory.runtime.contracts.models import Scope
from aether_p4_simulator.demo import diagnostics, stories
from aether_p4_simulator.demo.coverage import Coverage
from aether_p4_simulator.demo.execution import StoryRun
from aether_p4_simulator.demo.models import StartRequest
from aether_p4_simulator.validation.client import P3ValidationClient

from .support import Upstream, finish
from .test_service import service_for

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("business_state", ["passed", "failed"])
def test_diagnostic_exception_does_not_replace_business_result(monkeypatch, business_state):
    def business(run):
        with run.step(1, "/p3/remember"):
            if business_state == "failed":
                run.check("actual result", False, "actual mismatch")

    def broken(*_):
        raise RuntimeError("secret diagnostic payload")

    monkeypatch.setattr(stories, "run_story", business)
    monkeypatch.setattr(diagnostics, "collect", broken)
    service = service_for(Upstream())
    try:
        run = service.start(StartRequest(scenario_id="library-full", request_id=uuid4()))
        result = finish(service, run.run_id)
        assert result.state == business_state
        assert result.diagnostics.state == "incomplete"
        if business_state == "failed":
            assert result.error.code == "check_failed"
        assert "secret diagnostic" not in result.model_dump_json()
    finally:
        service.close()


def collect_metadata(variant, *, next_before=100):
    scope = Scope(
        tenant_id="t1",
        application_id="a1",
        user_id="u1",
        agent_id="a1",
        task_id="demo_diag",
        session_id="demo_diag_session",
    )
    subject = dict(
        owner="remember",
        object_type="memory",
        object_id="m1",
        scope=scope.model_dump(mode="json"),
    )
    foreign = {**subject, "scope": {**subject["scope"], "task_id": "other"}}
    seen = []

    def trace_metadata(trace_id, sequence):
        return {
            "trace_id": trace_id,
            "first_sequence": sequence,
            "started_at": "2026-10-01T00:00:00.000Z",
            "last_seen": "2026-10-01T00:00:01.000Z",
            "record_count": 1,
            "span_count": 1,
            "failed_span_count": 0,
            "entry_node": "remember",
            "flow": "remember",
        }

    def transport(request):
        path = request.url.path
        seen.append(request)
        data = {
            "/p3/tasks": {
                "items": [
                    {"task_id": "owned", "subject": subject, "trace_id": "trace1"},
                    {"task_id": "foreign", "subject": foreign, "trace_id": "secret"},
                ],
                "next_cursor": None,
            },
            "/p3/traces": {
                "items": [trace_metadata("trace1", 120), trace_metadata("secret", 100)],
                "next_before": None,
                "coverage": "retained_records_only",
            },
            "/p3/logs/trace1": {
                "records": [
                    {"trace_id": "trace1", "task_id": "owned"},
                    {"trace_id": "trace1", "task_id": "foreign", "message": "secret"},
                ]
            },
            "/p3/incidents": [{"subject": foreign}, {"subject": subject}],
            "/p3/runtime": {"state": "observed"},
            "/p3/readyz": {"readiness": "ready"},
            "/p3/periodic/control": {
                "revision": 1,
                "binding": {
                    "namespace": "default",
                    "workflow_id": "periodic",
                    "first_run_id": "run",
                    "current_run_id": "run",
                    "input_hash": "a" * 64,
                    "plan_version": "v1",
                },
            },
        }
        if variant == "malformed" and path == "/p3/tasks":
            data[path]["items"][0]["task_id"] = ["not", "an", "id"]
        if variant == "paginated":
            data["/p3/tasks"]["items"].append(
                {"task_id": "owned2", "subject": subject, "trace_id": "trace2"}
            )
            data["/p3/logs/trace2"] = {"records": [{"trace_id": "trace2", "task_id": "owned2"}]}
            if path == "/p3/traces":
                if request.url.params.get("before") is None:
                    data[path]["next_before"] = next_before
                else:
                    assert request.url.params["before"] == "100"
                    data[path]["items"] = [trace_metadata("trace2", 90)]
        return httpx.Response(200, json=data[path])

    with closing(
        P3ValidationClient(
            "http://p3.test",
            "secret",
            transport=httpx.MockTransport(transport),
        )
    ) as client:
        run = StoryRun(client, "demo_diag", "library-full", None, lambda _: None)
        run.scope, run.task_ids = scope, {"owned"}
        if variant == "paginated":
            run.task_ids.add("owned2")
        with run.step(1, "/p3/remember"):
            run.check("business result", True, "already verified")
        coverage = Coverage()
        with client.observe_calls(lambda call: coverage.observe(call, 0)):
            result = diagnostics.collect(run, coverage)
        assert run.current_step.state == "passed"
        assert run.current_step.checks[0].passed
        return result, coverage, seen


def test_malformed_metadata_is_incomplete_not_an_uncaught_business_error():
    result, coverage, _ = collect_metadata("malformed")
    assert result.state == "incomplete"
    assert result.task_count == 0
    assert coverage.rows["GET", "/p3/tasks"].state != "passed"


def test_metadata_counts_only_owned_tasks_and_validates_actual_periodic_contract():
    result, coverage, seen = collect_metadata("valid")
    assert (result.task_count, result.trace_count, result.log_record_count) == (1, 1, 1)
    assert result.incident_count == 1
    assert "/p3/logs/secret" not in [request.url.path for request in seen]
    assert "secret" not in result.model_dump_json()
    assert coverage.rows["GET", "/p3/periodic/control"].state == "passed"


def test_integer_trace_cursor_collects_owned_traces_across_pages():
    result, coverage, seen = collect_metadata("paginated")
    trace_requests = [request for request in seen if request.url.path == "/p3/traces"]
    assert [request.url.params.get("before") for request in trace_requests] == [None, "100"]
    assert (result.task_count, result.trace_count, result.log_record_count) == (2, 2, 2)
    assert result.state == "complete"
    assert coverage.rows["GET", "/p3/traces"].state == "passed"
    assert "/p3/logs/secret" not in [request.url.path for request in seen]
    assert "secret" not in result.model_dump_json()


@pytest.mark.parametrize("cursor", [True, False, 0, -1, 1.5, "100", "bad", {}, []])
def test_invalid_trace_cursor_is_incomplete_without_following_it(cursor):
    result, coverage, seen = collect_metadata("paginated", next_before=cursor)
    assert result.state == "incomplete"
    assert result.trace_count == 0
    assert coverage.rows["GET", "/p3/traces"].state != "passed"
    assert sum(request.url.path == "/p3/traces" for request in seen) == 1
