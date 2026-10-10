import importlib.util
from pathlib import Path

import pytest


def module():
    path = Path(__file__).parents[2] / "scripts/quality/component_benchmark.py"
    assert path.exists(), "component benchmark runner required"
    spec = importlib.util.spec_from_file_location("component_benchmark", path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_throughput_counts_only_success_but_includes_failure_time():
    m = module()
    rows = [
        {"status": "PASS", "seconds": 1, "input_tokens": 10, "input_bytes": 20},
        {"status": "FAIL", "seconds": 3, "input_tokens": 10, "input_bytes": 20},
    ]
    r = m.summarize(rows, planned=3, elapsed=4)
    assert r["valid_items_per_second"] == 0.25
    assert r["failed"] == 1 and r["not_completed"] == 1 and r["status"] == "FAIL"
    assert r["p99_ms"] is None and r["error_rate_started"] == 0.5


def test_empty_samples_and_incomplete_runs_cannot_pass():
    r = module().summarize([], planned=2, elapsed=0)
    assert r["status"] == "BLOCKED" and r["valid_items_per_second"] is None
    with pytest.raises(ValueError):
        module().summarize([], planned=0, elapsed=0)


def test_compression_is_weighted_and_failed_inputs_remain_in_denominator():
    r = module().compression_metrics(
        [
            {
                "status": "PASS",
                "input_bytes": 1000,
                "output_bytes": 100,
                "input_tokens": 100,
                "output_tokens": 10,
            },
            {"status": "FAIL", "input_bytes": 100, "input_tokens": 10},
        ]
    )
    assert r["byte_factor_conservative"] == 5.5
    assert r["token_factor_conservative"] == 5.5
    assert r["failed_inputs_retained"] == 1
    assert r["semantic_quality_status"] == "BLOCKED" and r["physical_storage_status"] == "NOT_RUN"


def test_no_zero_output_infinite_compression_and_no_missing_gold_pass():
    with pytest.raises(ValueError):
        module().compression_metrics(
            [
                {
                    "status": "PASS",
                    "input_bytes": 10,
                    "output_bytes": 0,
                    "input_tokens": 3,
                    "output_tokens": 0,
                }
            ]
        )
    assert module().fact_probe("some text", [])["status"] == "BLOCKED"
    assert module().fact_probe("must allow", ["must not allow"])["status"] == "FAIL"
    r = module().fact_probe("must not allow", ["must not allow"])
    assert r["status"] == "PASS" and r["semantic_acceptance"] is False


def test_comparison_refuses_changed_workload_or_hardware():
    m = module()
    a = {"comparison_key": "a", "metrics": {"valid_items_per_second": 10}}
    assert m.compare(a, {"comparison_key": "b"})["status"] == "BLOCKED"
    assert (
        m.compare(a, {"comparison_key": "a", "metrics": {"valid_items_per_second": 20}})[
            "throughput_change_percent"
        ]
        == -50
    )


def test_dataset_requires_source_and_nonempty_inputs():
    with pytest.raises(ValueError):
        module().validate_dataset([])
    with pytest.raises(ValueError):
        module().validate_dataset([{"dataset_id": "a", "text": "x"}])
    with pytest.raises(ValueError):
        module().validate_dataset([{"dataset_id": "a", "text": "", "source_sha256": "a" * 64}])


def test_missing_local_model_writes_blocked_report_without_network(tmp_path):
    import json
    import subprocess
    import sys

    m = module()
    data = tmp_path / "data.json"
    data.write_text(
        json.dumps([{"dataset_id": "a", "text": "真实本地输入", "source_sha256": "a" * 64}])
    )
    p = subprocess.run(
        [
            sys.executable,
            m.__file__,
            "--kind",
            "embedding",
            "--dataset",
            str(data),
            "--model-path",
            str(tmp_path / "absent"),
            "--output",
            str(tmp_path / "reports"),
            "--hardware-id",
            "test",
            "--candidate",
            "synthetic-unit-test",
        ],
        capture_output=True,
        timeout=15,
    )
    assert p.returncode == 2
    report = json.loads(next((tmp_path / "reports").glob("*/summary.json")).read_text())
    assert report["status"] == "BLOCKED" and report["metrics"]["completed_valid"] == 0
    assert report["release_gate"] == "BLOCKED" and report["diagnostics"][0]["type"] == "blocked"


def test_interrupted_started_operation_counts_as_failure_and_elapsed_time():
    m = module()
    tasks = [(0, "Query", {"dataset_id": "a", "text": "输入"})]
    rows = m.finish_samples(
        [{"type": "operation_start", "index": 0, "monotonic": 10}], tasks, finish=15
    )
    assert rows[0]["status"] == "FAIL" and rows[0]["seconds"] == 5
    assert rows[0]["error_type"] == "INTERRUPTED"
    result = m.summarize(rows, 1, 5)
    assert (
        result["failed"] == 1 and result["error_rate_started"] == 1 and result["not_completed"] == 0
    )


def test_component_threshold_gate_does_not_pass_missing_samples_or_failures():
    m = module()
    assert (
        m.threshold_gate(
            {"status": "PASS", "valid_items_per_second": 10, "p95_ms": None},
            {"min_items_per_second": 5, "max_p95_ms": 10},
        )["status"]
        == "BLOCKED"
    )
    assert (
        m.threshold_gate(
            {"status": "FAIL", "valid_items_per_second": 10, "p95_ms": 1},
            {"min_items_per_second": 5},
        )["status"]
        == "FAIL"
    )
    assert (
        m.threshold_gate(
            {"status": "PASS", "valid_items_per_second": 10, "p95_ms": 1},
            {"min_items_per_second": 20},
        )["status"]
        == "FAIL"
    )
    assert (
        m.threshold_gate(
            {"status": "BLOCKED", "byte_factor_conservative": 1}, {"min_byte_factor": 5}
        )["status"]
        == "BLOCKED"
    )
