"""Slow metadata must leave the transport loop available for heartbeats and deadlines."""

import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager

import pytest
from test_ledger import admit
from test_ledger import foundation as foundation
from test_ledger import ledger as ledger

from aether_agent_memory.runtime.temporal.activities import Activities
from aether_agent_memory.runtime.temporal.bridge import IntentBridge
from aether_agent_memory.runtime.temporal.locking import ExecutionLimits
from aether_agent_memory.runtime.temporal.models import CloseRequest, PeriodicState, StepResult
from aether_agent_memory.runtime.temporal.periodic import PeriodicActivities
from aether_agent_memory.runtime.temporal.registry import StageRegistry
from aether_agent_memory.runtime.temporal.sample import register_sample


@pytest.mark.parametrize("operation", ["plan", "close", "bridge", "periodic"])
async def test_metadata_wait_does_not_stop_transport_scheduling(
    foundation, ledger, monkeypatch, operation
):
    registry = StageRegistry()
    register_sample(registry, foundation.sample)
    job, _ = admit(foundation, ledger)
    if operation == "bridge":
        # Exercise the real bounded queue read without an external RPC.
        with foundation.uow.transaction() as tx:
            for key, row in tx.pending_intent_rows("start"):
                tx.write("temporal_start_intents", key, {**row, "state": "acknowledged"})
    original = foundation.uow.transaction
    loop = asyncio.get_running_loop()
    responsive = []

    @contextmanager
    def slow_transaction():
        # A dependency waits for an independently scheduled callback. On the
        # transport loop this cannot complete until the blocking wait expires.
        released = threading.Event()
        loop.call_soon_threadsafe(released.set)
        responsive.append(released.wait(0.15))
        with original() as tx:
            yield tx

    monkeypatch.setattr(foundation.uow, "transaction", slow_transaction)
    with ThreadPoolExecutor(2) as pool:
        activities = Activities(ledger, registry, ExecutionLimits(ledger.tasks.class_limits), pool)
        if operation == "plan":
            assert (await activities.plan(job)).first_stage == "commit"
        elif operation == "close":
            result = await activities.finish_workflow(
                CloseRequest(
                    job=job,
                    result=StepResult(
                        outcome="failed", effect_status="no_effect", reason_code="DEADLINE_EXCEEDED"
                    ),
                )
            )
            assert result.outcome == "failed"
        elif operation == "bridge":
            assert await IntentBridge(ledger, None).flush() == 0
        else:
            periodic = PeriodicActivities(ledger, None)
            periodic.register("due", lambda tx, key, tick, prepared: None)
            state = PeriodicState(deployment_id="test", last_tick=1)
            assert (await periodic.batch(state)).cursor is None
    assert responsive and all(responsive), "metadata waits blocked transport callbacks"
