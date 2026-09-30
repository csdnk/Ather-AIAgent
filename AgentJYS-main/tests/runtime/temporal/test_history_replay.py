import importlib
import importlib.util
from pathlib import Path

import pytest
from temporalio import workflow

from aether_agent_memory.runtime.temporal.models import StepResult, WorkflowInput
from aether_agent_memory.runtime.temporal.workflows import P3TaskWorkflow


@workflow.defn(name="P3TaskWorkflow")
class CompatibleWorkflow(P3TaskWorkflow):
    @workflow.query
    def implementation_revision(self):
        return "compatible-observability-change"

    @workflow.run
    async def run(self, job: WorkflowInput) -> StepResult:
        return await super().run(job)


@workflow.defn(name="P3TaskWorkflow")
class IncompatibleWorkflow(P3TaskWorkflow):
    @workflow.run
    async def run(self, job: WorkflowInput) -> StepResult:
        await workflow.sleep(1)  # Changes the first command from Activity to Timer.
        return await super().run(job)


def replay_api():
    name = "aether_agent_memory.runtime.temporal.replay"
    assert importlib.util.find_spec(name), "no offline history compatibility report"
    return importlib.import_module(name)


@pytest.mark.asyncio
async def test_old_history_replays_after_compatible_change():
    api = replay_api()
    histories = [Path(__file__).parent / "histories/task-plan-v1.json"]
    replay_failures = await api.replay_histories(histories, [CompatibleWorkflow])
    assert replay_failures == []


@pytest.mark.asyncio
async def test_periodic_history_before_control_wake_patch_replays():
    from aether_agent_memory.runtime.temporal.periodic_workflow import P3PeriodicWorkflow

    failures = await replay_api().replay_histories(
        [Path(__file__).parent / "histories/periodic-plan-v1.json"], [P3PeriodicWorkflow]
    )
    assert failures == []


@pytest.mark.asyncio
async def test_incompatible_history_is_reported_without_reexecution(monkeypatch):
    api = replay_api()
    from temporalio.client import Client

    new_business_effect_count = 0

    async def forbidden(*args, **kwargs):
        nonlocal new_business_effect_count
        new_business_effect_count += 1
        raise AssertionError("offline replay must never connect or execute Activities")

    monkeypatch.setattr(Client, "connect", forbidden)
    failures = await api.replay_histories(
        [Path(__file__).parent / "histories/task-plan-v1.json"], [IncompatibleWorkflow]
    )
    assert len(failures) == 1
    assert failures[0]["reason_code"] == "HISTORY_INCOMPATIBLE"
    assert failures[0]["error_type"] == "NondeterminismError"
    assert new_business_effect_count == 0
