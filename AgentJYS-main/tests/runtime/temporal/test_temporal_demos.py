import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[3] / "scripts" / "p3"


@pytest.mark.asyncio
async def test_complete_flow_demo_uses_unified_temporal_service(tmp_path, temporal_server):
    spec = importlib.util.spec_from_file_location("p3_flow_demo", SCRIPTS / "demo_flows.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    result = await module.demo(
        tmp_path / "demo", "lexical", temporal_endpoint=temporal_server.endpoint
    )
    assert result["passed"] and result["backend"] == "temporal"
    assert not result["production_acceptance"]
    evidence = json.loads(Path(result["evidence"]).read_text("utf-8"))
    assert evidence["trace"]["durable_facts"]["recall_stages"]
    assert evidence["retrieval"] == "sqlite_lexical"


def test_foundation_demo_explicit_profile_uses_temporal(tmp_path, temporal_server):
    completed = subprocess.run(
        [
            sys.executable,
            str(SCRIPTS / "demo_foundation.py"),
            "--directory",
            str(tmp_path / "demo"),
            "--temporal-endpoint",
            temporal_server.endpoint,
        ],
        capture_output=True,
        encoding="utf-8",
        timeout=120,
    )
    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout)["passed"]


@pytest.mark.asyncio
async def test_summary_demo_runs_through_temporal_commands(tmp_path, temporal_server):
    spec = importlib.util.spec_from_file_location(
        "p3_summary_demo", SCRIPTS / "demo_remember_summary.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    result = await module.run(tmp_path / "summary", temporal_server.endpoint)
    assert result["passed"] and result["backend"] == "temporal"


@pytest.mark.asyncio
async def test_summary_demo_checks_directory_ownership_before_opening_data(tmp_path):
    from aether_agent_memory.runtime.temporal.locking import DirectoryLock

    spec = importlib.util.spec_from_file_location(
        "summary_locked", SCRIPTS / "demo_remember_summary.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    ownership = DirectoryLock()
    ownership.acquire(tmp_path)
    try:
        with pytest.raises(RuntimeError, match="owned"):
            await module.run(tmp_path)
        assert not (tmp_path / "metadata.db").exists()
    finally:
        ownership.release()
