"""The dedicated real-backend gate must never count skips or missing cases as PASS."""

import importlib.util
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
