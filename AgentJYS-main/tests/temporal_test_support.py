"""Explicit component-test seeding through a real Temporal server.

Domain fixtures may create task rows before assembling their Worker. Only this
test helper binds those seeded rows; production uses atomic admission.
No legacy scheduler is used.
"""

import asyncio

from aether_agent_memory.operate.basic.maintenance import CacheMaintenance
from aether_agent_memory.remember.basic.pipeline import RememberPipeline
from aether_agent_memory.runtime.foundation.common import fingerprint
from aether_agent_memory.runtime.temporal.activities import StageContext
from aether_agent_memory.runtime.temporal.bridge import IntentBridge
from aether_agent_memory.runtime.temporal.config import (
    TemporalConfiguration,
    deployment_configuration,
)
from aether_agent_memory.runtime.temporal.events import register_events
from aether_agent_memory.runtime.temporal.gateway import TemporalGateway, connect_client
from aether_agent_memory.runtime.temporal.ledger import ExecutionLedger
from aether_agent_memory.runtime.temporal.models import StepResult
from aether_agent_memory.runtime.temporal.operate import register_operate
from aether_agent_memory.runtime.temporal.registry import StageRegistry
from aether_agent_memory.runtime.temporal.remember import register_remember
from aether_agent_memory.runtime.temporal.worker import WorkerHost


class SeededTemporalDriver:
    def __init__(self, runtime, endpoint):
        self.runtime, self.endpoint = runtime, endpoint

    async def drain(self, timeout_seconds=60):
        runtime = self.runtime
        tasks, events = runtime.foundation.tasks, runtime.foundation.events
        handlers, permissions = dict(tasks.handlers), dict(tasks.permissions)
        old_admit, old_delivery = tasks.on_admitted, events.on_delivery
        dispositions = runtime.foundation.dispositions
        old_catalog = {
            name: dict(getattr(dispositions, name))
            for name in ("signals", "operations", "samplers", "verifiers")
        }
        pipeline = isinstance(runtime.remember, RememberPipeline)
        prepared = runtime.remember.bodies.require_prepared if pipeline else None
        config = deployment_configuration(
            TemporalConfiguration(
                deployment_id=fingerprint(str(tasks.uow.path.resolve()))[:32],
                endpoint=self.endpoint,
            )
        )
        ledger = ExecutionLedger(tasks, config)
        registry = StageRegistry()
        workers = None
        try:
            if pipeline:
                register_remember(registry, runtime.remember)
            else:
                # Basic domain-unit regressions only. Production uses the complete
                # pipeline's phase adapters above, tested in test_remember_workflows.
                for kind, (_, handler) in handlers.items():
                    if not kind.startswith("remember."):
                        continue

                    async def commit(step, handler=handler):
                        c = StageContext.current()
                        result = await handler.run(c.context, c.task)
                        return StepResult(
                            outcome={
                                "committed": "done",
                                "uncertain": "query",
                                "retryable_no_effect": "retry",
                                "obsolete": "obsolete",
                                "failed": "failed",
                            }[result.outcome],
                            effect_status=result.effect_status,
                            result_ref=result.result_ref,
                            original_operation_id=result.operation_id,
                            reason_code="COMPONENT_RESULT",
                        )

                    async def reconcile(step, handler=handler):
                        c = StageContext.current()
                        result = await handler.recover(c.context, c.task)
                        with tasks.uow.transaction() as tx:
                            current = tasks.load(tx, c.task.task_id)[1]
                        if current.state == "succeeded":
                            return StepResult(
                                outcome="done",
                                result_ref=current.result_ref,
                                effect_status=current.effect_status,
                                reason_code="COMPONENT_COMMITTED",
                            )
                        return StepResult(
                            outcome={
                                "resume": "retry",
                                "query_only": "query",
                                "cancel": "obsolete",
                                "fail": "failed",
                                "attention": "attention",
                            }[result.action],
                            effect_status=result.effect_status,
                            original_operation_id=result.original_operation_id,
                            reason_code="COMPONENT_RECOVERY",
                        )

                    registry.register(
                        kind,
                        "component_commit",
                        commit,
                        reconcile,
                        tasks.permissions[kind],
                        "uncertain",
                    )
            existing = tasks.handlers.get("operate_repair_cache")
            maintenance = existing[1].handler if existing else CacheMaintenance(runtime)
            register_operate(registry, runtime.operate, maintenance)
            tasks.handlers.pop("runtime.event_delivery", None)
            events.on_delivery = None
            admission = register_events(registry, ledger, events)
            tasks.on_admitted = lambda tx, task: ledger.bind_admitted(tx, task)
            # Deliberately seed the component fixture, never production work.
            with tasks.uow.transaction() as tx:
                for key, row in tx.active_task_rows():
                    assert row["record"]["kind"] in registry.routes
                    ledger.bind_admitted(tx, tasks.load(tx, key)[1])
                from aether_agent_memory.runtime.contracts.models import (
                    EventEnvelope,
                    TrustedContext,
                )

                for _, row in tx.pending_delivery_rows():
                    original = tx.read("outbox", row["event_id"])
                    admission.admit(
                        tx,
                        TrustedContext.model_validate(original["context"]),
                        EventEnvelope.model_validate(original["event"]),
                        row["consumer_id"],
                    )
            client = await connect_client(config)
            gateway = TemporalGateway(client, ledger)
            bridge = IntentBridge(ledger, gateway)
            workers = WorkerHost(client, ledger, registry, shutdown_seconds=2)
            await workers.start()
            async with asyncio.timeout(timeout_seconds):
                while True:
                    await bridge.flush()
                    with tasks.uow.transaction() as tx:
                        pending = tx.active_task_rows() or tx.pending_delivery_rows()
                        bindings = tx.rows("temporal_bindings")
                    if not pending:
                        for _, binding in bindings:
                            await client.get_workflow_handle(binding["workflow_id"]).result()
                        break
                    await asyncio.sleep(0.03)
        finally:
            if workers:
                await workers.stop()
            tasks.handlers, tasks.permissions = handlers, permissions
            tasks.on_admitted, events.on_delivery = old_admit, old_delivery
            for name, entries in old_catalog.items():
                setattr(dispositions, name, entries)
            if pipeline:
                runtime.remember.bodies.require_prepared = prepared


def seed_driver(runtime, server):
    runtime.execution = SeededTemporalDriver(runtime, server.endpoint)
    return runtime


def http_execution(runtime, endpoint):
    """Full phase adapters for HTTP component tests with provisioned identities."""
    from aether_agent_memory.runtime.foundation.common import fingerprint
    from aether_agent_memory.runtime.temporal.service import TemporalService
    from component_configuration import ComponentConfiguration as ServiceConfiguration

    config = ServiceConfiguration(
        temporal=TemporalConfiguration(
            deployment_id=fingerprint(str(runtime.foundation.uow.path))[:32], endpoint=endpoint
        ),
        data_dir=runtime.foundation.uow.path.parent,
        identity_file=runtime.foundation.uow.path.parent / "test-identities.yaml",
        embedding_profile="injected",
        poll_seconds=0.01,
        periodic_seconds=0.1,
    )
    return TemporalService(runtime, config, lambda: None, CacheMaintenance(runtime))


BUSINESS_KINDS = {
    "remember.save", "remember.document", "remember.correct", "remember.extract",
    "remember.project", "remember.cleanup", "remember.compress", "remember.distill",
    "remember.revalidate", "remember.summarize", "recall.execute", "operate.evaluate",
    "operate_repair_cache", "runtime.event_delivery",
}
