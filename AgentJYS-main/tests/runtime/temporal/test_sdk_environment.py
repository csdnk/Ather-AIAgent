"""Our SDK connection must preserve typed payloads and committed execution history."""

from datetime import timedelta
from uuid import uuid4

import pytest
from pydantic import BaseModel, ValidationError
from temporalio import activity, workflow
from temporalio.worker import UnsandboxedWorkflowRunner, Worker

from aether_agent_memory.runtime.temporal.config import TemporalConfiguration
from aether_agent_memory.runtime.temporal.gateway import connect_client


class Packet(BaseModel):
    value: int


@activity.defn
async def echo(packet: Packet) -> Packet:
    return packet


@workflow.defn
class RoundtripWorkflow:
    @workflow.run
    async def run(self, packet: Packet) -> Packet:
        return await workflow.execute_activity(
            echo, packet, start_to_close_timeout=timedelta(seconds=5)
        )


def test_tls_client_credentials_must_be_a_pair(tmp_path):
    with pytest.raises(ValidationError):
        TemporalConfiguration(
            deployment_id="test", endpoint="localhost:7233", tls_cert_file=tmp_path / "cert"
        )


@pytest.mark.asyncio
async def test_sdk_roundtrip_and_persistent_restart(temporal_server):
    config = TemporalConfiguration(deployment_id="test", endpoint=temporal_server.endpoint)
    client = await connect_client(config)
    queue = "sdk-" + uuid4().hex
    async with Worker(
        client,
        task_queue=queue,
        workflows=[RoundtripWorkflow],
        activities=[echo],
        workflow_runner=UnsandboxedWorkflowRunner(),
    ):
        handle = await client.start_workflow(
            RoundtripWorkflow.run, Packet(value=7), id=queue, task_queue=queue
        )
        assert await handle.result() == Packet(value=7)
    temporal_server.stop()
    temporal_server.start()
    restarted = await connect_client(config)
    result = await restarted.get_workflow_handle(queue, result_type=Packet).result()
    assert result.model_dump() == {"value": 7}
    assert handle.first_execution_run_id is not None
