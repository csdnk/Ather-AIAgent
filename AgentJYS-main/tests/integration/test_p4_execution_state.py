"""Real P4 state capture and read-only reconstruction over current P2 objects."""

import json
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from tests.integration.test_current_p2_http import configuration as configuration
from tests.integration.test_current_p2_http import eventually, headers
from tests.integration.test_p4_demo_temporal import ForwardToP3, business_writes

from aether_p4_simulator.demo.models import StartRequest
from aether_p4_simulator.demo.service import DemoService
from aether_p4_simulator.validation.client import P3ValidationClient
from aether_p4_simulator.validation.errors import ValidationError
from azure_component_service import Service

pytestmark = pytest.mark.integration


class InterruptState(ForwardToP3):
    """Lose a selected state reply after commit, or refuse before it reaches P3."""

    def __init__(self, app, execution, *, phase, suffix, committed):
        super().__init__(app, execution)
        self.phase, self.suffix, self.committed = phase, suffix, committed
        self.original = None

    def handle_request(self, request):
        if request.method == "PUT" and "/states/" in request.url.path:
            value = json.loads(request.content)
            data = value["data"]
            if data["phase"] == self.phase and (data["operation_id"] or "").endswith(self.suffix):
                self.original = value
                if self.committed:
                    response = super().handle_request(request)
                    assert response.status_code == 200, response.text
                raise httpx.ReadError("injected execution-state interruption", request=request)
        return super().handle_request(request)


def execute_interrupted(app, service, run_id, scenario, **fault):
    bridge = InterruptState(app, service.execution, **fault)
    demo = DemoService(P3ValidationClient("http://testserver", "alice", transport=bridge))
    try:
        demo.start(StartRequest(scenario_id=scenario, request_id=run_id))
    finally:
        demo.close()
    assert bridge.original is not None
    return bridge


def restore_without_writes(app, service, run_id):
    path = "/p3/client-runs/" + str(run_id)
    record = app.get(path, headers=headers()).json()
    observer = ForwardToP3(app, service.execution)
    fresh = DemoService(P3ValidationClient("http://testserver", "alice", transport=observer))
    try:
        restored = fresh.restore_execution(str(run_id))
        assert observer.calls and all(method == "GET" for method, _, _ in observer.calls)
        assert app.get(path, headers=headers()).json() == record
        return restored
    finally:
        fresh.close()


@pytest.mark.parametrize("committed", [False, True])
@pytest.mark.parametrize(
    ("scenario", "suffix", "previous"),
    [
        ("library-full", "_1_save_rules", "_1_upload"),
        ("preference-update", "_6_active", "_6_archived"),
        ("preference-update", "_8_reindex", "_8_reprocess"),
        ("forget-sources", "_6_revoke_source", "_5_delete_source"),
    ],
)
def test_multi_effect_step_restores_original_consumed_prefix_and_unsent_tail(
    configuration, scenario, suffix, previous, committed
):
    service = Service(configuration)
    run_id = uuid4()
    with TestClient(service.app()) as app:
        eventually(lambda: app.get("/p3/readyz").status_code == 200)
        bridge = execute_interrupted(
            app,
            service,
            run_id,
            scenario,
            phase="before_effect",
            suffix=suffix,
            committed=committed,
        )
        original = bridge.original
        operation = original["data"]["operation_id"]
        assert not any(call[2] == operation for call in business_writes(bridge))
        assert any((call[2] or "").endswith(previous) for call in business_writes(bridge))
        restored = restore_without_writes(app, service, run_id)
        assert restored.journal_matches is committed
        journal = restored.record.snapshot["operations"]
        assert journal[-1]["operation_id"] == operation and journal[-1]["phase"] == "prepared"
        assert journal[:-1] == [
            item.model_dump(mode="json")
            for item in restored.envelope.operations
            if item.operation_id != operation
        ]
        data = restored.data
        prior = next(value for key, value in data.resolved.items() if key.endswith(previous))
        assert prior.consumed and operation not in data.resolved
        if scenario == "library-full":
            assert data.documents["rules"].document_id == restored.record.scope_id + "_rules"
            assert data.receipts == {} and data.completed_steps == []
        elif suffix.endswith("reindex"):
            assert data.task_slots["reprocess"] in data.task_ids
            assert "reindex" not in data.task_slots
        elif scenario == "forget-sources":
            assert data.task_groups["cleanup"]
            assert set(data.receipts) == {"booking", "second", "third"}
            assert "before_delete" in data.recalls
        else:
            assert "before_correction" in data.recalls
            assert prior.basis == "metadata_without_content"


@pytest.mark.parametrize("phase", ["http_observed", "parsed", "consumed"])
def test_restored_state_distinguishes_http_parsing_and_semantic_consumption(configuration, phase):
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
            committed=True,
        )
        operation = bridge.original["data"]["operation_id"]
        assert len(business_writes(bridge)) == 1
        restored = restore_without_writes(app, service, run_id)
        assert restored.journal_matches and restored.data.phase == phase
        if phase == "http_observed":
            assert operation not in restored.data.resolved
        else:
            assert restored.data.resolved[operation].consumed is (phase == "consumed")
        assert bool(restored.data.documents) is (phase == "consumed")
        assert not restored.data.completed_steps


@pytest.mark.parametrize("fault", [None, b"damaged-original-state"])
def test_restore_refuses_missing_or_corrupt_state_without_repair(configuration, monkeypatch, fault):
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
            suffix="_1_upload",
            committed=True,
        )
        path = "/p3/client-runs/" + str(run_id)
        record = app.get(path, headers=headers()).json()
        objects = service.execution.inputs.objects
        original = objects.get_object_sync
        writes = []
        monkeypatch.setattr(
            objects,
            "get_object_sync",
            lambda key: fault if "/client-states/" in key else original(key),
        )
        monkeypatch.setattr(objects, "put_object_sync", lambda *args: writes.append(args))
        observer = ForwardToP3(app, service.execution)
        fresh = DemoService(P3ValidationClient("http://testserver", "alice", transport=observer))
        try:
            with pytest.raises(ValidationError) as refused:
                fresh.restore_execution(str(run_id))
            assert refused.value.status == 400 and refused.value.code == "invalid_argument"
            response = app.get(
                path + f"/states/{record['execution_state']['sequence']}", headers=headers()
            )
            assert response.json()["code"] == "CONTRACT_VIOLATION"
            assert observer.calls and all(method == "GET" for method, _, _ in observer.calls)
            assert writes == []
            assert app.get(path, headers=headers()).json() == record
        finally:
            fresh.close()


def test_every_effect_has_original_state_and_fresh_service_restores_without_writes(configuration):
    service = Service(configuration)
    run_id = uuid4()
    path = "/p3/client-runs/" + str(run_id)
    before_effects = []
    with TestClient(service.app()) as app:
        eventually(lambda: app.get("/p3/readyz").status_code == 200)

        class InspectState(ForwardToP3):
            def handle_request(self, request):
                if request.method in {"POST", "PUT"} and not request.url.path.startswith(
                    "/p3/client-runs/"
                ):
                    record = app.get(path, headers=headers()).json()
                    assert record["state_policy"] == "p4_state_v1"
                    head = record["execution_state"]
                    assert head is not None
                    state = app.get(path + f"/states/{head['sequence']}", headers=headers()).json()
                    assert state["operations"] == record["snapshot"]["operations"]
                    assert state["data"]["phase"] == "before_effect"
                    assert state["data"]["operation_id"] == request.headers["X-Operation-ID"]
                    assert request.headers["X-P3-Run-Revision"] == str(record["revision"])
                    before_effects.append(state)
                return super().handle_request(request)

        bridge = InspectState(app, service.execution)
        demo = DemoService(P3ValidationClient("http://testserver", "alice", transport=bridge))
        try:
            demo.start(StartRequest(scenario_id="library-basic", request_id=run_id))
        finally:
            demo.close()
        record = app.get(path, headers=headers()).json()
        assert record["snapshot"]["state"] == "passed", record["snapshot"].get("error")
        assert len(before_effects) == 6
        assert set(before_effects[2]["data"]["receipts"]) == {"rules", "loan"}
        observer = ForwardToP3(app, service.execution)
        fresh = DemoService(P3ValidationClient("http://testserver", "alice", transport=observer))
        try:
            restored = fresh.restore_execution(str(run_id))
            assert restored.record.run_id == run_id and restored.journal_matches
            assert restored.data.completed_steps == [1, 2, 3, 4, 5, 6]
            assert set(restored.data.receipts) == {"rules", "loan", "noise"}
            assert all(value.consumed for value in restored.data.resolved.values())
            assert "rendered_context" not in restored.data.model_dump_json()
            assert business_writes(observer) == []
            assert app.get(path, headers=headers()).json() == record
        finally:
            fresh.close()


def test_lost_state_save_reply_stops_effect_and_retains_original_prepared_state(configuration):
    service = Service(configuration)
    run_id = uuid4()
    path = "/p3/client-runs/" + str(run_id)
    with TestClient(service.app()) as app:
        eventually(lambda: app.get("/p3/readyz").status_code == 200)

        class LoseState(ForwardToP3):
            original = None

            def handle_request(self, request):
                response = super().handle_request(request)
                if request.method == "PUT" and "/states/" in request.url.path:
                    value = json.loads(request.content)
                    if value["data"]["phase"] == "before_effect":
                        assert response.status_code == 200, response.text
                        self.original = request.content
                        raise httpx.ReadError("injected state save reply loss", request=request)
                return response

        bridge = LoseState(app, service.execution)
        demo = DemoService(P3ValidationClient("http://testserver", "alice", transport=bridge))
        try:
            demo.start(StartRequest(scenario_id="library-basic", request_id=run_id))
        finally:
            demo.close()
        assert bridge.original is not None and business_writes(bridge) == []
        record = app.get(path, headers=headers()).json()
        assert record["snapshot"]["operations"][0]["phase"] == "prepared"
        head = record["execution_state"]
        assert (
            app.get(path + f"/states/{head['sequence']}", headers=headers()).content
            == bridge.original
        )
