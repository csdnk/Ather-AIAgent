"""Resume provably unstarted P4 runs through actual P3 ownership and P2 definitions."""

from concurrent.futures import ThreadPoolExecutor
from threading import Event
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from tests.integration.test_current_p2_http import configuration as configuration
from tests.integration.test_current_p2_http import headers
from tests.integration.test_p4_demo_temporal import ForwardToP3, business_writes
from tests.integration.test_p4_execution_state import execute_interrupted

from aether_agent_memory.runtime.contracts.client_runs import ClientRunRecord
from aether_agent_memory.runtime.contracts.models import ErrorCode
from aether_agent_memory.runtime.foundation.common import FoundationError
from aether_agent_memory.runtime.foundation.postgres import (
    PostgresTransaction as PostgresTransaction,
)
from aether_p4_simulator.demo.definition import RunDefinition
from aether_p4_simulator.demo.models import RunSnapshot, StartRequest
from aether_p4_simulator.demo.service import DemoService
from aether_p4_simulator.demo.state import ExecutionData, encode_state
from aether_p4_simulator.validation.client import P3ValidationClient
from aether_p4_simulator.validation.errors import ValidationError
from azure_component_service import Service

pytestmark = pytest.mark.integration


def current(app, run_id):
    response = app.get(f"/p3/client-runs/{run_id}", headers=headers())
    assert response.status_code == 200, response.text
    return ClientRunRecord.model_validate(response.json())


def queued(app, service, monkeypatch, *, missing=False, incompatible=False, pending=False):
    run_id = uuid4()

    class Original(ForwardToP3):
        definition = None

        def handle_request(self, request):
            if request.method == "PUT" and request.url.path.endswith("/definition"):
                self.definition = request.content
                if missing:
                    raise httpx.ReadError("original definition not sent", request=request)
            return super().handle_request(request)

    bridge = Original(app, service.execution)
    first = DemoService(P3ValidationClient("http://testserver", "alice", transport=bridge))
    try:
        with monkeypatch.context() as patch:
            patch.setattr(first._worker, "submit", lambda *args: None)
            if pending:
                original_put = PostgresTransaction.put_if_revision

                def fail_confirmation(tx, ref, value, revision):
                    if ref.object_type == "client_run_definition" and value["state"] == "ready":
                        raise FoundationError(
                            ErrorCode.DEPENDENCY_UNAVAILABLE, "original confirmation interrupted"
                        )
                    return original_put(tx, ref, value, revision)

                patch.setattr(PostgresTransaction, "put_if_revision", fail_confirmation)
            if incompatible:
                patch.setattr(
                    "aether_p4_simulator.demo.definition.implementation_hash", lambda: "0" * 64
                )
            if missing or pending:
                with pytest.raises(ValidationError):
                    first.start(StartRequest(scenario_id="library-basic", request_id=run_id))
            else:
                first.start(StartRequest(scenario_id="library-basic", request_id=run_id))
    finally:
        first.close()
    assert not business_writes(bridge)
    return current(app, run_id), bridge.definition


def recoverer(app, service, *, bridge=None):
    bridge = bridge or ForwardToP3(app, service.execution)
    return DemoService(P3ValidationClient("http://testserver", "alice", transport=bridge)), bridge


@pytest.mark.parametrize("state", ["queued", "running", "unconfirmed"])
def test_original_initialization_runs_to_completion_without_recapturing_definition(
    configuration, monkeypatch, state
):
    service = Service(configuration)
    with TestClient(service.app()) as app:
        if state == "unconfirmed":
            run_id = uuid4()
            old = execute_interrupted(
                app, service, run_id, "library-basic", phase="initial", suffix="", committed=False
            )
            assert not business_writes(old)
            original = current(app, run_id)
            payload = app.get(f"/p3/client-runs/{run_id}/definition", headers=headers()).content
            assert original.snapshot["error"] is not None
        else:
            original, payload = queued(app, service, monkeypatch)
        fresh, bridge = recoverer(app, service)
        try:
            if state == "running":
                snapshot = RunSnapshot.model_validate(original.snapshot)
                snapshot.state = "running"
                original = fresh._registry.checkpoint(original, snapshot)
            assert original.execution_state is None and not original.snapshot["operations"]

            def forbidden(*args):
                pytest.fail("recovery must not recapture inputs or event times")

            monkeypatch.setattr(RunDefinition, "capture", forbidden)
            view = fresh.recover_initialization(str(original.run_id), "init-recovery")
            assert view.run_id == str(original.run_id)
        finally:
            fresh.close()
        final = current(app, original.run_id)
        assert final.snapshot["state"] == "passed", final.snapshot
        assert final.run_id == original.run_id and final.scope_id == original.scope_id
        assert final.definition == original.definition
        assert final.snapshot["error"] == original.snapshot["error"]
        assert final.owner_id == fresh._owner_id and not final.recovery_held
        assert (
            app.get(f"/p3/client-runs/{original.run_id}/definition", headers=headers()).content
            == payload
        )
        writes = business_writes(bridge)
        assert writes and len({call[2] for call in writes}) == len(writes)
        assert all(call[2].startswith(original.scope_id + "_") for call in writes)


def test_missing_original_definition_blocks_dispatch_until_exact_original_is_supplied(
    configuration, monkeypatch
):
    service = Service(configuration)
    with TestClient(service.app()) as app:
        original, payload = queued(app, service, monkeypatch, missing=True)
        fresh, bridge = recoverer(app, service)
        dispatched = []
        monkeypatch.setattr(fresh, "_execute", lambda *args: dispatched.append(args))
        try:
            with pytest.raises(ValidationError):
                fresh.recover_initialization(str(original.run_id), "init-recovery")
            assert not dispatched and current(app, original.run_id).recovery_held
            with pytest.raises(ValidationError):
                fresh.recover_initialization(
                    str(original.run_id), "init-recovery", original_definition=payload + b"wrong"
                )
            assert not dispatched
            fresh.recover_initialization(
                str(original.run_id), "init-recovery", original_definition=payload
            )
        finally:
            fresh.close()
        assert dispatched == [(str(original.run_id), original.scope_id)]
        assert not business_writes(bridge)


@pytest.mark.parametrize("stage", ["transfer", "confirmation", "activation"])
@pytest.mark.parametrize("committed", [False, True])
def test_lost_initialization_replies_resolve_same_intent_and_never_dispatch_early(
    configuration, monkeypatch, stage, committed
):
    service = Service(configuration)
    with TestClient(service.app()) as app:
        original, _ = queued(app, service, monkeypatch, pending=stage == "confirmation")

        class Lost(ForwardToP3):
            enabled = True
            attempts = []

            def handle_request(self, request):
                selected = (
                    request.method == "POST" and "/transfers/" in request.url.path
                    if stage == "transfer"
                    else request.method == "PUT" and "/recoveries/" in request.url.path
                    if stage == "confirmation"
                    else request.method == "POST" and "/recoveries/" in request.url.path
                )
                if selected:
                    self.attempts.append((request.url.raw_path, request.content))
                    if self.enabled:
                        if committed:
                            response = super().handle_request(request)
                            assert response.status_code in {200, 201}, response.text
                        raise httpx.ReadError("lost initialization reply", request=request)
                return super().handle_request(request)

        fresh, bridge = recoverer(app, service, bridge=Lost(app, service.execution))
        dispatched = []
        monkeypatch.setattr(fresh, "_execute", lambda *args: dispatched.append(args))
        try:
            if committed:
                fresh.recover_initialization(str(original.run_id), "init-recovery")
            else:
                with pytest.raises(ValidationError) as error:
                    fresh.recover_initialization(str(original.run_id), "init-recovery")
                assert error.value.write_outcome == "unconfirmed"
                assert not dispatched
                bridge.enabled = False
                fresh.recover_initialization(str(original.run_id), "init-recovery")
            fresh.recover_initialization(str(original.run_id), "init-recovery")
        finally:
            fresh.close()
        assert dispatched == [(str(original.run_id), original.scope_id)]
        assert len(bridge.attempts) == (1 if committed else 2)
        assert all(value == bridge.attempts[0] for value in bridge.attempts)
        assert not business_writes(bridge)


def test_concurrent_and_repeated_initialization_calls_submit_once(configuration, monkeypatch):
    service = Service(configuration)
    with TestClient(service.app()) as app:
        original, _ = queued(app, service, monkeypatch)
        fresh, bridge = recoverer(app, service)
        dispatched = []
        monkeypatch.setattr(fresh, "_execute", lambda *args: dispatched.append(args))
        try:
            with ThreadPoolExecutor(max_workers=2) as pool:
                jobs = [
                    pool.submit(fresh.recover_initialization, str(original.run_id), "init-recovery")
                    for _ in range(2)
                ]
                views = [job.result(timeout=30) for job in jobs]
            assert all(view.run_id == str(original.run_id) for view in views)
            fresh.recover_initialization(str(original.run_id), "init-recovery")
        finally:
            fresh.close()
        assert dispatched == [(str(original.run_id), original.scope_id)]
        assert (
            sum(method == "POST" and "/transfers/" in path for method, path, _ in bridge.calls) == 1
        )
        assert (
            sum(method == "POST" and "/recoveries/" in path for method, path, _ in bridge.calls)
            == 1
        )


@pytest.mark.parametrize("queued_before_failure", [False, True])
def test_failed_submission_can_retry_without_running_an_already_queued_wrapper(
    configuration, monkeypatch, queued_before_failure
):
    service = Service(configuration)
    with TestClient(service.app()) as app:
        original, _ = queued(app, service, monkeypatch)
        fresh, _ = recoverer(app, service)
        submit = fresh._worker.submit
        dispatched, attempts = [], []
        monkeypatch.setattr(fresh, "_execute", lambda *args: dispatched.append(args))

        def failing(*args, **kwargs):
            attempts.append(args)
            if len(attempts) == 1:
                if queued_before_failure:
                    submit(*args, **kwargs)
                raise RuntimeError("injected submit failure")
            return submit(*args, **kwargs)

        monkeypatch.setattr(fresh._worker, "submit", failing)
        try:
            with pytest.raises(ValidationError):
                fresh.recover_initialization(str(original.run_id), "init-recovery")
            fresh.recover_initialization(str(original.run_id), "init-recovery")
        finally:
            fresh.close()
        assert len(attempts) == 2 and dispatched == [(str(original.run_id), original.scope_id)]


@pytest.mark.parametrize("fault", ["head", "executor", "step"])
def test_initialized_or_incompatible_run_cannot_restart_whole_story(
    configuration, monkeypatch, fault
):
    service = Service(configuration)
    with TestClient(service.app()) as app:
        original, _ = queued(app, service, monkeypatch, incompatible=fault == "executor")
        fresh, bridge = recoverer(app, service)
        dispatched = []
        monkeypatch.setattr(fresh, "_execute", lambda *args: dispatched.append(args))
        try:
            if fault in {"head", "step"}:
                snapshot = RunSnapshot.model_validate(original.snapshot)
                snapshot.state = "running"
                if fault == "step":
                    snapshot.current_step = 1
                    snapshot.steps[0].response_text = "already observed"
                original = fresh._registry.checkpoint(original, snapshot)
            if fault == "head":
                original = fresh._registry.save_state(
                    original, encode_state(ExecutionData().envelope(original))
                )
            before = len(bridge.calls)
            with pytest.raises(ValidationError):
                fresh.recover_initialization(str(original.run_id), "init-recovery")
            assert not dispatched and not business_writes(bridge)
            if fault != "executor":
                assert all(method == "GET" for method, _, _ in bridge.calls[before:])
                assert current(app, original.run_id) == original
        finally:
            fresh.close()


def test_old_activation_receipt_cannot_restart_under_a_later_owner(configuration, monkeypatch):
    service = Service(configuration)
    with TestClient(service.app()) as app:
        original, _ = queued(app, service, monkeypatch)
        fresh, _ = recoverer(app, service)
        dispatched = []
        monkeypatch.setattr(fresh, "_execute", lambda *args: dispatched.append(args))
        try:
            fresh.recover_initialization(str(original.run_id), "init-recovery")
            with fresh._lock:
                record = current(app, original.run_id)
                moved = fresh._client.transfer_client_run(
                    record, new_owner_id="later-owner", transfer_id="later-recovery"
                )
                with pytest.raises(ValidationError):
                    fresh.recover_initialization(str(original.run_id), "init-recovery")
                assert current(app, original.run_id) == moved.record
        finally:
            fresh.close()
        assert len(dispatched) <= 1


def test_paused_original_executor_is_fenced_before_first_state_and_business(
    configuration, monkeypatch
):
    service = Service(configuration)
    with TestClient(service.app()) as app:
        first, old_bridge = recoverer(app, service)
        fresh, bridge = recoverer(app, service)
        entered, release = Event(), Event()
        original_save = first._registry.save_state
        run_id = uuid4()

        def paused(*args):
            entered.set()
            assert release.wait(40), "test must release the old executor"
            return original_save(*args)

        monkeypatch.setattr(first._registry, "save_state", paused)
        try:
            first.start(StartRequest(scenario_id="library-basic", request_id=run_id))
            assert entered.wait(20)
            assert current(app, run_id).snapshot["state"] == "running"
            fresh.recover_initialization(str(run_id), "init-recovery")
        finally:
            release.set()
            first.close()
            fresh.close()
        final = current(app, run_id)
        assert final.snapshot["state"] == "passed", final.snapshot
        assert final.owner_id == fresh._owner_id
        assert not business_writes(old_bridge) and business_writes(bridge)
