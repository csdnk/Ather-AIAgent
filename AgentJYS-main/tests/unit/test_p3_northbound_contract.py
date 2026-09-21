from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from aether_agent_memory.app import create_app
from aether_agent_memory.config.app_settings import AppSettings

ROOT = Path(__file__).parents[2]


@pytest.mark.unit
def test_p3_northbound_v1_contract_is_frozen_and_implemented() -> None:
    contract = json.loads((ROOT / "contracts" / "p3-northbound-v1.json").read_text("utf-8"))

    expected = {
        ("GET", "/health"),
        ("POST", "/api/v1/memory/events"),
        ("POST", "/api/v1/context"),
        ("POST", "/api/v1/b2/long-text"),
        ("GET", "/api/v1/b2/tasks/{task_id}"),
        ("POST", "/api/v1/b2/search"),
    }
    actual = {(item["method"], item["path"]) for item in contract["operations"]}

    assert contract["status"] == "frozen"
    assert contract["version"] == "1.0.0"
    assert actual == expected

    app = create_app(AppSettings(profile="demo"))
    with TestClient(app) as client:
        schema = client.get("/openapi.json").json()
    implemented = set(schema["paths"].keys())
    for _, path in expected:
        assert path in implemented


@pytest.mark.unit
def test_p4_package_does_not_import_p3_implementation() -> None:
    package = ROOT / "src" / "aether_p4_simulator"
    combined = "\n".join(path.read_text("utf-8") for path in package.glob("*.py"))

    assert "aether_agent_memory" not in combined
