import ast
import json
from pathlib import Path

import pytest
from test_temporal_http import configuration

from aether_agent_memory.runtime.flows.application import Service
from aether_agent_memory.runtime.temporal.migration import KINDS


def test_registered_kind_without_workflow_blocks_start(tmp_path, monkeypatch):
    import aether_agent_memory.runtime.temporal.service as composition

    original = composition.register_remember

    def missing(registry, remember):
        original(registry, remember)
        remember.tasks.register("remember.future", "remember", remember)

    monkeypatch.setattr(composition, "register_remember", missing)
    service = None
    try:
        with pytest.raises(ValueError, match="without Temporal"):
            service = Service(configuration(tmp_path, "127.0.0.1:1"))
    finally:
        if service:
            import asyncio

            asyncio.run(service.close())


def test_catalog_migration_and_frontend_coverage_match(tmp_path):
    import asyncio

    service = Service(configuration(tmp_path, "127.0.0.1:1"))
    try:
        registry = service.execution.registry
        assert hasattr(registry, "coverage_manifest"), "no exported execution coverage"
        manifest = registry.coverage_manifest()
        assert set(manifest) == set(service.runtime.foundation.tasks.handlers) == set(KINDS)
        assert {"remember.save", "remember.correct", "remember.document", "recall.execute"} <= set(
            manifest
        )
        assert manifest["runtime.event_delivery"].startswith("EventDeliveryWorkflow:")
        assert all(value.split(":", 1)[1] for value in manifest.values())
        root = Path(__file__).resolve().parents[3]
        rows = json.loads((Path(__file__).parent / "retired_rf_coverage.json").read_text("utf-8"))
        assert all((root / row["replacement"].split("::")[0]).is_file() for row in rows)
    finally:
        asyncio.run(service.close())


def test_acceptance_manifest_names_existing_tests_for_all_scenarios():
    base = Path(__file__).parent
    scenarios = json.loads((base / "acceptance_scenarios.json").read_text("utf-8"))
    assert set(scenarios) == {f"T{i:02}" for i in range(1, 19)}
    for references in scenarios.values():
        assert references
        for reference in references:
            file, case = reference.split("::")
            nodes = ast.walk(ast.parse((base / file).read_text("utf-8")))
            assert case.split("[")[0] in {
                n.name for n in nodes if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
            }, reference
