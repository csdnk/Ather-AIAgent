"""Drop real committed responses; never fabricate a P3 operation result."""

from hashlib import sha256
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from tests.integration.test_current_p2_http import configuration as configuration
from tests.integration.test_current_p2_http import eventually, headers
from tests.integration.test_p4_demo_temporal import ForwardToP3, business_writes

from aether_agent_memory.runtime.contracts.client_runs import ClientOperation
from aether_p4_simulator.demo.models import StartRequest
from aether_p4_simulator.demo.service import DemoService
from aether_p4_simulator.validation.client import P3ValidationClient
from aether_p4_simulator.validation.errors import ValidationError
from azure_component_service import Service

pytestmark = pytest.mark.integration


def test_definition_reply_loss_preserves_run_and_future_inputs_without_business(configuration):
    from aether_p4_simulator.demo.definition import RunDefinition

    service = Service(configuration)
    run_id = uuid4()
    path = "/p3/client-runs/" + str(run_id)
    with TestClient(service.app()) as app:

        class LoseDefinitionReply(ForwardToP3):
            original = None

            def handle_request(self, request):
                response = super().handle_request(request)
                if request.method == "PUT" and request.url.path == path + "/definition":
                    assert response.status_code == 200, response.text
                    self.original = request.content
                    raise httpx.ReadError("lost definition receipt", request=request)
                return response

        bridge = LoseDefinitionReply(app, service.execution)
        demo = DemoService(P3ValidationClient("http://testserver", "alice", transport=bridge))
        try:
            with pytest.raises(ValidationError):
                demo.start(StartRequest(scenario_id="library-basic", request_id=run_id))
        finally:
            demo.close()
        assert bridge.original is not None and business_writes(bridge) == []
        record = app.get(path, headers=headers()).json()
        assert record["snapshot"]["state"] == "queued" and record["snapshot"]["operations"] == []
        restored = app.get(path + "/definition", headers=headers())
        assert restored.status_code == 200 and restored.content == bridge.original
        definition = RunDefinition.model_validate_json(restored.content)
        assert definition.run_id == run_id and len(definition.library) == 6
        assert app.get(path, headers=headers()).json() == record


def test_input_reply_loss_stops_business_and_preserves_exact_original_bytes(configuration):
    service = Service(configuration)
    run_id = str(uuid4())
    path = "/p3/client-runs/" + run_id
    with TestClient(service.app()) as app:
        eventually(lambda: app.get("/p3/readyz").status_code == 200)

        class LoseInputReply(ForwardToP3):
            original = None

            def handle_request(self, request):
                response = super().handle_request(request)
                if request.method == "PUT" and request.url.path.startswith(path + "/inputs/"):
                    assert response.status_code == 200, response.text
                    self.original = request.content
                    raise httpx.ReadError("lost original input receipt", request=request)
                return response

        bridge = LoseInputReply(app, service.execution)
        demo = DemoService(P3ValidationClient("http://testserver", "alice", transport=bridge))
        try:
            demo.start(StartRequest(scenario_id="library-basic", request_id=run_id))
        finally:
            demo.close()
        assert bridge.original is not None, (
            "exact original bytes must be saved before business send"
        )
        assert business_writes(bridge) == []
        record = app.get(path, headers=headers()).json()
        assert record["snapshot"]["state"] == "unconfirmed"
        operation = ClientOperation.model_validate(record["snapshot"]["operations"][0])
        assert operation.phase == "prepared"
        assert operation.request_hash == sha256(bridge.original).hexdigest()
        observer = P3ValidationClient(
            "http://testserver", "alice", transport=ForwardToP3(app, service.execution)
        )
        try:
            assert observer.read_client_input(record["run_id"], operation) == bridge.original
        finally:
            observer.close()
        assert app.get(path, headers=headers()).json() == record


@pytest.mark.parametrize("lost", ["registration", "checkpoint", "remember"])
def test_lost_committed_reply_retains_original_run_and_prevents_reexecution(configuration, lost):
    service = Service(configuration)
    run_id = str(uuid4())
    path = "/p3/client-runs/" + run_id
    request = StartRequest(scenario_id="library-basic", request_id=run_id)

    with TestClient(service.app()) as app:
        eventually(lambda: app.get("/p3/readyz").status_code == 200)

        class LoseReply(ForwardToP3):
            response = None

            def handle_request(self, request):
                response = super().handle_request(request)
                target = {
                    "registration": ("POST", path),
                    "checkpoint": ("PUT", path),
                    "remember": ("POST", "/p3/remember"),
                }[lost]
                if self.response is None and (request.method, request.url.path) == target:
                    self.response = response
                    raise httpx.ReadError("injected reply loss after P3 responded", request=request)
                return response

        bridge = LoseReply(app, service.execution)
        demo = DemoService(P3ValidationClient("http://testserver", "alice", transport=bridge))
        try:
            if lost == "registration":
                with pytest.raises(ValidationError):
                    demo.start(request)
            else:
                demo.start(request)
        finally:
            demo.close()
        assert bridge.response is not None, "fault must occur after a real P3 response"
        durable = app.get(path, headers=headers())
        assert durable.status_code == 200, durable.text
        row = durable.json()
        writes = business_writes(bridge)
        if lost == "remember":
            assert len(writes) == 1
            assert row["snapshot"]["state"] == "unconfirmed"
            journal = row["snapshot"]["operations"]
            assert len(journal) == 1 and journal[0]["phase"] == "prepared"
            assert journal[0]["binding"]["target"] == "/p3/remember"
            assert journal[0]["binding"]["content_type"] == "application/json"
            assert journal[0]["operation_id"] == writes[0][2]
            original_job = bridge.response.headers["X-P3-Job-ID"]

            def completed():
                result = app.get("/p3/operations/" + original_job + "/result", headers=headers())
                return result if result.status_code == 200 else None

            assert eventually(completed).json()
        else:
            assert writes == []
            assert row["snapshot"]["state"] == ("queued" if lost == "registration" else "running")

        fresh_bridge = ForwardToP3(app, service.execution)
        if lost == "remember":
            observer = P3ValidationClient("http://testserver", "alice", transport=fresh_bridge)
            try:
                recovered_job = observer.lookup_operation(writes[0][2], "remember.save")
                assert recovered_job.state == "found" and recovered_job.job_id == original_job
                assert recovered_job.task_state == "succeeded"
                confirmed = observer.confirm_operation(ClientOperation.model_validate(journal[0]))
                assert confirmed.state == "matched"
                assert confirmed.lookup.job_id == original_job
            finally:
                observer.close()
        replacement = DemoService(
            P3ValidationClient("http://testserver", "alice", transport=fresh_bridge)
        )
        try:
            created, recovered = replacement.start_with_status(request)
            assert created is False
            assert recovered.run_id == run_id
            assert [item.model_dump(mode="json") for item in recovered.operations] == row[
                "snapshot"
            ].get("operations", [])
            with pytest.raises(ValidationError) as blocked:
                replacement.start(StartRequest(scenario_id="library-basic", request_id=uuid4()))
            assert blocked.value.status == 409
        finally:
            replacement.close()
        assert business_writes(fresh_bridge) == []
        assert app.get(path, headers=headers()).json() == row
