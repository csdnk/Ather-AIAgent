"""Operational HTTP acceptance on disposable state, never the live demo instance."""

import asyncio

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from aether_agent_memory.runtime.contracts.foundation import ConfigurationSnapshot
from aether_agent_memory.runtime.foundation.common import fingerprint, now
from aether_p4_simulator.validation.calls import ROUTES
from azure_component_service import Service
from integration.test_p4_demo_temporal import configuration, until

pytestmark = pytest.mark.integration


def test_inventory_matches_registered_p3_method_paths(tmp_path):
    """A newly installed or removed route must not leave the Web inventory stale."""
    service = Service(configuration(tmp_path, "127.0.0.1:1"))
    try:
        registered = {
            (method, route.path)
            for route in service.app().routes
            if isinstance(route, APIRoute) and route.path.startswith("/p3/")
            for method in route.methods
        }
        assert len(ROUTES) == len(set(ROUTES))
        assert set(ROUTES) == registered
    finally:
        asyncio.run(service.close())


def snapshot(version):
    values = dict(
        version=version,
        deployment_id="handoff",
        provider_ids=("postgresql", "redis", "milvus", "ceph"),
        policy_versions=("test",),
        secret_refs=(),
    )
    return ConfigurationSnapshot(
        **values, config_hash=fingerprint(values), activated_at=now()
    ).model_dump(mode="json")


def test_operational_http_restore_is_isolated_and_version_checked(tmp_path, temporal_server):
    """PostgreSQL online restore is refused while authorization and CAS remain intact."""
    config = configuration(tmp_path, temporal_server.endpoint).model_copy(
        update={"maintenance_principals": ("alice",)}
    )
    service = Service(config)
    operator = {"Authorization": "Bearer alice"}
    other = {"Authorization": "Bearer eve"}
    with TestClient(service.app()) as client:
        until(lambda: client.get("/p3/readyz").status_code, lambda status: status == 200)
        first = {"snapshot": snapshot("handoff_1"), "expected_version": None}
        assert client.put("/p3/configuration", json=first, headers=other).status_code == 403
        activated = client.put("/p3/configuration", json=first, headers=operator)
        assert activated.status_code == 200
        assert activated.json()["version"] == "handoff_1"

        backup_request = {"backup_id": "handoff_snapshot"}
        assert client.post("/p3/backups", json=backup_request, headers=other).status_code == 403
        response = client.post("/p3/backups", json=backup_request, headers=operator)
        assert response.status_code == 400, response.text
        assert response.json()["code"] == "CONTRACT_VIOLATION"

        second = {"snapshot": snapshot("handoff_2"), "expected_version": None}
        assert client.put("/p3/configuration", json=second, headers=operator).status_code == 409
        second["expected_version"] = "handoff_1"
        response = client.put("/p3/configuration", json=second, headers=operator)
        assert response.status_code == 200
        assert response.json()["version"] == "handoff_2"

        restore_request = {**backup_request, "restore_id": "handoff_drill"}
        assert (
            client.post("/p3/restore-drills", json=restore_request, headers=other).status_code
            == 403
        )
        response = client.post("/p3/restore-drills", json=restore_request, headers=operator)
        assert response.status_code == 400, response.text
        assert response.json()["code"] == "CONTRACT_VIOLATION"

        foundation = service.runtime.foundation
        context = foundation.identity.context("alice", timeout_seconds=30)
        lifecycle = foundation.lifecycle
        # A rejected restore must preserve the exact current configuration and DB.
        assert lifecycle.configuration(context).version == "handoff_2"
