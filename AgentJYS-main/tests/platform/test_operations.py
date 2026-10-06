"""Authorization, data minimization and safe command semantics for the ops API."""

from dataclasses import dataclass
from datetime import UTC, datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from aether_platform.directory import AccessDeniedError, Actor
from aether_platform.operations.api import install_operations
from aether_platform.operations.models import Command, public_request


@dataclass(frozen=True)
class TestActor(Actor):
    __test__ = False
    permissions: tuple[str, ...] = ("aether:ops:read",)


def actor(role="platform_admin", tenant=None):
    return TestActor("operator", "https://identity.test", "12", tenant, role, 1)


def test_command_rejects_extra_identity_and_arbitrary_url():
    with pytest.raises(ValidationError):
        Command(command_id="command-1", resource="tasks", action="recover", tenant_id="other")
    with pytest.raises(ValidationError):
        Command(command_id="command-1", resource="https://evil.test", action="get")


def test_request_projection_excludes_text_credentials_and_arbitrary_evidence():
    result = public_request(
        {
            "id": "turn-1",
            "input": "private question",
            "output": "private answer",
            "status": "complete",
            "error": "Bearer secret should never be exposed",
            "created_at": datetime.now(UTC),
            "memory_evidence": {
                "status": "saved",
                "token": "secret",
                "sources": [{"text": "private"}],
            },
        }
    )
    rendered = str(result)
    assert "private" not in rendered and "secret" not in rendered
    assert result["memory_status"] == "saved" and result["status"] == "complete"


class Verifier:
    def __init__(self, current):
        self.current = current

    def verify_ruoyi_actor(self, token):
        if token != "valid":
            raise AccessDeniedError("bad token")
        return self.current


class Service:
    def __init__(self):
        self.calls = []

    def read(self, current, token, resource, limit=50, offset=0):
        self.calls.append((current, token, resource, limit, offset))
        return {"items": [], "total": 0, "status": "ok"}


def client_for(current):
    app, service = FastAPI(), Service()
    install_operations(app, {}, None, Verifier(current), service=service)
    return TestClient(app), service


def test_api_rejects_missing_invalid_and_ordinary_user_identity():
    client, service = client_for(actor("user", "tenant-a"))
    assert client.get("/platform-ops/v1/requests").status_code == 401
    assert (
        client.get(
            "/platform-ops/v1/requests", headers={"Authorization": "Bearer forged"}
        ).status_code
        == 401
    )
    assert (
        client.get(
            "/platform-ops/v1/requests", headers={"Authorization": "Bearer valid"}
        ).status_code
        == 403
    )
    assert service.calls == []


def test_tenant_admin_cannot_access_global_resources_or_spoof_role():
    client, service = client_for(actor("tenant_admin", "tenant-a"))
    headers = {
        "Authorization": "Bearer valid",
        "X-Role": "platform_admin",
        "X-Tenant-Id": "tenant-b",
    }
    assert client.get("/platform-ops/v1/resources", headers=headers).status_code == 403
    assert client.get("/platform-ops/v1/requests", headers=headers).status_code == 200
    assert service.calls[-1][0].tenant_id == "tenant-a"


def test_api_bounds_pagination_and_rejects_unregistered_resources():
    client, service = client_for(actor())
    headers = {"Authorization": "Bearer valid"}
    assert client.get("/platform-ops/v1/requests?limit=5000", headers=headers).status_code == 422
    assert client.get("/platform-ops/v1/arbitrary", headers=headers).status_code == 422
    assert service.calls == []


def test_task_projection_preserves_control_revision_and_failure_metadata():
    from aether_platform.operations.service import Operations

    service = object.__new__(Operations)
    service.p3_call = lambda *args, **kwargs: {
        "items": [
            {
                "task_id": "task-1",
                "revision": 7,
                "owner_flow": "remember",
                "error_code": "PROVIDER_UNAVAILABLE",
                "effect_status": "unknown",
                "payload": "private content",
            }
        ],
        "next_cursor": "cursor-2",
    }
    result = service.read(actor(), "valid", "tasks")
    assert result["items"][0] == {
        "task_id": "task-1",
        "revision": 7,
        "owner_flow": "remember",
        "error_code": "PROVIDER_UNAVAILABLE",
        "effect_status": "unknown",
    }
    assert result["next_cursor"] == "cursor-2"
