"""P4 consumes exact recovery evidence without promoting or replaying objects."""

import json

import httpx
import pytest
from fastapi.testclient import TestClient
from tests.integration.test_client_recovery_reads import recovery_object
from tests.integration.test_current_p2_http import configuration as configuration
from tests.integration.test_p4_demo_temporal import ForwardToP3, business_writes

from aether_agent_memory.runtime.contracts.client_runs import ClientOperation, ClientRunRecord
from aether_p4_simulator.validation.client import P3ValidationClient
from aether_p4_simulator.validation.errors import ValidationError
from azure_component_service import Service

pytestmark = pytest.mark.integration


def read_recovered(client, record, kind):
    if kind == "definition":
        return client.read_recovery_definition(record)
    if kind == "input":
        return client.read_recovery_input(
            record, ClientOperation.model_validate(record.snapshot["operations"][0])
        )
    return client.read_recovery_state(record, 1)


@pytest.mark.parametrize("kind", ["definition", "input", "state"])
@pytest.mark.parametrize("ready", [False, True])
def test_p4_reads_original_recovery_bytes_without_confirming_them(
    configuration, monkeypatch, kind, ready
):
    service = Service(configuration)
    with TestClient(service.app()) as app:
        raw, _, _, payload, _, _ = recovery_object(service, app, monkeypatch, kind, ready=ready)
        record = ClientRunRecord.model_validate(raw)
        transport = ForwardToP3(app, service.execution)
        client = P3ValidationClient("http://testserver", "alice", transport=transport)
        try:
            result = read_recovered(client, record, kind)
            assert result.payload == payload
            assert result.evidence.state == ("ready" if ready else "pending")
            assert client.get_client_run(record.run_id) == record
            assert business_writes(transport) == []
        finally:
            client.close()


@pytest.mark.parametrize("kind", ["definition", "input", "state"])
@pytest.mark.parametrize(
    "fault", ["bytes", "transfer", "record", "kind", "binding", "state", "missing"]
)
def test_p4_refuses_changed_recovery_evidence(configuration, monkeypatch, kind, fault):
    service = Service(configuration)
    with TestClient(service.app()) as app:
        raw, _, _, _, _, _ = recovery_object(service, app, monkeypatch, kind)
        record = ClientRunRecord.model_validate(raw)

        class ChangedEvidence(ForwardToP3):
            def handle_request(self, request):
                response = super().handle_request(request)
                assert request.method == "GET"
                assert response.status_code == 200, response.text
                evidence = json.loads(response.headers["X-P3-Recovery-Object"])
                payload, output_headers = response.content, dict(response.headers)
                if fault == "bytes":
                    payload = b"changed"
                elif fault == "transfer":
                    evidence["transfer_id"] = "wrong"
                elif fault == "record":
                    evidence["record_hash"] = "0" * 64
                elif fault == "kind":
                    evidence["kind"] = "input" if kind != "input" else "state"
                elif fault == "binding":
                    evidence["binding"]["size_bytes"] += 1
                elif fault == "state":
                    evidence["state"] = "published"
                output_headers["x-p3-recovery-object"] = json.dumps(evidence)
                if fault == "missing":
                    del output_headers["x-p3-recovery-object"]
                return httpx.Response(200, content=payload, headers=output_headers)

        client = P3ValidationClient(
            "http://testserver", "alice", transport=ChangedEvidence(app, service.execution)
        )
        try:
            with pytest.raises(ValidationError) as error:
                read_recovered(client, record, kind)
            assert error.value.code == "upstream_protocol_error"
            assert error.value.write_outcome == "not_applicable"
        finally:
            client.close()
