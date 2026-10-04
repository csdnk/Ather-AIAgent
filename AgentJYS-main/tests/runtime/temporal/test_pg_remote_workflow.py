"""Real PostgreSQL/Temporal, shared immutable port; no local input/body authority."""

import asyncio
import json
from hashlib import sha256
from uuid import uuid4

import pytest

from aether_agent_memory.remember.contracts.models import (
    CorrectionRequest,
    RememberRequest,
    SourceInput,
    TextInput,
)
from aether_agent_memory.runtime.contracts.models import Permission, Principal, Scope, ScopeSelector
from aether_agent_memory.runtime.foundation.common import now
from aether_agent_memory.runtime.temporal.bridge import IntentBridge
from aether_agent_memory.runtime.temporal.config import TemporalConfiguration
from aether_agent_memory.runtime.temporal.gateway import TemporalGateway
from aether_agent_memory.runtime.temporal.ingress import (
    CommandAdmission,
    InputStore,
    register_ingress,
)
from aether_agent_memory.runtime.temporal.ledger import ExecutionLedger
from aether_agent_memory.runtime.temporal.registry import StageRegistry
from aether_agent_memory.runtime.temporal.remember import register_remember
from aether_agent_memory.runtime.temporal.worker import WorkerHost
from azure_test_runtime import create_runtime, owned


@pytest.mark.asyncio
async def test_pg_admission_other_worker_and_restart_correction(tmp_path, workflow_client):
    dsn = owned().dsn(tmp_path / "shared-metadata")
    namespace = "test-" + uuid4().hex
    deployment = "pg-" + uuid4().hex
    endpoint = workflow_client.service_client.config.target_host
    config = TemporalConfiguration(deployment_id=deployment, endpoint=endpoint)
    token = uuid4().hex
    scope = Scope(tenant_id="t-" + uuid4().hex, application_id="app", user_id="u", agent_id="bot")
    principal = Principal(
        principal_id="p-" + uuid4().hex,
        auth_epoch=1,
        permissions=tuple(Permission),
        home_scope=scope,
    )
    hosts = []

    def instance(name):
        folder = tmp_path / name
        owned().provider_namespaces[str((folder / "not-created.db").resolve())] = namespace
        runtime = create_runtime(
            folder / "not-created.db",
            folder / "cache",
            body_root=folder / "bodies",
            postgres_dsn=dsn,
            embedding_profile="injected",
        )
        hosts.append(runtime)
        objects = runtime.remember.bodies.p2
        ledger = ExecutionLedger(runtime.foundation.tasks, config)
        inputs = InputStore(
            runtime.foundation.uow, runtime.foundation.identity, folder / "inputs", objects=objects
        )
        registry = StageRegistry()
        register_ingress(registry, ledger, inputs, runtime.remember)
        register_remember(registry, runtime.remember)
        return runtime, ledger, inputs, registry, CommandAdmission(ledger, inputs)

    api, api_ledger, api_inputs, _, admission = instance("api")
    api.foundation.identity.provision([(sha256(token.encode()).hexdigest(), principal)])
    ctx = api.foundation.identity.context(token, operation_id=uuid4().hex, timeout_seconds=90)
    request = RememberRequest(
        source=SourceInput(
            kind="conversation", external_id=uuid4().hex, external_version="1", occurred_at=now()
        ),
        selection=ScopeSelector(session_id="s"),
        content=TextInput(kind="text", text="First source survives different worker."),
    )
    ref = api_inputs.persist(
        ctx, ctx.operation_id, request.model_dump_json().encode(), "application/json"
    )
    job = admission.accept(ctx, "remember.save", ref)
    worker, ledger, _, registry, _ = instance("worker")
    engine = WorkerHost(workflow_client, ledger, registry)
    await engine.start()
    try:
        await IntentBridge(api_ledger, TemporalGateway(workflow_client, api_ledger)).flush()
        await asyncio.wait_for(
            workflow_client.get_workflow_handle(
                f"p3/{deployment}/{job.kind}/{job.job_id}"
            ).result(),
            60,
        )
        receipt = admission.result(ctx, job.job_id)
        assert receipt and receipt["saved"]
        memory_id = receipt["memories"][0]["memory_id"]
    finally:
        await engine.stop()
        api.close()
        worker.close()
        hosts.clear()

    fresh, fresh_ledger, inputs, registry, commands = instance("restarted")
    ctx = fresh.foundation.identity.context(token, operation_id=uuid4().hex, timeout_seconds=90)
    assert fresh.remember.get(ctx, memory_id).content == request.content.text
    fresh.remember.bodies.verified.clear()
    correction = CorrectionRequest(
        expected_version=1,
        source=request.source.model_copy(update={"external_version": "2"}),
        content="Corrected immutable source from a new process.",
        reason="source updated",
    )
    ref = inputs.persist(
        ctx,
        ctx.operation_id,
        json.dumps(
            {"memory_id": memory_id, "request": correction.model_dump(mode="json")}
        ).encode(),
        "application/json",
    )
    corrected = commands.accept(ctx, "remember.correct", ref)
    engine = WorkerHost(workflow_client, fresh_ledger, registry)
    await engine.start()
    try:
        await IntentBridge(fresh_ledger, TemporalGateway(workflow_client, fresh_ledger)).flush()
        await asyncio.wait_for(
            workflow_client.get_workflow_handle(
                f"p3/{deployment}/{corrected.kind}/{corrected.job_id}"
            ).result(),
            60,
        )
        result = commands.result(ctx, corrected.job_id)
        assert result and result["memories"][0]["version"] == 2
        current = fresh.remember.get(ctx, memory_id)
        assert current.content == correction.content
        assert current.content_hash == sha256(correction.content.encode()).hexdigest()
        for name in ("api", "worker", "restarted"):
            assert not list((tmp_path / name / "inputs").iterdir())
            assert not list((tmp_path / name / "bodies").iterdir())
            assert not list((tmp_path / name).glob("*.db"))
    finally:
        await engine.stop()
        for runtime in hosts:
            runtime.close()


@pytest.mark.asyncio
async def test_pg_periodic_hydrates_without_committing_probe_effects(tmp_path, workflow_client):
    from aether_agent_memory.operate.basic.continuous import ContinuousOperate
    from aether_agent_memory.operate.basic.maintenance import CacheMaintenance
    from aether_agent_memory.remember.basic.policy import RememberPolicy
    from aether_agent_memory.remember.contracts.models import RetentionRequest
    from aether_agent_memory.runtime.foundation.common import later
    from aether_agent_memory.runtime.temporal.models import PeriodicState
    from aether_agent_memory.runtime.temporal.periodic import (
        PeriodicActivities,
        register_p3_periodic,
    )

    dsn = owned().dsn(tmp_path / "shared-metadata")
    namespace = "test-" + uuid4().hex
    for name in ("first", "second"):
        owned().provider_namespaces[str((tmp_path / name / "none.db").resolve())] = namespace
    policy = RememberPolicy(consolidation_messages=1000, consolidation_seconds=3600)
    options = dict(
        postgres_dsn=dsn,
        embedding_profile="injected",
        remember_policy=policy,
        operate_factory=ContinuousOperate,
    )
    first = create_runtime(tmp_path / "first" / "none.db", tmp_path / "first" / "cache", **options)
    token = uuid4().hex
    principal = Principal(
        principal_id="p-" + uuid4().hex,
        auth_epoch=1,
        permissions=tuple(Permission),
        home_scope=Scope(
            tenant_id="t-" + uuid4().hex, application_id="a", user_id="u", agent_id="bot"
        ),
    )
    first.foundation.identity.provision([(sha256(token.encode()).hexdigest(), principal)])
    ctx = first.foundation.identity.context(token)
    receipt = await first.remember.save(
        ctx,
        RememberRequest(
            source=SourceInput(
                kind="conversation",
                external_id=uuid4().hex,
                external_version="1",
                occurred_at=now(),
            ),
            selection=ScopeSelector(session_id="s"),
            content=TextInput(kind="text", text="expiry owned by periodic flow"),
        ),
    )
    item = first.remember.get(ctx, receipt.memories[0].memory_id)
    first.remember.retention.configure(
        ctx,
        item.ref.memory_id,
        RetentionRequest(
            expected_version=item.ref.version,
            expected_object_revision=item.object_revision,
            completed=True,
            expires_at=later(now(), 60),
            reason="explicit expiry",
        ),
    )
    first.close()
    second = create_runtime(
        tmp_path / "second" / "none.db", tmp_path / "second" / "cache", **options
    )
    second.foundation.identity.clock = lambda: later(now(), 120)
    config = TemporalConfiguration(
        deployment_id="periodic-" + uuid4().hex,
        endpoint=workflow_client.service_client.config.target_host,
    )
    ledger = ExecutionLedger(second.foundation.tasks, config)
    maintenance = CacheMaintenance(second)
    runner = PeriodicActivities(ledger, None)
    register_p3_periodic(runner, second, maintenance, ())
    # Limit this regression to the body's lifecycle route. Other routes have their own suites.
    runner.routes = {
        "remember_retention_enrollment": runner.routes["remember_retention_enrollment"]
    }
    try:
        state = PeriodicState(deployment_id=config.deployment_id, last_tick=1)
        await runner.batch(state)
        ctx = second.foundation.identity.context(token)
        item = second.remember.get(ctx, item.ref.memory_id)
        assert item.status == "expired"
        with second.foundation.uow.transaction() as tx:
            lifecycle = [
                row for _, row in tx.rows("tasks") if row["record"]["kind"] == "remember.cleanup"
            ]
        assert len(lifecycle) == 1
        await runner.batch(state)
        with second.foundation.uow.transaction() as tx:
            assert (
                len(
                    [
                        row
                        for _, row in tx.rows("tasks")
                        if row["record"]["kind"] == "remember.cleanup"
                    ]
                )
                == 1
            )
        assert not list(second.remember.bodies.root.iterdir())
    finally:
        second.close()
