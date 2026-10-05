"""A fresh P4 owner acquires only a recovery hold, including after a lost reply."""

from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from tests.integration.test_current_p2_http import configuration as configuration
from tests.integration.test_current_p2_http import headers
from tests.integration.test_p4_demo_temporal import ForwardToP3, business_writes
from tests.integration.test_p4_execution_state import execute_interrupted

from aether_p4_simulator.demo.service import DemoService
from aether_p4_simulator.validation.client import P3ValidationClient
from aether_p4_simulator.validation.errors import ValidationError
from azure_component_service import Service

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("lose_reply", [False, True])
def test_fresh_p4_owner_reads_original_results_after_transfer_without_dispatch(
    configuration, lose_reply
):
    service = Service(configuration)
    run_id = uuid4()
    with TestClient(service.app()) as app:
        execute_interrupted(
            app, service, run_id, "library-full", phase="parsed", suffix="_1_upload", committed=True
        )

        class TransferTransport(ForwardToP3):
            transfers = 0

            def handle_request(self, request):
                response = super().handle_request(request)
                if request.method == "POST" and "/transfers/" in request.url.path:
                    self.transfers += 1
                    if lose_reply:
                        assert response.status_code == 201, response.text
                        raise httpx.ReadError("injected transfer reply loss", request=request)
                return response

        bridge = TransferTransport(app, service.execution)
        client = P3ValidationClient("http://testserver", "alice", transport=bridge)
        fresh = DemoService(client)
        try:
            prior = fresh.reconcile_execution(str(run_id))
            if lose_reply:
                with pytest.raises(ValidationError) as error:
                    fresh.transfer_execution(prior, "handoff")
                assert error.value.write_outcome == "unconfirmed"
                found = client.lookup_client_transfer(run_id, "handoff")
                assert found.state == "committed"
                result = found.result
            else:
                result = fresh.transfer_execution(prior, "handoff")
            assert result.previous == prior.restored.record and result.record.recovery_held
            assert result.record.owner_id != result.previous.owner_id
            assert bridge.transfers == 1 and business_writes(bridge) == []
            restored = fresh.reconcile_execution(str(run_id))
            assert restored.restored.record == result.record
            assert restored.findings[0].state == "result_available"
            assert restored.findings[0].admission_state == "matched"
            assert restored.findings[0].observed_result == prior.findings[0].observed_result
            assert restored.restored.data == prior.restored.data
            assert not fresh._runs and fresh._active is None
            assert app.get(
                f"/p3/client-runs/{run_id}", headers=headers()
            ).json() == result.record.model_dump(mode="json")
        finally:
            fresh.close()
