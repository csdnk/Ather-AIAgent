import importlib.util
from pathlib import Path


def test_missing_dependencies_selects_portable_cases_and_reports_blocked():
    path = Path(__file__).parents[2] / "scripts/quality/product_ci.py"
    s = importlib.util.spec_from_file_location("product_ci", path)
    m = importlib.util.module_from_spec(s)
    s.loader.exec_module(m)
    selection = m.select("integration", {})
    assert selection["paths"] == ["tests/integration/test_p2_tls_transport.py"]
    assert selection["full_layer_status"] == "BLOCKED"
    assert "P3_TEST_STATE_DSN" in selection["missing"]
    assert m.select("unit", {})["paths"]


def test_scratch_paths_are_short_and_outside_nested_report_directory(tmp_path):
    import sys

    sys.path.insert(0, str(Path(__file__).parents[2] / "scripts/quality"))
    import product_ci as m

    report = tmp_path / "a-very-long-report-name" / "deep"
    scratch = m.scratch_directory(report, {"QUALITY_TEST_TEMP_ROOT": str(tmp_path / "scratch")})
    assert scratch.parent == tmp_path / "scratch"
    assert scratch.is_dir() and scratch.name.startswith("qt-")
    assert not scratch.is_relative_to(report)
