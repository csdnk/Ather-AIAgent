"""The dedicated real-backend gate must never count skips or missing cases as PASS."""

import importlib.util
import json
import subprocess
from pathlib import Path

import pytest


def runner():
    path = Path(__file__).resolve().parents[2] / "scripts/p3/validate_working_milvus.py"
    assert path.is_file(), "real Working validation runner is missing"
    spec = importlib.util.spec_from_file_location("validate_working_milvus", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("fault", ["skip", "failure", "error", "missing", "exit", "duplicate"])
def test_gate_rejects_incomplete_evidence(tmp_path, fault):
    module = runner()
    names = list(module.REQUIRED_TESTS)
    if fault == "missing":
        names.pop()
    if fault == "duplicate":
        names.append(names[0])
    cases = [f'<testcase name="{name}"/>' for name in names]
    if fault in {"skip", "failure", "error"}:
        tag = "skipped" if fault == "skip" else fault
        cases[0] = f'<testcase name="{names[0]}"><{tag}/></testcase>'
    report = tmp_path / "junit.xml"
    report.write_text("<testsuite>" + "".join(cases) + "</testsuite>")
    assert not module.verify_junit(report, 1 if fault == "exit" else 0)["passed"]


def test_gate_accepts_all_required_cases(tmp_path):
    module = runner()
    report = tmp_path / "junit.xml"
    report.write_text(
        "<testsuites><testsuite>"
        + "".join(f'<testcase name="{name}"/>' for name in module.REQUIRED_TESTS)
        + "</testsuite></testsuites>"
    )
    result = module.verify_junit(report, 0)
    assert result["passed"]
    assert result["tests"] == len(module.REQUIRED_TESTS)
    assert result["skipped"] == 0


def test_gate_rejects_absent_junit(tmp_path):
    assert not runner().verify_junit(tmp_path / "absent.xml", 0)["passed"]


def test_runner_rejects_process_files_inside_parent_repository(tmp_path, monkeypatch):
    module = runner()
    repository = Path(__file__).resolve().parents[3]
    config = tmp_path / "config.json"
    config.write_text("{}")

    def must_not_create_run(**kwargs):
        raise AssertionError("runner reached an unsafe processing directory")

    monkeypatch.setattr(module.tempfile, "mkdtemp", must_not_create_run)
    with pytest.raises(SystemExit) as error:
        module.main(
            [
                "--directory",
                str(repository),
                "--embedding-config",
                str(config),
                "--temporal-cli",
                str(config),
            ]
        )
    assert error.value.code == 2


@pytest.mark.parametrize("cache", ["omitted", "external", "relative_external"])
def test_runner_writes_external_effective_native_config(tmp_path, monkeypatch, cache):
    module = runner()
    root = Path(__file__).resolve().parents[2]
    config = tmp_path / "original.json"
    settings = {
        "model_path": "models/selected-model",
        "precision": "fp32",
        "threads": 2,
        "query_prefix": "custom query prefix",
    }
    if cache != "omitted":
        settings["cache_dir"] = (
            str(tmp_path / "approved-cache")
            if cache == "external"
            else "../../codex-aether/.agent-work/workspace-support/cache/test-relative"
        )
    config.write_text(json.dumps(settings, indent=3), encoding="utf-8")
    original = config.read_bytes()
    monkeypatch.delenv("TIKTOKEN_CACHE_DIR", raising=False)
    observed = {}

    def consume_config(command, **kwargs):
        effective = Path(kwargs["env"]["P3_TEST_NATIVE_CONFIG"])
        assert effective != config
        assert effective.is_relative_to(tmp_path / "runs")
        observed.update(json.loads(effective.read_text("utf-8")))
        # The process boundary is replaced only to inspect effective configuration.
        # It emits controlled complete JUnit, not real-backend acceptance evidence.
        junit = Path(command[command.index("--junitxml") + 1])
        junit.write_text(
            "<testsuite>"
            + "".join(f'<testcase name="{name}"/>' for name in module.REQUIRED_TESTS)
            + "</testsuite>"
        )
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(module.subprocess, "run", consume_config)
    assert (
        module.main(
            [
                "--directory",
                str(tmp_path / "runs"),
                "--embedding-config",
                str(config),
                "--temporal-cli",
                str(config),
            ]
        )
        == 0
    )
    assert config.read_bytes() == original
    assert Path(observed["model_path"]) == (root / "models/selected-model").resolve()
    assert observed["precision"] == "fp32"
    assert observed["threads"] == 2
    assert observed["query_prefix"] == "custom query prefix"
    assert observed["model_name"] == "BAAI/bge-small-zh-v1.5"
    if cache == "omitted":
        assert Path(observed["cache_dir"]).is_relative_to(tmp_path / "runs")
    else:
        assert Path(observed["cache_dir"]) == (root / settings["cache_dir"]).resolve()


@pytest.mark.parametrize("cache", ["explicit_default", "business", "parent_repository"])
def test_runner_rejects_native_cache_in_source_before_subprocess(tmp_path, monkeypatch, cache):
    module = runner()
    root = Path(__file__).resolve().parents[2]
    directory = {
        "explicit_default": ".aether/recall/embedding/models",
        "business": str(root / "blocked-model-cache"),
        "parent_repository": str(root.parent / "blocked-model-cache"),
    }[cache]
    config = tmp_path / "original.json"
    config.write_text(json.dumps({"model_path": "models/selected-model", "cache_dir": directory}))
    original = config.read_bytes()
    monkeypatch.delenv("TIKTOKEN_CACHE_DIR", raising=False)

    def must_not_start(*args, **kwargs):
        pytest.fail("unsafe model cache reached subprocess startup")

    monkeypatch.setattr(module.subprocess, "run", must_not_start)
    with pytest.raises(SystemExit) as error:
        module.main(
            [
                "--directory",
                str(tmp_path / "runs"),
                "--embedding-config",
                str(config),
                "--temporal-cli",
                str(config),
            ]
        )
    assert error.value.code == 2
    assert config.read_bytes() == original
