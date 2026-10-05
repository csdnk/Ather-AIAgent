"""Only committed intents cross the RPC boundary, with stable identity on retry."""

from uuid import uuid4

import pytest
from temporalio.common import WorkflowIDReusePolicy
from test_ledger import admit as admit_job
from test_ledger import delivery
from test_ledger import foundation as foundation
from test_ledger import ledger as ledger

from aether_agent_memory.runtime.contracts.models import ErrorCode
from aether_agent_memory.runtime.foundation.common import FoundationError
from aether_agent_memory.runtime.foundation.transactions import _active
from aether_agent_memory.runtime.temporal.bridge import IntentBridge
from aether_agent_memory.runtime.temporal.gateway import TemporalGateway
from aether_agent_memory.runtime.temporal.models import ControlIntent, StartIntent, StepRequest


def admit(app, ledger):
    return admit_job(app, ledger, uuid4().hex)


def pending(app):
    with app.uow.transaction() as tx:
        return StartIntent.model_validate(tx.pending_intent_rows("start")[0][1]["intent"])


@pytest.mark.asyncio
async def test_start_ack_loss_reuses_execution(foundation, ledger, workflow_client):
    job, _ = admit(foundation, ledger)
    gateway = TemporalGateway(workflow_client, ledger)
    created = []

    class LoseAck:
        async def start(self, intent):
            binding = await gateway.start(intent)
            created.append(binding)
            if len(created) == 1:
                raise TimeoutError("ACK lost after server commit")
            return binding

    bridge = IntentBridge(ledger, LoseAck())
    assert await bridge.flush() == 0
    # A new process/bridge sees the same pending intent.
    assert await IntentBridge(ledger, LoseAck()).flush() == 1
    assert len({b.workflow_id for b in created}) == 1
    assert len({b.first_run_id for b in created}) == 1
    with foundation.uow.transaction() as tx:
        assert tx.read("temporal_bindings", job.job_id)["binding"]["first_run_id"]
        assert tx.pending_intent_rows("start") == []


@pytest.mark.asyncio
async def test_bridge_never_calls_rpc_inside_transaction(foundation, ledger, workflow_client):
    admit(foundation, ledger)
    gateway = TemporalGateway(workflow_client, ledger)
    observations = []

    class Observe:
        async def start(self, intent):
            observations.append(_active.get())
            return await gateway.start(intent)

    assert await IntentBridge(ledger, Observe()).flush() == 1
    assert observations == [False]


@pytest.mark.asyncio
async def test_existing_workflow_with_wrong_binding_is_rejected(
    foundation, ledger, workflow_client
):
    job, _ = admit(foundation, ledger)
    workflow_id = delivery(job).workflow_id
    await workflow_client.start_workflow(
        "P3TaskWorkflow",
        job,
        id=workflow_id,
        task_queue="wrong",
        memo={"p3_binding": {"wrong": True}},
        id_reuse_policy=WorkflowIDReusePolicy.REJECT_DUPLICATE,
    )
    with pytest.raises(FoundationError) as error:
        await TemporalGateway(workflow_client, ledger).start(pending(foundation))
    assert error.value.code == ErrorCode.IDEMPOTENCY_CONFLICT
    assert await IntentBridge(ledger, TemporalGateway(workflow_client, ledger)).flush() == 0
    with foundation.uow.transaction() as tx:
        row = tx.rows("temporal_start_intents")[0][1]
        assert row["state"] == "attention_required"


@pytest.mark.asyncio
async def test_terminal_tombstone_prevents_start_after_history_removal(foundation, ledger):
    job, spec = admit(foundation, ledger)
    with foundation.uow.transaction() as tx:
        execution = ledger.begin(
            tx, StepRequest(job=job, stage="save", ordinal=0, mode="execute"), delivery(job)
        )
        output = spec.subject.model_copy(update={"object_type": "output"})
        tx.put_if_revision(output, {"value": 7}, None)
        ledger.complete(tx, job.job_id, execution, output)

    class NoStart:
        async def start(self, intent):
            pytest.fail("terminal task must not create another workflow")

    assert await IntentBridge(ledger, NoStart()).flush() == 1


@pytest.mark.asyncio
async def test_control_ack_loss_preserves_original_control(foundation, ledger, workflow_client):
    job, _ = admit(foundation, ledger)
    gateway = TemporalGateway(workflow_client, ledger)
    assert await IntentBridge(ledger, gateway).flush() == 1
    control = ControlIntent(
        control_id="cancel1", job_id=job.job_id, action="cancel", expected_revision=1
    )
    with foundation.uow.transaction() as tx:
        tx.write(
            "temporal_control_intents",
            control.control_id,
            {"state": "pending", "intent": control.model_dump(mode="json")},
        )
    sent = []

    class LoseControlAck:
        async def send_control(self, intent):
            await gateway.send_control(intent)
            sent.append(intent.control_id)
            if len(sent) == 1:
                raise TimeoutError("lost")

    assert await IntentBridge(ledger, LoseControlAck()).flush() == 0
    assert await IntentBridge(ledger, LoseControlAck()).flush() == 1
    assert sent == ["cancel1", "cancel1"]


@pytest.mark.asyncio
async def test_acknowledged_run_missing_does_not_start_new_execution(
    foundation, ledger, workflow_client
):
    job, _ = admit(foundation, ledger)
    gateway = TemporalGateway(workflow_client, ledger)
    intent = pending(foundation)
    binding = await gateway.start(intent)
    with foundation.uow.transaction() as tx:
        row = tx.read("temporal_bindings", job.job_id)
        tx.write(
            "temporal_bindings",
            job.job_id,
            {
                **row,
                "binding": binding.model_copy(
                    update={"current_run_id": "00000000-0000-0000-0000-000000000001"}
                ).model_dump(mode="json"),
            },
        )
    with pytest.raises(FoundationError) as error:
        await gateway.start(intent)
    assert error.value.code == ErrorCode.NOT_FOUND


@pytest.mark.asyncio
async def test_closed_workflow_id_is_not_reused(foundation, ledger, workflow_client):
    admit(foundation, ledger)
    gateway = TemporalGateway(workflow_client, ledger)
    intent = pending(foundation)
    first = await gateway.start(intent)
    await workflow_client.get_workflow_handle(first.workflow_id).terminate()
    repeated = await gateway.start(intent)
    assert repeated.first_run_id == first.first_run_id
    assert (await gateway.describe(repeated)).state == "terminated"
