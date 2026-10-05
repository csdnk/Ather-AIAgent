"""Cloud runner guards: isolation, credentials and exact resource ownership."""

import importlib.util
import json
from pathlib import Path

import pytest


def runner():
    path = Path(__file__).resolve().parents[2] / "scripts/p3/run_aks_tests.py"
    assert path.is_file(), "reproducible AKS runner is missing"
    spec = importlib.util.spec_from_file_location("run_aks_tests", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_processing_directory_cannot_be_inside_repository():
    module = runner()
    root = Path(__file__).resolve().parents[3]
    with pytest.raises(ValueError, match="outside"):
        module.external_directory(root / "unsafe", root)


def test_cloud_cleanup_requires_exact_created_uid():
    module = runner()
    assert module.owns_pod({"metadata": {"uid": "mine"}}, "mine")
    assert not module.owns_pod({"metadata": {"uid": "another"}}, "mine")
    assert not module.owns_pod({}, "mine")


def test_cloud_delete_is_conditioned_on_uid_at_server():
    calls = []

    def call(arguments, *, data=None, timeout=60):
        calls.append((arguments, json.loads(data)))
        return type("Result", (), {"returncode": 0})()

    assert runner().delete_owned_pod(call, "test-namespace", "p3-owned", "created-uid")
    arguments, options = calls[0]
    assert arguments == [
        "delete",
        "--raw=/api/v1/namespaces/test-namespace/pods/p3-owned",
        "-f",
        "-",
    ]
    assert options["preconditions"]["uid"] == "created-uid"


def test_cloud_delete_failure_cannot_report_clean_success():
    def call(arguments, *, data=None, timeout=60):
        return type("Result", (), {"returncode": 1})()

    assert not runner().delete_owned_pod(call, "test-namespace", "p3-owned", "created-uid")


def test_redaction_covers_secret_values_and_dsn():
    module = runner()
    values = {"P3_TEST_REDIS_PASSWORD": "private-key", "P3_TEST_STATE_DSN": "password=pg-secret"}
    safe = module.redact(json.dumps(values), values)
    assert "private-key" not in safe and "pg-secret" not in safe


def test_required_azure_backends_fail_before_pod_creation():
    with pytest.raises(ValueError, match="P3_TEST_STATE_DSN"):
        runner().validate_environment({})


@pytest.mark.parametrize("fault", ["empty", "skip", "error", "failure", "collection", "changed"])
def test_gate_rejects_nonexecuted_or_incomplete_evidence(tmp_path, fault):
    module = runner()
    junit = tmp_path / "junit.xml"
    entry = '<testcase name="executed"/>'
    if fault in {"skip", "error", "failure"}:
        tag = "skipped" if fault == "skip" else fault
        entry = f'<testcase name="executed"><{tag}/></testcase>'
    junit.write_text("<testsuite>" + ("" if fault == "empty" else entry) + "</testsuite>")
    execution = {
        "exit": 0,
        "source_unchanged": fault != "changed",
        "executed": fault != "collection",
    }
    assert not module.verify_evidence(junit, execution, 0, allow_skips=False)["passed"]


def test_gate_accepts_executed_unchanged_source(tmp_path):
    junit = tmp_path / "junit.xml"
    junit.write_text('<testsuite><testcase name="executed"/></testsuite>')
    execution = {"exit": 0, "source_unchanged": True, "executed": True}
    assert runner().verify_evidence(junit, execution, 0, allow_skips=False)["passed"]


def test_source_archive_excludes_private_environment_files(tmp_path):
    import io
    import tarfile

    root, assets = tmp_path / "repository", tmp_path / "assets"
    (root / "web").mkdir(parents=True)
    assets.mkdir()
    (root / "web/.env").write_text("PRIVATE=test-secret")
    (root / "web/.env.local").write_text("PRIVATE=test-secret")
    (root / "web/.env.example").write_text("PUBLIC=value")
    (root / "web/a.ts").write_text("export const a = 1")
    archive, _ = runner().source_archive(root, assets)
    with tarfile.open(fileobj=io.BytesIO(archive)) as package:
        names = package.getnames()
    assert not any(name.endswith(("/.env", "/.env.local")) for name in names)
    assert any(name.endswith("/.env.example") for name in names)


@pytest.mark.parametrize("fault", ["query", "delete"])
def test_runner_summary_includes_failed_pod_cleanup(tmp_path, monkeypatch, fault):
    """Green testcases must not hide an unknown or refused resource cleanup."""
    import io
    import subprocess
    import tarfile

    module = runner()
    environment = tmp_path / "private.json"
    values = dict.fromkeys(module.REQUIRED, "fixture-value")
    values["P3_TEST_STATE_DSN"] = "host=fixture dbname=p3_test_fixture sslmode=verify-full"
    environment.write_text(json.dumps(values))
    assets = tmp_path / "assets"
    assets.mkdir()
    (assets / "model").mkdir()
    for name in ("native.json", "milvus-ca.crt", "temporal"):
        (assets / name).write_text("fixture")
    evidence = io.BytesIO()
    with tarfile.open(fileobj=evidence, mode="w:gz") as package:
        files = {
            "evidence/junit.xml": '<testsuite><testcase name="executed"/></testsuite>',
            "evidence/execution.json": json.dumps(
                {"exit": 0, "source_unchanged": True, "executed": True}
            ),
        }
        for name, value in files.items():
            item = tarfile.TarInfo(name)
            data = value.encode()
            item.size = len(data)
            package.addfile(item, io.BytesIO(data))

    def kubectl(command, **kwargs):
        arguments = command[3:]
        if arguments[0] == "create":
            return subprocess.CompletedProcess(command, 0, b'{"metadata":{"uid":"owned"}}', b"")
        if arguments[0] == "get":
            if fault == "query":
                return subprocess.CompletedProcess(command, 1, b"", b"API query denied")
            return subprocess.CompletedProcess(command, 0, b'{"metadata":{"uid":"owned"}}', b"")
        if arguments[0] == "delete":
            return subprocess.CompletedProcess(command, 1, b"", b"UID deletion denied")
        if "-czf" in arguments:
            return subprocess.CompletedProcess(command, 0, evidence.getvalue(), b"")
        return subprocess.CompletedProcess(command, 0, b"", b"")

    monkeypatch.setattr(module.subprocess, "run", kubectl)
    monkeypatch.setattr(module, "source_archive", lambda *args: (b"snapshot", {}))
    output = tmp_path / "results"
    code = module.main(
        [
            "--environment-file",
            str(environment),
            "--assets-directory",
            str(assets),
            "--directory",
            str(output),
            "--",
            "tests",
        ]
    )
    summary = json.loads(next(output.glob("*/summary.json")).read_text())
    assert code == 1, "cleanup failure returned a successful process status"
    assert summary["exit"] == 1 and summary["passed"] is False
    assert summary["cleanup"]["confirmed"] is False
