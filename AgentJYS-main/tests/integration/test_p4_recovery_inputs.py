"""P4 confirms exact original inputs and treats lost or altered receipts as unknown."""

import httpx
import pytest
from fastapi.testclient import TestClient
from tests.integration.test_client_recovery_reads import recovery_object
from tests.integration.test_current_p2_http import configuration as configuration
from tests.integration.test_current_p2_http import eventually
from tests.integration.test_p4_demo_temporal import ForwardToP3, business_writes

from aether_agent_memory.runtime.contracts.client_runs import ClientOperation, ClientRunRecord
from aether_p4_simulator.validation.client import P3ValidationClient
from aether_p4_simulator.validation.errors import ValidationError
from azure_component_service import Service

pytestmark = pytest.mark.integration


@pytest.mark.parametrize(
    "fault",
    [
        None,
        "run_id",
        "operation_id",
        "request_hash",
        "binding_digest",
        "size_bytes",
        "state",
        "lost",
    ],
)
def test_original_input_confirmation_checks_receipt_and_never_replays_business(
    configuration, monkeypatch, fault
):
    service = Service(configuration)
    with TestClient(service.app()) as app:
        eventually(lambda: app.get("/p3/readyz").status_code == 200)
        raw, _, _, payload, _, _ = recovery_object(service, app, monkeypatch, "input")
        record = ClientRunRecord.model_validate(raw)
        intent = ClientOperation.model_validate(record.snapshot["operations"][0])
        puts = []

        class ReceiptFault(ForwardToP3):
            def handle_request(self, request):
                response = super().handle_request(request)
                if request.method != "PUT":
                    return response
                puts.append(request.url.path)
                assert response.status_code == 200, response.text
                if fault == "lost":
                    raise httpx.ReadError("injected lost receipt", request=request)
                if fault is None:
                    return response
                value = response.json()
                value[fault] = {
                    "run_id": "00000000-0000-0000-0000-000000000000",
                    "operation_id": "another-request",
                    "request_hash": "0" * 64,
                    "binding_digest": "0" * 64,
                    "size_bytes": len(payload) + 1,
                    "state": "pending",
                }[fault]
                return httpx.Response(200, json=value)

        transport = ReceiptFault(app, service.execution)
        client = P3ValidationClient("http://testserver", "alice", transport=transport)
        effect_observations = []
        try:
            if fault is None:
                with client.observe_effects(effect_observations.append):
                    receipt = client.confirm_recovery_input(record, intent)
                assert receipt.state == "ready" and receipt.size_bytes == len(payload)
                assert receipt.operation_id == "original-input"
            else:
                with (
                    client.observe_effects(effect_observations.append),
                    pytest.raises(ValidationError) as failure,
                ):
                    client.confirm_recovery_input(record, intent)
                assert failure.value.write_outcome == "unconfirmed"
                assert failure.value.operation_id == intent.operation_id
            observed = client.read_recovery_input(record, intent)
            assert observed.payload == payload and observed.evidence.state == "ready"
            assert client.get_client_run(record.run_id) == record
            assert puts == [
                f"/p3/client-runs/{record.run_id}/recoveries/recovery-1/inputs/original-input"
            ]
            assert business_writes(transport) == []
            assert effect_observations == []
        finally:
            client.close()
