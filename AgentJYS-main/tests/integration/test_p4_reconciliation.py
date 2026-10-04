"""Read original effects through current P2 without resending or updating a run."""

from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from tests.integration.test_current_p2_http import configuration as configuration
from tests.integration.test_current_p2_http import eventually, headers
from tests.integration.test_p4_demo_temporal import ForwardToP3, business_writes
from tests.integration.test_p4_execution_state import execute_interrupted

from aether_agent_memory.remember.basic.policy import RememberPolicy
from aether_p4_simulator.demo.models import StartRequest
from aether_p4_simulator.demo.service import DemoService
from aether_p4_simulator.validation.client import P3ValidationClient
from azure_component_service import Service

pytestmark = pytest.mark.integration


@pytest.mark.parametrize(
    ("phase", "committed"),
    [
        ("before_effect", False),
        ("before_effect", True),
        ("http_observed", True),
        ("parsed", True),
        ("consumed", True),
    ],
)
def test_reconcile_original_document_after_interruption_without_any_write(
    configuration, phase, committed
):
    service = Service(configuration)
    run_id = uuid4()
    with TestClient(service.app()) as app:
        eventually(lambda: app.get("/p3/readyz").status_code == 200)
        bridge = execute_interrupted(
            app,
            service,
            run_id,
            "library-full",
            phase=phase,
            suffix="_1_upload",
            committed=committed,
        )
        record = app.get(f"/p3/client-runs/{run_id}", headers=headers()).json()
        if phase == "http_observed":
            # Observing HTTP may mean accepted/pending. Let the original job
            # finish before asserting that its result is available; do not resend.
            job_id = record["snapshot"]["operations"][0]["job_id"]
            assert job_id
            eventually(
                lambda: (
                    app.get(f"/p3/operations/{job_id}", headers=headers()).json()["state"]
                    == "succeeded"
                )
            )
        original_calls = list(business_writes(bridge))
        observer = ForwardToP3(app, service.execution)
        fresh = DemoService(P3ValidationClient("http://testserver", "alice", transport=observer))
        try:
            report = fresh.reconcile_execution(str(run_id))
            (item,) = report.findings
            assert item.intent.operation_id == record["snapshot"]["operations"][0]["operation_id"]
            assert item.input_state == "matched"
            if phase == "before_effect":
                assert item.state == "unconfirmed" and item.observed_result is None
            else:
                assert item.state == "result_available" and item.admission_state == "matched"
                assert item.observed_result.result_type == "DocumentInput"
                assert item.result_match == (
                    "not_captured" if phase == "http_observed" else "matched"
                )
                assert bool(item.saved_result and item.saved_result.consumed) is (
                    phase == "consumed"
                )
            assert all(method == "GET" for method, _, _ in observer.calls)
            assert business_writes(bridge) == original_calls
            assert app.get(f"/p3/client-runs/{run_id}", headers=headers()).json() == record
        finally:
            fresh.close()


def test_newer_unsent_journal_tail_is_reconciled_even_when_state_save_failed(configuration):
    service = Service(configuration)
    run_id = uuid4()
    with TestClient(service.app()) as app:
        eventually(lambda: app.get("/p3/readyz").status_code == 200)
        execute_interrupted(
            app,
            service,
            run_id,
            "library-full",
            phase="before_effect",
            suffix="_1_save_rules",
            committed=False,
        )
        observer = ForwardToP3(app, service.execution)
        fresh = DemoService(P3ValidationClient("http://testserver", "alice", transport=observer))
        try:
            report = fresh.reconcile_execution(str(run_id))
            assert not report.restored.journal_matches
            first, tail = report.findings
            assert first.state == "result_available" and first.saved_result.consumed
            assert (
                tail.intent.operation_id.endswith("_1_save_rules") and tail.state == "unconfirmed"
            )
            assert tail.input_state == "matched" and tail.saved_result is None
            assert all(method == "GET" for method, _, _ in observer.calls)
            assert app.get(
                f"/p3/client-runs/{run_id}", headers=headers()
            ).json() == report.restored.record.model_dump(mode="json")
        finally:
            fresh.close()


def test_lost_original_business_reply_is_recovered_by_original_id_only(configuration):
    service = Service(configuration)
    run_id = uuid4()
    with TestClient(service.app()) as app:
        eventually(lambda: app.get("/p3/readyz").status_code == 200)

        class LoseReply(ForwardToP3):
            original_job = None

            def handle_request(self, request):
                response = super().handle_request(request)
                if request.method == "POST" and request.url.path == "/p3/remember":
                    self.original_job = response.headers["X-P3-Job-ID"]
                    raise httpx.ReadError("lost original accepted reply", request=request)
                return response

        bridge = LoseReply(app, service.execution)
        demo = DemoService(P3ValidationClient("http://testserver", "alice", transport=bridge))
        try:
            demo.start(StartRequest(scenario_id="library-basic", request_id=run_id))
        finally:
            demo.close()
        job = bridge.original_job
        assert job and len(business_writes(bridge)) == 1
        eventually(
            lambda: (
                app.get(f"/p3/operations/{job}", headers=headers()).json()["state"] == "succeeded"
            )
        )
        observer = ForwardToP3(app, service.execution)
        fresh = DemoService(P3ValidationClient("http://testserver", "alice", transport=observer))
        try:
            report = fresh.reconcile_execution(str(run_id))
            (item,) = report.findings
            assert item.intent.phase == "prepared" and item.intent.job_id is None
            assert item.state == "result_available" and item.result_match == "not_captured"
            assert item.confirmation.lookup.job_id == job and item.task.task_id == job
            assert all(method == "GET" for method, _, _ in observer.calls)
            assert app.get(
                f"/p3/client-runs/{run_id}", headers=headers()
            ).json() == report.restored.record.model_dump(mode="json")
        finally:
            fresh.close()


@pytest.mark.parametrize(
    ("scenario", "required"),
    [
        ("library-basic", {"remember.save", "recall.execute"}),
        ("library-full", {"remember.document", "remember.save", "recall.execute"}),
        ("weather-weekend", {"remember.consolidate"}),
        (
            "preference-update",
            {
                "remember.correct",
                "remember.lifecycle",
                "remember.retention",
                "remember.reprocess",
                "remember.reindex",
            },
        ),
        ("learning-review", {"remember.consolidate", "remember.reflection", "remember.distill"}),
        ("forget-sources", {"remember.delete", "source.delete", "source.revoke"}),
    ],
)
def test_all_six_original_story_journals_are_read_without_replaying(
    configuration, scenario, required
):
    service = Service(configuration.model_copy(update={"remember": RememberPolicy()}))
    run_id = uuid4()
    with TestClient(service.app()) as app:
        eventually(lambda: app.get("/p3/readyz").status_code == 200)
        bridge = ForwardToP3(app, service.execution)
        demo = DemoService(P3ValidationClient("http://testserver", "alice", transport=bridge))
        try:
            demo.start(StartRequest(scenario_id=scenario, request_id=run_id))
        finally:
            demo.close()
        record = app.get(f"/p3/client-runs/{run_id}", headers=headers()).json()
        assert record["snapshot"]["state"] == (
            "blocked" if scenario == "learning-review" else "passed"
        )
        observer = ForwardToP3(app, service.execution)
        fresh = DemoService(P3ValidationClient("http://testserver", "alice", transport=observer))
        try:
            report = fresh.reconcile_execution(str(run_id))
            assert [x.intent.model_dump(mode="json") for x in report.findings] == record[
                "snapshot"
            ]["operations"]
            found = {x.confirmation.lookup.kind for x in report.findings}
            assert required <= found
            assert all(
                x.input_state == "matched" and x.admission_state == "matched"
                for x in report.findings
            )
            for item in report.findings:
                assert item.state in {"result_available", "result_unavailable"}, item.model_dump()
                if item.state == "result_available":
                    assert item.result_match == "matched", item.model_dump()
                else:
                    assert item.confirmation.lookup.kind == "recall.execute"
                    assert item.errors[-1].code == "recall_invalidated"
            if scenario == "preference-update":
                lifecycle = [
                    x for x in report.findings if x.confirmation.lookup.kind == "remember.lifecycle"
                ]
                assert len(lifecycle) == 2
                assert (
                    lifecycle[0].observed_result.parsed_hash
                    != lifecycle[1].observed_result.parsed_hash
                )
                assert all(x.result_validation == "committed_metadata" for x in lifecycle)
                assert any(x.state == "result_unavailable" for x in report.findings)
            if scenario == "learning-review":
                distill = next(
                    x for x in report.findings if x.confirmation.lookup.kind == "remember.distill"
                )
                assert distill.state == "result_available" and not distill.saved_result.consumed
                assert distill.task is None and distill.confirmation.lookup.receipt.task_ids
            assert all(method == "GET" for method, _, _ in observer.calls)
            assert business_writes(observer) == []
            assert app.get(f"/p3/client-runs/{run_id}", headers=headers()).json() == record
        finally:
            fresh.close()
