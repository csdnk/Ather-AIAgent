"""Run schema tooling with synthetic passwords and verify safe diagnostics."""

import os
import shutil
import subprocess
from pathlib import Path

import pytest


@pytest.mark.parametrize("password", ["abc+def", "unmatched[", r"a.*$b\c&/"])
@pytest.mark.parametrize("tool_exit", [0, 7])
def test_schema_initializer_redacts_literal_password_and_keeps_exit(tmp_path, password, tool_exit):
    shell = shutil.which("bash")
    assert shell, "schema initializer must be verified in its Linux execution environment"
    tool = tmp_path / "temporal-sql-tool"
    tool.write_text(
        '#!/bin/sh\nprintf "before %s after %s\\n" "$SQL_PASSWORD" "$SQL_PASSWORD"\n'
        'printf "stderr %s\\n" "$SQL_PASSWORD" >&2\nexit "$FIXTURE_EXIT"\n'
    )
    tool.chmod(0o700)
    script = Path(__file__).resolve().parents[2] / "deploy/azure/initialize-temporal.sh"
    environment = dict(
        os.environ,
        PATH=str(tmp_path) + os.pathsep + os.environ["PATH"],
        SQL_PASSWORD=password,
        SQL_DATABASE="agent",
        P3_SCHEMA_MODE="upgrade",
        FIXTURE_EXIT=str(tool_exit),
    )
    result = subprocess.run(
        [shell, str(script)], env=environment, capture_output=True, text=True, timeout=10
    )
    diagnostics = result.stdout + result.stderr
    assert password not in diagnostics, "literal credential escaped the log sanitizer"
    assert "[REDACTED]" in diagnostics
    assert result.returncode == tool_exit, "sanitizing logs hid the SQL tool failure"
