"""Deployment-owned command budgets are bounded and reach authenticated contexts."""

from datetime import datetime
from hashlib import sha256
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from aether_agent_memory.runtime.contracts.models import Permission, Principal, Scope
from aether_agent_memory.runtime.flows.config import ServiceConfiguration
from aether_agent_memory.runtime.flows.http import create_app
from azure_configuration_support import settings
from azure_test_runtime import create_runtime


def test_deployment_command_budget_reaches_authenticated_context(tmp_path, monkeypatch):
    config = ServiceConfiguration.model_validate(
        {**settings(tmp_path), "request_timeout_seconds": 300}
    )
    runtime = create_runtime(tmp_path / "unit.db", tmp_path / "cache", embedding_profile="injected")
    principal = Principal(
        principal_id="alice",
        home_scope=Scope(tenant_id="t1", application_id="app", user_id="alice", agent_id="agent"),
        permissions=tuple(Permission),
        auth_epoch=1,
    )
    identity = runtime.foundation.identity
    identity.provision([(sha256(b"alice").hexdigest(), principal)])
    captured = []
    original = identity.context_for_principal

    def capture(*args, **kwargs):
        value = original(*args, **kwargs)
        captured.append(value)
        return value

    monkeypatch.setattr(identity, "context_for_principal", capture)
    # Auth-only route does not enter an execution lifecycle or perform a command.
    execution = SimpleNamespace(state={})
    try:
        app = create_app(
            runtime, execution=execution, request_timeout_seconds=config.request_timeout_seconds
        )
        client = TestClient(app, raise_server_exceptions=True)
        response = client.get("/p3/auth/me", headers={"Authorization": "Bearer alice"})
        client.close()
        assert response.status_code == 200
        assert len(captured) == 1
        context = captured[0]
        seconds = (
            datetime.fromisoformat(context.deadline_at) - datetime.fromisoformat(identity.clock())
        ).total_seconds()
        assert 299 < seconds <= 300
    finally:
        runtime.close()


@pytest.mark.parametrize("seconds", [0, -1, 3601, float("inf")])
def test_deployment_rejects_unbounded_request_budget(tmp_path, seconds):
    with pytest.raises(ValidationError):
        ServiceConfiguration.model_validate(
            {**settings(tmp_path), "request_timeout_seconds": seconds}
        )
