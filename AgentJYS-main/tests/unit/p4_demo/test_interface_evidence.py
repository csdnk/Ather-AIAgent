"""The acceptance gate must reject mocks, failed cases and missing responses."""

import json
import os
import subprocess
import sys
from pathlib import Path
from textwrap import dedent

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts/p3/validate_demo_interfaces.py"


def test_full_suite_launcher_collects_local_packages_without_claiming_execution(tmp_path):
    directory = tmp_path / "collect-only"
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--full-suite", "--directory", str(directory)],
        cwd=tmp_path,
        env={**os.environ, "PYTEST_ADDOPTS": "--collect-only", "PYTHONUTF8": "1"},
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=90,
    )
    report = json.loads((directory / "report.json").read_text("utf-8"))
    assert report["pytest_exit_code"] == 0, result.stdout + result.stderr
    assert report["not_collected_tests"] == []
    assert report["status"] == "incomplete"
    assert report["verified_routes"] == 0
    assert result.returncode == 1  # A clean collection is not an executed acceptance test.


def test_gate_only_credits_real_responses_from_fully_passed_tests(tmp_path):
    assert SCRIPT.is_file(), "The reproducible HTTP evidence gate is not implemented"
    (tmp_path / "test_probe.py").write_text(
        dedent(
            """
            import asyncio
            import httpx
            import pytest
            from fastapi.testclient import TestClient
            from aether_agent_memory.runtime.flows.application import Service
            from integration.test_p4_demo_temporal import configuration

            @pytest.fixture
            def client(tmp_path):
                service = Service(configuration(tmp_path, "127.0.0.1:1"))
                client = TestClient(service.app())  # No lifespan/Temporal needed for liveness.
                try:
                    yield client
                finally:
                    client.close()
                    asyncio.run(service.close())

            def test_real(client):
                assert client.get("/p3/live").json()["liveness"] == "alive"

            def test_unauthorized_is_not_normal_success(client):
                assert client.get("/p3/health").status_code == 401

            def test_mock():
                with httpx.Client(
                    transport=httpx.MockTransport(lambda r: httpx.Response(200)),
                    base_url="http://unused",
                ) as fake:
                    assert fake.get("/p3/health").status_code == 200

            def test_inventory_without_a_request():
                from aether_p4_simulator.validation.calls import ROUTES
                assert ("GET", "/p3/health") in ROUTES

            def test_failed(client):
                assert client.get("/p3/live").status_code == 200
                pytest.fail("business assertion failed")

            def test_skipped(client):
                assert client.get("/p3/live").status_code == 200
                pytest.skip("no acceptance")

            @pytest.mark.xfail(reason="not verified")
            def test_xfailed(client):
                assert client.get("/p3/live").status_code == 200
                assert False

            @pytest.fixture
            def broken_teardown(client):
                yield client
                raise RuntimeError("teardown failed")

            def test_teardown_failed(broken_teardown):
                assert broken_teardown.get("/p3/live").status_code == 200

            @pytest.fixture
            def broken_setup(client):
                assert client.get("/p3/live").status_code == 200
                raise RuntimeError("setup failed")

            def test_setup_failed(broken_setup):
                pass
            """
        ),
        encoding="utf-8",
    )
    probe = dedent(
        """
        import json
        from pathlib import Path
        import pytest
        from validate_demo_interfaces import Evidence

        names = (
            "real", "unauthorized_is_not_normal_success", "mock",
            "inventory_without_a_request", "failed", "skipped", "xfailed",
            "teardown_failed", "setup_failed",
        )
        evidence = Evidence(
            tests=tuple("test_probe.py::test_" + name for name in names),
            routes=(("GET", "/p3/live"), ("GET", "/p3/health")),
        )
        result = pytest.main(["-q", "test_probe.py", "-p", "no:cacheprovider"], plugins=[evidence])
        Path("evidence.json").write_text(
            json.dumps(evidence.summary(int(result))), encoding="utf-8"
        )
        """
    )
    result = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=tmp_path,
        env={
            **os.environ,
            "PYTHONPATH": os.pathsep.join(
                (str(ROOT / "scripts/p3"), str(ROOT / "tests"), str(ROOT / "src"))
            ),
            "PYTHONUTF8": "1",
        },
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=90,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads((tmp_path / "evidence.json").read_text("utf-8"))
    assert report["pytest_exit_code"] == 1
    assert report["status"] == "incomplete"
    assert report["verified_routes"] == 1
    assert report["missing"] == [{"method": "GET", "path": "/p3/health"}]
    live, health = report["routes"]
    assert live["evidence"] == [
        {"test": "test_probe.py::test_real", "statuses": [200], "responses": 1}
    ]
    assert health["evidence"] == [
        {
            "test": "test_probe.py::test_unauthorized_is_not_normal_success",
            "statuses": [401],
            "responses": 1,
        }
    ]
    assert set(report["incomplete_cases"]) == {
        "test_probe.py::test_failed",
        "test_probe.py::test_skipped",
        "test_probe.py::test_xfailed",
        "test_probe.py::test_teardown_failed",
        "test_probe.py::test_setup_failed",
    }
