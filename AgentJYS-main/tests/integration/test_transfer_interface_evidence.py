"""The default acceptance observer must credit actual transfer HTTP contracts."""

import json
import os
import subprocess
import sys
from pathlib import Path
from textwrap import dedent

import pytest

pytestmark = pytest.mark.integration
ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize(
    ("kind", "case"),
    [
        (
            "transfers",
            "tests/integration/test_client_transfers.py::test_transfer_receipt_preserves_original_snapshot_head_and_survives_restart",
        ),
        (
            "recoveries",
            "tests/integration/test_client_recovery_safety.py::test_original_activation_receipt_survives_restart_and_cannot_rewind_later_owner",
        ),
    ],
)
def test_default_evidence_selection_credits_transfer_responses(tmp_path, kind, case):
    probe = tmp_path / "transfer_evidence.py"
    report_path = tmp_path / "report.json"
    probe.write_text(
        dedent(
            f"""
            import json
            import runpy
            import sys
            from pathlib import Path
            import pytest

            sys.path.insert(0, {str(ROOT)!r})
            gate = runpy.run_path({str(ROOT / "scripts/p3/validate_demo_interfaces.py")!r})
            evidence = gate['Evidence'](routes=(
                ('POST', '/p3/client-runs/{{run_id}}/{kind}/{{transfer_id}}'),
                ('GET', '/p3/client-runs/{{run_id}}/{kind}/{{transfer_id}}'),
            ))
            result = pytest.main([
                {case!r},
                '-q', '-p', 'no:cacheprovider',
                '--basetemp', {str(tmp_path / "isolated-pytest")!r},
            ], plugins=[evidence])
            Path({str(report_path)!r}).write_text(
                json.dumps(evidence.summary(int(result))), encoding='utf-8'
            )
            raise SystemExit(int(result))
            """
        ),
        encoding="utf-8",
    )
    result = subprocess.run(
        [sys.executable, "-B", "-X", "utf8", str(probe)],
        cwd=ROOT,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "PYTHONUTF8": "1"},
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=180,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads(report_path.read_text("utf-8"))
    assert report["pytest_exit_code"] == 0
    assert report["verified_routes"] == 2 and report["missing"] == [], report
    assert report["incomplete_cases"] == []
    assert all(row["evidence"] for row in report["routes"])


@pytest.mark.parametrize("confirmation", ["reads", "definition", "input"])
def test_default_evidence_selection_credits_pending_recovery_reads(tmp_path, confirmation):
    probe = tmp_path / "recovery_read_evidence.py"
    report_path = tmp_path / "report.json"
    cases = {
        "definition": (
            "tests/integration/test_client_initialization.py"
            "::test_confirmation_preserves_hold_binding_writer_and_run"
        ),
        "input": (
            "tests/integration/test_client_recovery_inputs.py"
            "::test_confirm_original_input_preserves_writer_bytes_journal_and_hold"
        ),
        "reads": (
            "tests/integration/test_client_recovery_reads.py"
            "::test_recovery_read_preserves_original_pending_or_ready_object"
        ),
    }
    case = cases[confirmation]
    routes = (
        (
            (
                "PUT",
                "/p3/client-runs/{run_id}/recoveries/{transfer_id}/"
                + ("inputs/{operation_id}" if confirmation == "input" else "definition"),
            ),
        )
        if confirmation != "reads"
        else tuple(
            ("GET", "/p3/client-runs/{run_id}/recoveries/{transfer_id}/" + suffix)
            for suffix in ("definition", "inputs/{operation_id}", "states/{sequence}")
        )
    )
    probe.write_text(
        dedent(f"""
        import json, runpy, sys
        from pathlib import Path
        import pytest
        sys.path.insert(0, {str(ROOT)!r})
        gate = runpy.run_path({str(ROOT / "scripts/p3/validate_demo_interfaces.py")!r})
        evidence = gate['Evidence'](routes={routes!r})
        result = pytest.main([
            {case!r}, '-q', '-p', 'no:cacheprovider',
            '--basetemp', {str(tmp_path / "isolated-pytest")!r},
        ], plugins=[evidence])
        Path({str(report_path)!r}).write_text(
            json.dumps(evidence.summary(int(result))), encoding='utf-8'
        )
        raise SystemExit(int(result))
    """),
        encoding="utf-8",
    )
    result = subprocess.run(
        [sys.executable, "-B", "-X", "utf8", str(probe)],
        cwd=ROOT,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=180,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads(report_path.read_text("utf-8"))
    assert report["verified_routes"] == len(routes) and report["missing"] == [], report
    assert report["pytest_exit_code"] == 0 and report["incomplete_cases"] == []
