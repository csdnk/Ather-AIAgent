import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from test_temporal_http import configuration

from azure_component_service import Service

SCRIPTS = Path(__file__).resolve().parents[3] / "scripts" / "p3"


@pytest.fixture(autouse=True)
def demo_imports(monkeypatch):
    monkeypatch.syspath_prepend(str(SCRIPTS))


@pytest.mark.asyncio
async def test_complete_flow_demo_uses_unified_temporal_service(tmp_path, temporal_server):
    spec = importlib.util.spec_from_file_location("p3_flow_demo", SCRIPTS / "demo_flows.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    from demo_support import DemoRun

    service = Service(configuration(tmp_path, temporal_server.endpoint))
    try:
        run = DemoRun(service, "alice", tmp_path / "demo")
        result = await module.scenario(run)
    finally:
        await service.close()
    assert result["passed"] and result["backend"] == "temporal"
    assert not result["production_acceptance"]
    evidence = json.loads(Path(result["evidence"]).read_text("utf-8"))
    assert evidence["trace"]["durable_facts"]["recall_stages"]
    assert evidence["providers"]["vectors"].endswith(".AzureVectors")
    assert len(set(evidence["job_ids"])) == 5


def test_foundation_demo_explicit_profile_uses_temporal(tmp_path, temporal_server):
    from azure_test_runtime import owned

    completed = subprocess.run(
        [
            sys.executable,
            str(SCRIPTS / "demo_foundation.py"),
            "--directory",
            str(tmp_path / "demo"),
            "--temporal-endpoint",
            temporal_server.endpoint,
            "--postgres-dsn-env",
            "P3_FOUNDATION_DEMO_DSN",
        ],
        env={**os.environ, "P3_FOUNDATION_DEMO_DSN": owned().dsn(tmp_path / "demo/foundation.db")},
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
    from demo_support import DemoRun

    from summary_demo_models import ModelDouble

    config = configuration(tmp_path, temporal_server.endpoint)
    config = config.model_copy(
        update={
            "remember": config.remember.model_copy(
                update={
                    "working_summary_min_bytes": 512,
                    "working_summary_max_chars": 256,
                    "source_page_chars": 256,
                }
            )
        }
    )
    service = Service(config)
    service.runtime.remember.extraction = ModelDouble()
    try:
        run = DemoRun(service, "alice", tmp_path / "summary")
        result = await module.scenario(run)
    finally:
        await service.close()
    assert result["passed"] and result["backend"] == "temporal"
    assert not result["production_acceptance"]
    assert result["providers"]["models"].endswith(".ModelDouble")


@pytest.mark.asyncio
async def test_demo_rejects_existing_run_without_replacing_job_evidence(tmp_path, temporal_server):
    from demo_support import DemoRun

    service = Service(configuration(tmp_path, temporal_server.endpoint))
    try:
        run = DemoRun(service, "alice", tmp_path / "demo")
        await run.start()
        from test_temporal_http import request

        job, result = await run.command("save", "remember.save", request())
        evidence = run.journal.read_bytes()
        assert result["saved"]
        with pytest.raises(FileExistsError):
            DemoRun(service, "alice", run.directory)
        assert run.journal.read_bytes() == evidence
        assert json.loads(evidence)["commands"]["save"]["job_id"] == job.job_id
    finally:
        await service.close()


@pytest.mark.parametrize("script", ["demo_flows.py", "demo_remember_summary.py"])
def test_public_demo_requires_complete_service_configuration(tmp_path, script):
    completed = subprocess.run(
        [sys.executable, str(SCRIPTS / script), "--directory", str(tmp_path / "run")],
        capture_output=True,
        encoding="utf-8",
        timeout=30,
    )
    assert completed.returncode == 2
    assert "--config" in completed.stderr and "--credential-file" in completed.stderr
    assert not list(tmp_path.rglob("*.db"))


@pytest.mark.asyncio
async def test_configured_demo_checks_directory_ownership_before_opening_data(
    tmp_path, temporal_server, monkeypatch
):
    import demo_support
    from demo_remember_summary import run

    from aether_agent_memory.runtime.temporal.locking import DirectoryLock

    settings = configuration(tmp_path, temporal_server.endpoint)
    path = tmp_path / "service.yaml"
    path.write_text(settings.model_dump_json(), "utf-8")
    credential = tmp_path / "credential"
    credential.write_text("alice", "utf-8")
    from types import SimpleNamespace

    monkeypatch.setattr(
        demo_support,
        "ServiceConfiguration",
        SimpleNamespace(load=lambda path: settings.model_copy(update={"profile": "test"})),
    )
    monkeypatch.setattr(demo_support, "Service", Service)
    ownership = DirectoryLock()
    ownership.acquire(settings.data_dir)
    try:
        with pytest.raises(RuntimeError, match="owned"):
            await run(
                tmp_path / "evidence",
                config_path=path,
                credential_file=credential,
            )
        assert not (settings.data_dir / "p3.db").exists()
        assert not (tmp_path / "evidence").exists()
    finally:
        ownership.release()


@pytest.mark.asyncio
async def test_demo_timeout_keeps_admitted_job_for_original_operation(
    tmp_path, temporal_server, monkeypatch
):
    from demo_support import DemoRun
    from test_temporal_http import request

    service = Service(configuration(tmp_path, temporal_server.endpoint))
    try:
        run = DemoRun(service, "alice", tmp_path / "demo")
        await run.start()
        wait = service.execution.await_result

        async def lost_reply(*args):
            raise TimeoutError("injected wait timeout")

        monkeypatch.setattr(service.execution, "await_result", lost_reply)
        with pytest.raises(TimeoutError):
            await run.command("save", "remember.save", request())
        entry = json.loads(run.journal.read_text("utf-8"))["commands"]["save"]
        assert entry["error_type"] == "TimeoutError"
        with pytest.raises(ValueError, match="original job"):
            await run.command("save", "remember.save", request())
        result = await wait(run.context("save"), entry["job_id"], 60)
        assert result["saved"]
        assert len(json.loads(run.journal.read_text("utf-8"))["commands"]) == 1
    finally:
        await service.close()
