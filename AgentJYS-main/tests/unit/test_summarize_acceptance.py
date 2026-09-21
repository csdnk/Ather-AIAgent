"""Acceptance-summary gate tests (B1 throughput + B2 compression/P99)."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

ROOT = Path(__file__).parents[2]
SPEC = importlib.util.spec_from_file_location(
    "summarize_b1_b2_acceptance", ROOT / "scripts" / "summarize_b1_b2_acceptance.py"
)
assert SPEC is not None and SPEC.loader is not None
summarize = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(summarize)


def _b1_result(
    *, primary_qps: float | None, contract_status: str, error_rate: float
) -> dict[str, Any]:
    return {
        "status": "COMPLETED",
        "best_primary": {"error_rate": error_rate},
        "contract": {
            "contract_target": 2000,
            "primary_value": primary_qps,
            "status": contract_status,
        },
    }


def test_phase_one_passes_with_target_throughput() -> None:
    phase = summarize.phase_one(
        _b1_result(primary_qps=2500.0, contract_status="PASS", error_rate=0.0)
    )
    assert phase["gate_status"] == "PASSED"
    assert phase["throughput"]["effective_item_qps"] == 2500.0


def test_phase_one_requires_target_throughput() -> None:
    phase = summarize.phase_one(
        _b1_result(primary_qps=1500.0, contract_status="PASS", error_rate=0.0)
    )
    assert phase["gate_status"] == "NOT_VERIFIED"


def test_phase_one_requires_contract_pass() -> None:
    phase = summarize.phase_one(
        _b1_result(primary_qps=2500.0, contract_status="FAIL", error_rate=0.0)
    )
    assert phase["gate_status"] == "NOT_VERIFIED"


def _smoke_log(tmp_path: Path) -> Path:
    log = tmp_path / "smoke.log"
    log.write_text("COMPOSE_SMOKE_PASSED\n", encoding="utf-8")
    return log


def _replay(*, samples: int = 3, p99: float | None = None) -> dict[str, Any]:
    replay = {"samples": samples}
    if p99 is not None:
        replay["working_write_ms_p99_ms"] = p99
    return replay


def test_phase_two_accepts_pass_and_passed_gates(tmp_path: Path) -> None:
    compression = {"compression_gate_status": "PASS", "acceptance_status": "PASSED"}
    phase = summarize.phase_two(compression, _replay(), _smoke_log(tmp_path))
    assert phase["gate_status"] == "PASSED"


def test_phase_two_fails_working_p99_over_limit(tmp_path: Path) -> None:
    compression = {"compression_gate_status": "PASSED", "acceptance_status": "PASSED"}
    phase = summarize.phase_two(compression, _replay(p99=25.0), _smoke_log(tmp_path))
    assert phase["gate_status"] == "NOT_VERIFIED"
    assert phase["working_write_p99_ok"] is False


def test_phase_two_reports_missing_data_as_not_verified(tmp_path: Path) -> None:
    compression = {"compression_gate_status": "PASSED", "acceptance_status": "PASSED"}
    phase = summarize.phase_two(compression, _replay(samples=0), _smoke_log(tmp_path))
    assert phase["gate_status"] == "NOT_VERIFIED"
