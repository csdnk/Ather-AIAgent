"""Real process test runtime; durable effect evidence lives in the shared PG schema."""

import asyncio
import json
import os
from pathlib import Path

from aether_agent_memory.runtime.celery.config import CeleryConfiguration
from aether_agent_memory.runtime.celery.execution import CeleryExecution
from aether_agent_memory.runtime.contracts.models import EffectStatus, Permission
from aether_agent_memory.runtime.temporal.activities import StageContext
from aether_agent_memory.runtime.temporal.config import TemporalConfiguration
from aether_agent_memory.runtime.temporal.ledger import ExecutionLedger
from aether_agent_memory.runtime.temporal.models import StepResult
from aether_agent_memory.runtime.temporal.registry import StageRegistry
from azure_test_runtime import Foundation, OwnedResources, _resources


def factory():
    _resources.set(OwnedResources(json.loads(os.environ["CELERY_TEST_MANIFEST"])))
    foundation = Foundation(Path(os.environ["CELERY_TEST_DATABASE"]), engineering_profile=True)
    ledger = ExecutionLedger(
        foundation.tasks, TemporalConfiguration(deployment_id="test", endpoint="127.0.0.1:1")
    )
    registry = StageRegistry()

    async def execute(step):
        context = StageContext.current()
        ref = context.task.subject.model_copy(update={"object_type": "result"})
        with foundation.uow.transaction() as tx:
            context.guard(tx)
            count = tx.read("celery_test_effects", step.job.job_id) or 0
            tx.write("celery_test_effects", step.job.job_id, count + 1)
            tx.put_if_revision(ref, {"answer": 42}, None)
        if step.job.job_id == "kill_after_write":
            # Simulate SIGKILL after an external effect but before the stage receipt.
            os._exit(17)
        await asyncio.sleep(0.05)
        return StepResult(
            outcome="done",
            result_ref=ref,
            effect_status=EffectStatus.CONFIRMED,
            reason_code="SAVED",
        )

    async def reconcile(step):
        context = StageContext.current()
        ref = context.task.subject.model_copy(update={"object_type": "result"})
        with foundation.uow.transaction() as tx:
            context.guard(tx)
            evidence = tx.get(ref)
            tx.write("celery_test_queries", step.job.job_id, True)
        return (
            StepResult(
                outcome="done",
                result_ref=ref,
                effect_status=EffectStatus.CONFIRMED,
                reason_code="ORIGINAL_EFFECT_FOUND",
            )
            if evidence
            else StepResult(
                outcome="attention", effect_status=EffectStatus.UNKNOWN, reason_code="UNKNOWN"
            )
        )

    registry.register(
        "engineering.save",
        "write",
        execute,
        reconcile,
        Permission.WRITE,
        "uncertain",
        timeout_seconds=1,
    )
    engine = CeleryExecution(
        ledger, registry, CeleryConfiguration(repair_seconds=1, lease_grace_seconds=1)
    )
    ledger.backend_admission = engine.bind
    return engine
