"""Operational HTTP acceptance on disposable state, never the live demo instance."""

import asyncio

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from aether_agent_memory.runtime.contracts.foundation import ConfigurationSnapshot
from aether_agent_memory.runtime.flows.application import Service
from aether_agent_memory.runtime.foundation.common import fingerprint, now
from aether_p4_simulator.validation.calls import ROUTES
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
        provider_ids=("sqlite",),
        policy_versions=("test",),
        secret_refs=(),
    )
    return ConfigurationSnapshot(
        **values, config_hash=fingerprint(values), activated_at=now()
    ).model_dump(mode="json")


def test_operational_http_restore_is_isolated_and_version_checked(tmp_path, temporal_server):
    """Catch stale configuration writes, missing operator checks and live-DB replacement."""
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
        assert response.status_code == 200
        backup = response.json()
        assert backup["config_version"] == "handoff_1"
        assert backup["restore_state"] == "untested"
        assert backup["consistency_watermark"] > 0
        assert client.post("/p3/backups", json=backup_request, headers=operator).json() == backup

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
        assert response.status_code == 200
        restored = response.json()
        assert restored["restore_state"] == "passed"
        assert restored["config_version"] == "handoff_1"
        assert len(restored["restore_evidence"]) == 1
        assert restored["restore_evidence"][0]["object_id"] == "handoff_drill"

        foundation = service.runtime.foundation
        context = foundation.identity.context("alice", timeout_seconds=30)
        lifecycle = foundation.lifecycle
        # The pre-restore live version must survive; restored state is a separate file.
        assert lifecycle.configuration(context).version == "handoff_2"
        source = lifecycle.path("handoff_snapshot")
        target = lifecycle.path("handoff_drill", restore=True)
        assert source.is_relative_to(tmp_path)
        assert target.is_relative_to(tmp_path)
        assert target != foundation.uow.path.resolve()
        assert lifecycle.inspect(source) == lifecycle.inspect(target)
