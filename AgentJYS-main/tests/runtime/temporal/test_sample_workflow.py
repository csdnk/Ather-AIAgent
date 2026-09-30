import asyncio
from uuid import uuid4

import pytest
from test_ledger import foundation as foundation

from aether_agent_memory.runtime.temporal.config import TemporalConfiguration
from aether_agent_memory.runtime.temporal.ledger import ExecutionLedger
from aether_agent_memory.runtime.temporal.registry import StageRegistry


@pytest.mark.asyncio
async def test_explicit_engineering_profile_runs_on_temporal(foundation, workflow_client):
    import importlib.util

    assert importlib.util.find_spec("aether_agent_memory.runtime.temporal.sample"), (
        "explicit sample stage missing"
    )
    from aether_agent_memory.runtime.temporal.bridge import IntentBridge
    from aether_agent_memory.runtime.temporal.events import register_events
    from aether_agent_memory.runtime.temporal.gateway import TemporalGateway
    from aether_agent_memory.runtime.temporal.sample import register_sample
    from aether_agent_memory.runtime.temporal.worker import WorkerHost

    ledger = ExecutionLedger(
        foundation.tasks, TemporalConfiguration(deployment_id="test", endpoint="local:7233")
    )
    registry = StageRegistry()
    register_sample(registry, foundation.sample)
    register_events(registry, ledger, foundation.events)
    foundation.tasks.on_admitted = lambda tx, task: ledger.bind_admitted(tx, task)
    task = foundation.sample.submit(foundation.identity.context("alice"), uuid4().hex, "sample")
    workers = WorkerHost(workflow_client, ledger, registry)
    await workers.start()
    bridge = IntentBridge(ledger, TemporalGateway(workflow_client, ledger))
    try:
        async with asyncio.timeout(60):
            while True:
                await bridge.flush()
                with foundation.uow.transaction() as tx:
                    pending = tx.active_task_rows() or tx.pending_delivery_rows()
                if not pending:
                    break
                await asyncio.sleep(0.05)
        with foundation.uow.transaction() as tx:
            assert foundation.tasks.load(tx, task.task_id)[1].state == "succeeded"
            assert len(tx.rows("inbox")) == 1
    finally:
        await workers.stop()
