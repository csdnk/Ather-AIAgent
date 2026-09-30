import asyncio
import importlib
import importlib.util
from uuid import uuid4

import pytest
from temporalio.worker import Replayer
from test_ledger import foundation as foundation
from test_ledger import ledger as ledger

from aether_agent_memory.runtime.contracts.models import EventEnvelope, Flow, RecordRef
from aether_agent_memory.runtime.foundation.common import fingerprint, now
from aether_agent_memory.runtime.temporal.bridge import IntentBridge
from aether_agent_memory.runtime.temporal.gateway import TemporalGateway
from aether_agent_memory.runtime.temporal.registry import StageRegistry
from aether_agent_memory.runtime.temporal.worker import WorkerHost
from aether_agent_memory.runtime.temporal.workflows import EventDeliveryWorkflow


def setup(foundation, ledger, apply):
    module = "aether_agent_memory.runtime.temporal.events"
    assert importlib.util.find_spec(module), "events have no Workflow admission"
    foundation.events.register_type("test.changed", lambda event: None)
    foundation.events.subscribe("test.changed", "counter", apply)
    registry = StageRegistry()
    importlib.import_module(module).register_events(registry, ledger, foundation.events)
    ctx = foundation.identity.context("alice", timeout_seconds=60)
    event = EventEnvelope(
        event_id=uuid4().hex,
        event_type="test.changed",
        producer=Flow.RUNTIME,
        subject=RecordRef(
            owner=Flow.RUNTIME,
            object_type="example",
            object_id="source",
            scope=ctx.principal.home_scope,
        ),
        subject_revision=1,
        occurred_at=now(),
        request_id=ctx.request_id,
        trace_id=ctx.trace_id,
        initiator_id=ctx.principal.principal_id,
        initiator_auth_epoch=ctx.principal.auth_epoch,
        payload={"amount": 1},
        payload_hash=fingerprint({"amount": 1}),
    )
    with foundation.uow.transaction() as tx:
        foundation.events.append(tx, ctx, event)
        key = fingerprint(["counter", event.event_id])
        assert tx.read("temporal_bindings", key)
    return registry, event, key


@pytest.mark.asyncio
async def test_consumer_commit_ack_loss_has_one_business_effect(
    foundation, ledger, workflow_client, monkeypatch
):
    def increment(tx, event):
        tx.write("counter", "value", (tx.read("counter", "value") or 0) + event.payload["amount"])

    registry, event, key = setup(foundation, ledger, increment)
    save_step = ledger.save_step
    interrupted = False

    def lose_ack(tx, step, *args, **kwargs):
        nonlocal interrupted
        if step.stage == "deliver" and not interrupted:
            interrupted = True
            assert tx.read("inbox", key)
            raise OSError("consumer commit succeeded, Activity response lost")
        return save_step(tx, step, *args, **kwargs)

    monkeypatch.setattr(ledger, "save_step", lose_ack)
    host = WorkerHost(workflow_client, ledger, registry)
    await host.start()
    try:
        await IntentBridge(ledger, TemporalGateway(workflow_client, ledger)).flush()
        handle = workflow_client.get_workflow_handle(f"p3/test/runtime.event_delivery/{key}")
        await asyncio.wait_for(handle.result(), 60)
        assert (await handle.describe()).workflow_type == "EventDeliveryWorkflow"
        await Replayer(
            workflows=[EventDeliveryWorkflow], data_converter=workflow_client.data_converter
        ).replay_workflow(await handle.fetch_history())
        with foundation.uow.transaction() as tx:
            applied_count = tx.read("counter", "value")
            assert applied_count == 1
            assert tx.read("deliveries", key)["state"] == "acknowledged"
            assert tx.read("inbox", key)["signature"] == fingerprint(event.model_dump(mode="json"))
    finally:
        await host.stop()


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["fingerprint", "subscriber", "permission", "rollback"])
async def test_event_fingerprint_conflict_stops_delivery(
    foundation, ledger, workflow_client, fault
):
    def increment(tx, event):
        tx.write("counter", "value", 1)
        if fault == "rollback":
            raise OSError("consumer transaction interrupted")

    registry, event, key = setup(foundation, ledger, increment)
    with foundation.uow.transaction() as tx:
        if fault == "fingerprint":
            row = tx.read("outbox", event.event_id)
            row["event"]["payload"]["amount"] = 2
            row["event"]["payload_hash"] = fingerprint({"amount": 2})
            row["signature"] = fingerprint(row["event"])
            tx.write("outbox", event.event_id, row)
        if fault == "permission":
            person = tx.read("identities", "alice")
            tx.write("identities", "alice", {**person, "enabled": False})
    if fault == "subscriber":
        foundation.events.consumers.clear()
    host = WorkerHost(workflow_client, ledger, registry)
    await host.start()
    try:
        await IntentBridge(ledger, TemporalGateway(workflow_client, ledger)).flush()
        await asyncio.wait_for(
            workflow_client.get_workflow_handle(f"p3/test/runtime.event_delivery/{key}").result(),
            60,
        )
        with foundation.uow.transaction() as tx:
            assert tx.read("deliveries", key)["state"] != "acknowledged"
            assert tx.read("inbox", key) is None
            assert tx.read("counter", "value") is None
    finally:
        await host.stop()
