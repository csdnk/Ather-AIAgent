import asyncio
import threading
import time
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient
from test_ledger import admit
from test_ledger import foundation as foundation
from test_ledger import ledger as ledger
from test_temporal_http import configuration, headers, wait_ready

from aether_agent_memory.recall.contracts.models import RecallRequest
from aether_agent_memory.runtime.contracts.models import EffectStatus
from aether_agent_memory.runtime.flows.application import Service
from aether_agent_memory.runtime.temporal.models import StepResult


def until(check, seconds=60):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        value = check()
        if value:
            return value
        time.sleep(0.03)
    raise AssertionError("control did not converge")


@pytest.mark.asyncio
async def test_control_intent_rolls_back_and_revocation_blocks_transfer(
    foundation, ledger, workflow_client
):
    from aether_agent_memory.runtime.foundation.common import FoundationError
    from aether_agent_memory.runtime.temporal.bridge import IntentBridge
    from aether_agent_memory.runtime.temporal.controls import ControlAdmission, ControlRequest
    from aether_agent_memory.runtime.temporal.gateway import TemporalGateway

    job, _ = admit(foundation, ledger)
    gateway = TemporalGateway(workflow_client, ledger)
    await IntentBridge(ledger, gateway).flush()
    controls = ControlAdmission(ledger, ())
    ctx = foundation.identity.context("alice")
    request = ControlRequest(
        operation_id="atomic", action="reconcile", expected_revision=1, reason="original only"
    )
    with pytest.raises(RuntimeError), foundation.uow.transaction() as tx:
        controls.task(tx, ctx, job.job_id, request)
        raise RuntimeError("before commit")
    with foundation.uow.transaction() as tx:
        assert tx.read("operations", "atomic") is None
        assert tx.rows("temporal_control_intents") == []
        assert ledger.tasks.load(tx, job.job_id)[1].revision == 1
        controls.task(tx, ctx, job.job_id, request)
    with foundation.uow.transaction() as tx:
        identity = tx.read("identities", "alice")
        tx.write("identities", "alice", {**identity, "enabled": False})
    assert await IntentBridge(ledger, gateway).flush() == 0
    with foundation.uow.transaction() as tx:
        assert tx.read("operations", "atomic")["record"]["state"] == "failed"
        assert "FORBIDDEN" in tx.read("operations", "atomic")["record"]["reason"]
    with pytest.raises(FoundationError), foundation.uow.transaction() as tx:
        controls.task(tx, ctx, job.job_id, request)


@pytest.mark.parametrize("action", ["reconcile", "cancel"])
def test_http_control_reaches_original_workflow_after_ack_loss(
    tmp_path, temporal_server, monkeypatch, action
):
    service = Service(configuration(tmp_path, temporal_server.endpoint))
    entered, release = threading.Event(), threading.Event()
    calls, sent = [], []

    async def execute(step):
        calls.append("execute")
        entered.set()
        while not release.is_set():
            await asyncio.sleep(0.01)
        return StepResult(
            outcome="retry", effect_status=EffectStatus.NO_EFFECT, reason_code="TEST_RETRY"
        )

    async def reconcile(step):
        calls.append("reconcile")
        while not release.is_set():
            await asyncio.sleep(0.01)
        return StepResult(
            outcome="failed", effect_status=EffectStatus.NO_EFFECT, reason_code="TEST_RECONCILED"
        )

    routes = service.execution.registry.routes["recall.execute"]
    routes["prepare"] = replace(routes["prepare"], execute=execute, reconcile=reconcile)
    with TestClient(service.app()) as client:
        wait_ready(client, service)
        saved = client.post(
            "/p3/recall",
            json={"query": "control", "sources": "long_term", "selection": {}},
            headers=headers(),
        )
        job_id = saved.headers["x-p3-job-id"]
        assert entered.wait(60)
        task = client.get(f"/p3/tasks/{job_id}", headers=headers()).json()
        with service.runtime.foundation.uow.transaction() as tx:
            original = tx.read("temporal_bindings", job_id)["binding"]
        gateway = service.execution.gateway
        real_send = gateway.send_control

        async def lose_ack(intent):
            await real_send(intent)
            sent.append(intent.control_id)
            if len(sent) == 1:
                raise TimeoutError("lost control ACK")

        monkeypatch.setattr(gateway, "send_control", lose_ack)
        body = {
            "operation_id": "control-original",
            "expected_revision": task["revision"],
            "reason": "verify original effect",
        }
        if action == "reconcile":
            path = "/p3/recovery"
            body["task_id"] = job_id
        else:
            path = f"/p3/tasks/{job_id}/control"
            body["action"] = action
        assert client.post(path, json=body, headers=headers("eve")).status_code == 403
        bad = {**body, "expected_revision": task["revision"] + 99}
        assert client.post(path, json=bad, headers=headers()).status_code == 409
        response = client.post(path, json=body, headers=headers())
        assert response.status_code == 200, response.text
        with service.runtime.foundation.uow.transaction() as tx:
            intents = tx.rows("temporal_control_intents")
            assert len(intents) == 1, "HTTP must atomically persist a control intent"
            assert intents[0][1]["intent"]["job_id"] == job_id
        repeated = client.post(path, json=body, headers=headers())
        assert repeated.status_code == 200, repeated.text
        until(lambda: len(sent) >= 2)
        assert len(set(sent)) == 1
        release.set()
        until(lambda: "reconcile" in calls)
        assert calls.count("execute") == 1
        handle = service.execution.client.get_workflow_handle(original["workflow_id"])
        state = client.portal.call(handle.query, "state")
        assert state["acknowledged_controls"] == [sent[0]]
        with service.runtime.foundation.uow.transaction() as tx:
            assert tx.read("temporal_bindings", job_id)["binding"] == original
            assert len(tx.rows("temporal_control_intents")) == 1


def test_closed_original_control_is_reported_and_never_restarted(tmp_path, temporal_server):
    service = Service(configuration(tmp_path, temporal_server.endpoint))
    with TestClient(service.app()) as client:
        wait_ready(client, service)
        # Admit a real job while its lane is deliberately paused at the Worker boundary.
        worker = service.execution.workers
        client.portal.call(worker.stop)
        job = service.execution.recall.accept(
            service.runtime.foundation.identity.context("alice"),
            RecallRequest(query="closed", sources="long_term", selection={}),
        )
        client.portal.call(service.execution.bridge.flush)
        with service.runtime.foundation.uow.transaction() as tx:
            binding = tx.read("temporal_bindings", job.job_id)["binding"]
        handle = service.execution.client.get_workflow_handle(binding["workflow_id"])
        client.portal.call(handle.terminate)
        # Use the production transactional admission even when HTTP readiness is degraded.
        from aether_agent_memory.runtime.contracts.models import RecoveryRequest

        ctx = service.runtime.foundation.identity.context("alice")
        with service.runtime.foundation.uow.transaction() as tx:
            task = service.execution.ledger.tasks.load(tx, job.job_id)[1]
            service.runtime.foundation.tasks.request_recovery(
                tx,
                ctx,
                RecoveryRequest(
                    operation_id="closed-control",
                    task_id=job.job_id,
                    expected_revision=task.revision,
                    reason="original chain",
                ),
            )
        client.portal.call(service.execution.bridge.flush)
        result = client.get("/p3/controls/closed-control", headers=headers())
        assert result.status_code == 200, result.text
        assert result.json()["state"] == "failed"
        assert "CLOSED" in result.json()["reason"] or "INVALID_ARGUMENT" in result.json()["reason"]
        with service.runtime.foundation.uow.transaction() as tx:
            assert tx.read("temporal_bindings", job.job_id)["binding"] == binding


def test_periodic_controls_require_deployment_operator_and_keep_chain(tmp_path, temporal_server):
    config = configuration(tmp_path, temporal_server.endpoint).model_copy(
        update={"maintenance_principals": ("alice",), "periodic_seconds": 60}
    )
    service = Service(config)
    with TestClient(service.app()) as client:
        wait_ready(client, service)
        url = "/p3/periodic/control"
        assert client.get(url, headers=headers("eve")).status_code == 403
        snapshot = client.get(url, headers=headers())
        assert snapshot.status_code == 200, snapshot.text
        handle = service.execution.client.get_workflow_handle(
            snapshot.json()["binding"]["workflow_id"]
        )
        first_tick = until(
            lambda: (
                value
                if (value := client.portal.call(handle.query, "state"))["last_tick"] >= 0
                else None
            )
        )["last_tick"]
        until(
            lambda: any(
                event.HasField("timer_started_event_attributes")
                for event in client.portal.call(handle.fetch_history).events
            )
        )
        body = {
            "operation_id": "periodic-control",
            "action": "reconcile",
            "expected_revision": snapshot.json()["revision"],
            "reason": "request next bounded scan",
        }
        assert client.post(url, json=body, headers=headers("eve")).status_code == 403
        accepted = client.post(url, json=body, headers=headers())
        assert accepted.status_code == 200, accepted.text
        assert client.post(url, json=body, headers=headers()).status_code == 200
        until(
            lambda: (
                client.get("/p3/controls/periodic-control", headers=headers()).json().get("state")
                == "completed"
            )
        )
        with service.runtime.foundation.uow.transaction() as tx:
            binding = tx.read("temporal_periodic_binding", config.temporal.deployment_id)["binding"]
        assert binding["first_run_id"] == snapshot.json()["binding"]["first_run_id"]
        handle = service.execution.client.get_workflow_handle(binding["workflow_id"])
        received = client.portal.call(handle.query, "state")
        assert len(received["acknowledged_controls"]) == 1
        until(
            lambda: client.portal.call(handle.query, "state")["last_tick"] > first_tick, seconds=60
        )
        # A normal timer expiry must not masquerade as a successful control wake.
        history = client.portal.call(handle.fetch_history)
        assert any(event.HasField("timer_canceled_event_attributes") for event in history.events)
