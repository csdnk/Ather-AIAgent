"""P4 activation confirms a held original run without dispatching its business story."""

from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from tests.integration.test_client_recoveries import held
from tests.integration.test_client_states import running
from tests.integration.test_current_p2_http import configuration as configuration
from tests.integration.test_p4_demo_temporal import ForwardToP3, business_writes
from tests.integration.test_p4_execution_state import execute_interrupted

from aether_agent_memory.runtime.contracts.client_runs import ClientRunRecord
from aether_p4_simulator.demo.service import DemoService
from aether_p4_simulator.demo.state import ExecutionData, encode_state
from aether_p4_simulator.validation.client import P3ValidationClient
from aether_p4_simulator.validation.errors import ValidationError
from azure_component_service import Service

pytestmark = pytest.mark.integration


def test_p4_can_activate_first_state_with_explicit_null_parent(configuration):
    service = Service(configuration)
    with TestClient(service.app()) as app:
        record = ClientRunRecord.model_validate(held(app, running(app)))
        client = P3ValidationClient(
            "http://testserver", "alice", transport=ForwardToP3(app, service.execution)
        )
        try:
            result = client.activate_client_run(record, ExecutionData().envelope(record))
            assert result.record.execution_state.sequence == 1
            assert result.record.execution_state.parent_hash is None
            assert client.read_client_state(result.record)
        finally:
            client.close()


@pytest.mark.parametrize("lose_reply", [False, True])
def test_p4_activation_reads_original_state_stream_without_replaying_business(
    configuration, lose_reply
):
    service = Service(configuration)
    run_id = uuid4()
    with TestClient(service.app()) as app:
        execute_interrupted(
            app, service, run_id, "library-full", phase="parsed", suffix="_1_upload", committed=True
        )

        class ActivationTransport(ForwardToP3):
            def handle_request(self, request):
                response = super().handle_request(request)
                if lose_reply and request.method == "POST" and "/recoveries/" in request.url.path:
                    assert response.status_code == 201, response.text
                    raise httpx.ReadError("injected activation reply loss", request=request)
                return response

        bridge = ActivationTransport(app, service.execution)
        client = P3ValidationClient("http://testserver", "alice", transport=bridge)
        fresh = DemoService(client)
        try:
            report = fresh.reconcile_execution(str(run_id))
            transfer = fresh.transfer_execution(report, "activate")
            held_report = fresh.reconcile_execution(str(run_id))
            if lose_reply:
                with pytest.raises(ValidationError) as error:
                    fresh.activate_execution(held_report)
                assert error.value.write_outcome == "unconfirmed"
                found = client.lookup_client_recovery(run_id, "activate")
                assert found.state == "committed"
                result = found.result
            else:
                result = fresh.activate_execution(held_report)
            assert result.previous == transfer.record
            assert not result.record.recovery_held
            assert result.record.snapshot == {**transfer.record.snapshot, "state": "running"}
            assert result.record.execution_state.stream_id == "activate"
            restored = fresh.restore_execution(str(run_id))
            assert restored.record == result.record and restored.journal_matches
            assert restored.data == report.restored.data
            assert not fresh._runs and fresh._active is None and business_writes(bridge) == []
            # Ordinary P4 writes after activation must continue the same stream.
            payload = encode_state(restored.data.envelope(restored.record))
            advanced = client.save_client_state(restored.record, payload)
            assert client.read_client_state(advanced) == payload
            assert advanced.execution_state.parent_stream_id == "activate"
        finally:
            fresh.close()
