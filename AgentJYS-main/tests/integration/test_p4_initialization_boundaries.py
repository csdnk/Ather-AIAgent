"""Initialization keeps old evidence, uncertain outcomes and explicit ownership boundaries."""

from copy import deepcopy
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from tests.integration.test_current_p2_http import configuration as configuration
from tests.integration.test_current_p2_http import headers
from tests.integration.test_p4_demo_temporal import ForwardToP3, business_writes
from tests.integration.test_p4_initialization_recovery import current, queued, recoverer

from aether_agent_memory.runtime.contracts.models import ErrorCode
from aether_agent_memory.runtime.foundation.client_states import ClientStates
from aether_agent_memory.runtime.foundation.common import FoundationError
from aether_agent_memory.runtime.foundation.postgres import (
    PostgresTransaction as PostgresTransaction,
)
from aether_p4_simulator.demo.models import RunSnapshot, StartRequest
from aether_p4_simulator.demo.state import ExecutionData
from aether_p4_simulator.validation.errors import ValidationError
from azure_component_service import Service

pytestmark = pytest.mark.integration


def test_invalid_transfer_id_does_not_reserve_a_local_recovery_attempt(configuration, monkeypatch):
    service = Service(configuration)
    with TestClient(service.app()) as app:
        original, _ = queued(app, service, monkeypatch)
        fresh, _ = recoverer(app, service)
        dispatched = []
        monkeypatch.setattr(fresh, "_execute", lambda *args: dispatched.append(args))
        try:
            with pytest.raises(ValidationError):
                fresh.recover_initialization(str(original.run_id), "../invalid")
            fresh.recover_initialization(str(original.run_id), "init-recovery")
        finally:
            fresh.close()
        assert dispatched == [(str(original.run_id), original.scope_id)]


@pytest.mark.parametrize("stage", ["transfer", "confirmation", "activation"])
def test_unavailable_evidence_after_lost_write_cannot_enable_dispatch(
    configuration, monkeypatch, stage
):
    service = Service(configuration)
    with TestClient(service.app()) as app:
        original, _ = queued(app, service, monkeypatch, pending=stage == "confirmation")

        class LostEvidence(ForwardToP3):
            committed = False
            enabled = True

            def handle_request(self, request):
                suffix = (
                    "/transfers/init-recovery"
                    if stage == "transfer"
                    else "/recoveries/init-recovery"
                )
                selected = request.url.path.endswith(
                    suffix + ("/definition" if stage == "confirmation" else "")
                )
                if self.enabled and selected:
                    if request.method in {"PUT", "POST"}:
                        response = super().handle_request(request)
                        assert response.status_code in {200, 201}, response.text
                        self.committed = True
                        raise httpx.ReadError("lost write reply", request=request)
                    if self.committed:
                        raise httpx.ReadError(
                            "original receipt currently unreadable", request=request
                        )
                return super().handle_request(request)

        fresh, bridge = recoverer(app, service, bridge=LostEvidence(app, service.execution))
        dispatched = []
        monkeypatch.setattr(fresh, "_execute", lambda *args: dispatched.append(args))
        try:
            with pytest.raises(ValidationError) as error:
                fresh.recover_initialization(str(original.run_id), "init-recovery")
            assert error.value.write_outcome == "unconfirmed" and not dispatched
            bridge.enabled = False
            fresh.recover_initialization(str(original.run_id), "init-recovery")
        finally:
            fresh.close()
        assert dispatched == [(str(original.run_id), original.scope_id)]
        assert not business_writes(bridge)


def test_original_pending_initial_state_is_preserved_after_real_recovery(
    configuration, monkeypatch
):
    service = Service(configuration)
    with TestClient(service.app()) as app:
        first, old_bridge = recoverer(app, service)
        run_id = uuid4()
        original_put = PostgresTransaction.put_if_revision

        def fail_initial_confirmation(tx, ref, value, revision):
            if ref.object_type == "client_run_state" and value["state"] == "ready":
                raise FoundationError(
                    ErrorCode.DEPENDENCY_UNAVAILABLE, "interrupted initial state confirmation"
                )
            return original_put(tx, ref, value, revision)

        with monkeypatch.context() as patch:
            patch.setattr(PostgresTransaction, "put_if_revision", fail_initial_confirmation)
            try:
                first.start(StartRequest(scenario_id="library-basic", request_id=run_id))
            finally:
                first.close()
        original = current(app, run_id)
        assert original.snapshot["state"] == "unconfirmed" and original.execution_state is None
        assert not business_writes(old_bridge)
        foundation = service.runtime.foundation
        ctx = foundation.identity.context("alice")
        ref = ClientStates.ref(ctx, run_id, 1)
        with foundation.uow.transaction() as tx:
            metadata = deepcopy(tx.get(ref))
        assert metadata["state"] == "pending"
        key = ClientStates.key(ref, metadata["binding"]["content_hash"])
        old_payload = service.execution.inputs.objects.get_object_sync(key)
        assert old_payload is not None
        fresh, _ = recoverer(app, service)
        try:
            fresh.recover_initialization(str(run_id), "init-recovery")
        finally:
            fresh.close()
        final = current(app, run_id)
        assert final.snapshot["state"] == "passed", final.snapshot
        assert final.snapshot["error"] == original.snapshot["error"]
        assert final.execution_state.stream_id == "init-recovery"
        with foundation.uow.transaction() as tx:
            assert tx.get(ref) == metadata
        assert service.execution.inputs.objects.get_object_sync(key) == old_payload
        old = app.get(f"/p3/client-runs/{run_id}/states/1", headers=headers())
        assert old.json()["code"] == "COMMIT_UNCONFIRMED"


def test_new_instance_requires_new_explicit_transfer_after_prior_activation(
    configuration, monkeypatch
):
    service = Service(configuration)
    with TestClient(service.app()) as app:
        original, _ = queued(app, service, monkeypatch)
        first, _ = recoverer(app, service)

        def reject(*args):
            raise RuntimeError("executor did not accept work")

        monkeypatch.setattr(first._worker, "submit", reject)
        try:
            with pytest.raises(ValidationError):
                first.recover_initialization(str(original.run_id), "init-recovery")
        finally:
            first.close()
        after = current(app, original.run_id)
        assert after.owner_id == first._owner_id and not after.recovery_held
        second, _ = recoverer(app, service)
        dispatched = []
        monkeypatch.setattr(second, "_execute", lambda *args: dispatched.append(args))
        try:
            with pytest.raises(ValidationError):
                second.recover_initialization(str(original.run_id), "init-recovery")
            second.recover_initialization(str(original.run_id), "second-recovery")
        finally:
            second.close()
        assert dispatched == [(str(original.run_id), original.scope_id)]
        assert current(app, original.run_id).owner_id == second._owner_id


def test_advanced_original_activation_is_returned_without_dispatch(configuration, monkeypatch):
    service = Service(configuration)
    with TestClient(service.app()) as app:
        original, _ = queued(app, service, monkeypatch)
        fresh, _ = recoverer(app, service)
        dispatched = []

        def reject(*args):
            raise RuntimeError("executor did not accept work")

        monkeypatch.setattr(fresh._worker, "submit", reject)
        monkeypatch.setattr(fresh, "_execute", lambda *args: dispatched.append(args))
        try:
            with pytest.raises(ValidationError):
                fresh.recover_initialization(str(original.run_id), "init-recovery")
            record = current(app, original.run_id)
            run = RunSnapshot.model_validate(record.snapshot)
            run.state = "running"
            advanced = fresh._registry.checkpoint(record, run)
            view = fresh.recover_initialization(str(original.run_id), "init-recovery")
            assert view.state == "running" and not dispatched
            assert current(app, original.run_id) == advanced
        finally:
            fresh.close()


def test_another_activation_intent_is_not_adopted_as_initialization(configuration, monkeypatch):
    service = Service(configuration)
    with TestClient(service.app()) as app:
        original, _ = queued(app, service, monkeypatch)
        fresh, _ = recoverer(app, service)
        dispatched = []
        monkeypatch.setattr(fresh, "_execute", lambda *args: dispatched.append(args))
        try:
            transfer = fresh._client.transfer_client_run(
                original, new_owner_id=fresh._owner_id, transfer_id="init-recovery"
            )
            other = fresh._client.activate_client_run(
                transfer.record, ExecutionData().envelope(transfer.record)
            )
            with pytest.raises(ValidationError):
                fresh.recover_initialization(str(original.run_id), "init-recovery")
            assert current(app, original.run_id) == other.record and not dispatched
        finally:
            fresh.close()
