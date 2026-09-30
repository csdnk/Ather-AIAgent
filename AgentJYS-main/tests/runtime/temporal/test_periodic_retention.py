from types import SimpleNamespace

import pytest
from temporalio.client import WorkflowExecutionStatus
from temporalio.service import RPCError, RPCStatusCode
from test_ledger import foundation as foundation
from test_ledger import ledger as ledger

from aether_agent_memory.runtime.foundation.common import FoundationError, fingerprint
from aether_agent_memory.runtime.temporal.gateway import TemporalGateway
from aether_agent_memory.runtime.temporal.models import PeriodicState, WorkflowBinding
from aether_agent_memory.runtime.temporal.periodic import PeriodicController


@pytest.mark.asyncio
@pytest.mark.parametrize("mismatch", [None, "chain", "closed", "memo", "queue", "type"])
async def test_reattach_periodic_after_old_run_retention(foundation, ledger, mismatch):
    state = PeriodicState(deployment_id="test")
    plan = state.model_dump(mode="json", exclude={"last_tick", "cursor", "acknowledged_controls"})
    digest = fingerprint(plan)
    binding = WorkflowBinding(
        namespace="default",
        workflow_id="p3/test/periodic",
        first_run_id="first",
        current_run_id="first",
        input_hash=digest,
        plan_version="1",
    )
    with foundation.uow.transaction() as tx:
        tx.write(
            "temporal_periodic_binding",
            "test",
            {"plan": plan, "binding": binding.model_dump(mode="json")},
        )

    async def memo_value(key, default):
        assert key == "p3_periodic"
        return "wrong" if mismatch == "memo" else digest

    class Client:
        namespace = "default"
        starts = 0

        def get_workflow_handle(self, workflow_id, *, run_id=None):
            assert workflow_id == binding.workflow_id

            async def describe(**kwargs):
                if run_id == "first":
                    raise RPCError("retained history expired", RPCStatusCode.NOT_FOUND, b"")
                return SimpleNamespace(
                    status=WorkflowExecutionStatus.COMPLETED
                    if mismatch == "closed"
                    else WorkflowExecutionStatus.RUNNING,
                    raw_info=SimpleNamespace(
                        first_run_id="different" if mismatch == "chain" else "first"
                    ),
                    run_id="current",
                    workflow_type="wrong" if mismatch == "type" else "P3PeriodicWorkflow",
                    task_queue="wrong" if mismatch == "queue" else "p3.periodic",
                    memo_value=memo_value,
                )

            return SimpleNamespace(describe=describe)

        async def start_workflow(self, *args, **kwargs):
            self.starts += 1
            raise AssertionError("never replace a bound chain")

    client = Client()
    controller = PeriodicController(TemporalGateway(client, ledger))
    if mismatch:
        with pytest.raises(FoundationError):
            await controller.start(state)
    else:
        resumed = await controller.start(state)
        assert resumed.first_run_id == "first" and resumed.current_run_id == "current"
        with foundation.uow.transaction() as tx:
            assert tx.read("temporal_periodic_binding", "test")["binding"] == resumed.model_dump(
                mode="json"
            )
    assert client.starts == 0
