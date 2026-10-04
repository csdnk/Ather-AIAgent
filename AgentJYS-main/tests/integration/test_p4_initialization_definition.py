"""The P4 confirmation consumer validates the original receipt and preserves uncertainty."""

import httpx
import pytest
from fastapi.testclient import TestClient
from tests.integration.test_client_recovery_reads import recovery_object
from tests.integration.test_current_p2_http import configuration as configuration
from tests.integration.test_p4_demo_temporal import ForwardToP3, business_writes

from aether_agent_memory.runtime.contracts.client_runs import ClientRunRecord
from aether_p4_simulator.validation.client import P3ValidationClient
from aether_p4_simulator.validation.errors import ValidationError
from azure_component_service import Service

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("ready", [False, True])
@pytest.mark.parametrize("supply", [False, True])
def test_p4_confirms_original_definition_without_activation_or_dispatch(
    configuration, monkeypatch, ready, supply
):
    service = Service(configuration)
    with TestClient(service.app()) as app:
        raw, _, _, payload, _, _ = recovery_object(
            service, app, monkeypatch, "definition", ready=ready
        )
        record = ClientRunRecord.model_validate(raw)
        transport = ForwardToP3(app, service.execution)
        client = P3ValidationClient("http://testserver", "alice", transport=transport)
        try:
            receipt = client.confirm_recovery_definition(record, payload if supply else None)
            assert receipt.run_id == record.run_id and receipt.state == "ready"
            assert receipt.content_hash == record.definition.content_hash
            assert client.read_recovery_definition(record).evidence.state == "ready"
            assert client.read_client_definition(record) == payload
            assert client.get_client_run(record.run_id) == record
            assert client.lookup_client_recovery(record.run_id, "recovery-1").state == "unconfirmed"
            assert business_writes(transport) == []
        finally:
            client.close()


@pytest.mark.parametrize("fault", ["hash", "size", "run", "state", "format"])
def test_p4_does_not_accept_altered_confirmation_receipt(configuration, monkeypatch, fault):
    service = Service(configuration)
    with TestClient(service.app()) as app:
        raw, _, _, _, _, _ = recovery_object(service, app, monkeypatch, "definition")
        record = ClientRunRecord.model_validate(raw)

        class Altered(ForwardToP3):
            def handle_request(self, request):
                response = super().handle_request(request)
                assert request.method == "PUT" and response.status_code == 200, response.text
                value = response.json()
                if fault == "hash":
                    value["content_hash"] = "0" * 64
                elif fault == "size":
                    value["size_bytes"] += 1
                elif fault == "run":
                    value["run_id"] = "00000000-0000-0000-0000-000000000000"
                elif fault == "format":
                    value["format_id"] = "different"
                else:
                    value["state"] = "pending"
                return httpx.Response(200, json=value)

        client = P3ValidationClient(
            "http://testserver", "alice", transport=Altered(app, service.execution)
        )
        try:
            with pytest.raises(ValidationError) as error:
                client.confirm_recovery_definition(record)
            assert error.value.code == "upstream_protocol_error"
            assert error.value.write_outcome == "unconfirmed"
        finally:
            client.close()


@pytest.mark.parametrize("committed", [False, True])
def test_lost_confirmation_is_observed_by_original_get_and_never_retried(
    configuration, monkeypatch, committed
):
    service = Service(configuration)
    with TestClient(service.app()) as app:
        raw, _, _, payload, _, _ = recovery_object(service, app, monkeypatch, "definition")
        record = ClientRunRecord.model_validate(raw)

        class Lost(ForwardToP3):
            attempts = 0

            def handle_request(self, request):
                if request.method == "PUT":
                    self.attempts += 1
                    if committed:
                        response = super().handle_request(request)
                        assert response.status_code == 200, response.text
                    raise httpx.ReadError("lost confirmation response", request=request)
                return super().handle_request(request)

        transport = Lost(app, service.execution)
        client = P3ValidationClient("http://testserver", "alice", transport=transport)
        try:
            with pytest.raises(ValidationError) as error:
                client.confirm_recovery_definition(record)
            assert error.value.write_outcome == "unconfirmed"
            evidence = client.read_recovery_definition(record)
            assert evidence.payload == payload
            assert evidence.evidence.state == ("ready" if committed else "pending")
            assert transport.attempts == 1
            assert client.get_client_run(record.run_id) == record
            assert business_writes(transport) == []
        finally:
            client.close()


@pytest.mark.parametrize("fault", ["binding", "payload", "hold"])
def test_p4_rejects_local_confirmation_contradictions_before_network(
    configuration, monkeypatch, fault
):
    service = Service(configuration)
    with TestClient(service.app()) as app:
        raw, _, _, payload, _, _ = recovery_object(service, app, monkeypatch, "definition")
        record = ClientRunRecord.model_validate(raw)
        if fault == "binding":
            record = record.model_copy(update={"definition": None})
        elif fault == "hold":
            record = record.model_copy(
                update={
                    "ownership": record.ownership.model_copy(update={"recovery_transfer_id": None})
                }
            )
        else:
            payload += b"different"

        def forbidden(request):
            pytest.fail("contradictory original definition must not reach the network")

        client = P3ValidationClient(
            "http://testserver", "alice", transport=httpx.MockTransport(forbidden)
        )
        try:
            with pytest.raises(ValidationError):
                client.confirm_recovery_definition(record, payload)
        finally:
            client.close()
