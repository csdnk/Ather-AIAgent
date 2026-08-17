"""Optional Docker validation for the unified B1 -> P2 -> B2 -> B3 smoke flow."""

from __future__ import annotations

from pathlib import Path
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.integration
def test_compose_demo_runs_the_unified_smoke_flow() -> None:
    if shutil.which("docker") is None:
        pytest.skip("Docker is not installed")

    command = [
        "docker",
        "compose",
        "--profile",
        "demo",
        "up",
        "--build",
        "--abort-on-container-exit",
        "--exit-code-from",
        "p3-demo",
    ]
    try:
        result = subprocess.run(
            command,
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=600,
            check=False,
        )
    finally:
        subprocess.run(
            ["docker", "compose", "--profile", "demo", "down", "--volumes", "--remove-orphans"],
            cwd=ROOT,
            text=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )

    assert result.returncode == 0, result.stdout
    assert "P2 object=" in result.stdout
    assert "vectors=" in result.stdout
    assert "B2 memories=" in result.stdout
    assert "B3 action=" in result.stdout
