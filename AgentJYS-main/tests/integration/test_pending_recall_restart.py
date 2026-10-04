"""An unchanged generation provider must bootstrap with its original pending job."""

import asyncio
import json

import pytest
from fastapi.testclient import TestClient
from tests.integration.test_current_p2_http import configuration as configuration
from tests.integration.test_current_p2_http import eventually, headers
from tests.integration.test_operation_lookup import body

from aether_agent_memory.recall.contracts.models import RecallRequest
from aether_agent_memory.runtime.contracts.models import RecordRef
from azure_component_service import Service

pytestmark = pytest.mark.integration


def test_pending_generation_recall_bootstraps_and_finishes_original_job(configuration):
    service = Service(configuration)
    try:
        ctx = service.runtime.foundation.identity.context("alice", operation_id="pending-recall")
        job = service.execution.recall.accept(
            ctx, RecallRequest(query="coffee", selection=body()["selection"], sources="working")
        )
        with service.runtime.foundation.uow.transaction() as tx:
            original = tx.read("tasks", job.job_id)
            binding = tx.read("temporal_bindings", job.job_id)
            assert tx.read("recall_requests", job.job_id)["record"]["state"] == "accepted"
    finally:
        asyncio.run(service.close())

    with TestClient(Service(configuration).app()) as client:

        def completed():
            response = client.get("/p3/operations/" + job.job_id, headers=headers())
            assert response.status_code == 200, response.text
            value = response.json()
            return (
                value if value["state"] in {"succeeded", "failed", "attention_required"} else None
            )

        recovered = eventually(completed)
        assert recovered["state"] == "succeeded", recovered
        assert recovered["task_id"] == job.job_id
        assert recovered["input_hash"] == original["record"]["input_hash"]
        assert recovered["temporal"]["workflow_id"] == binding["workflow_id"]
        result = client.get("/p3/operations/" + job.job_id + "/result", headers=headers())
        assert result.status_code == 200, result.text


@pytest.mark.parametrize("change", ["missing_task", "input_hash", "policy"])
def test_pending_recall_rejects_missing_or_changed_original_binding(
    configuration, change, tmp_path
):
    service = Service(configuration)
    try:
        ctx = service.runtime.foundation.identity.context("alice", operation_id="bound-pending")
        job = service.execution.recall.accept(
            ctx, RecallRequest(query="coffee", selection=body()["selection"], sources="working")
        )
        with service.runtime.foundation.uow.transaction() as tx:
            if change == "missing_task":
                tx.raw.delete("p3_rf_tasks", "system", job.job_id)
            elif change == "input_hash":
                row = tx.read("tasks", job.job_id)
                ref = RecordRef.model_validate(row["record"]["input_ref"])
                value = tx.get(ref)
                tx.put_if_revision(ref, {**value, "binding": "changed"}, 1)
            else:
                config = tmp_path / "changed-recall.json"
                config.write_text(json.dumps({"max_items": 6}), encoding="utf-8")
                configuration = configuration.model_copy(update={"recall_config": config})
    finally:
        asyncio.run(service.close())
    with pytest.raises(ValueError, match="cannot switch Recall"):
        Service(configuration)
