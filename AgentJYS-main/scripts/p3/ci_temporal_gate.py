"""Provision a pinned, private development server for existing and new CI gates."""

import argparse
import os
import subprocess
import sys
from pathlib import Path

from temporal_dev import start, stop

ROOT = Path(__file__).resolve().parents[2]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("gate", choices=["foundation", "flows", "collaboration", "temporal"])
    parser.add_argument("--directory", required=True, type=Path)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    args.directory.mkdir(parents=True, exist_ok=True)
    binary = os.environ.get("P3_TEMPORAL_CLI")
    if not binary:
        installed = subprocess.check_output(
            [
                sys.executable,
                str(ROOT / "scripts/p3/install_temporal_cli.py"),
                "--directory",
                str(args.directory / "cli"),
            ],
            text=True,
        )
        binary = installed.strip()
    directory = args.directory / "server"
    try:
        state = start(directory, Path(binary))
        env = {
            **os.environ,
            "P3_TEMPORAL_CLI": binary,
            "P3_TEMPORAL_ENDPOINT": state["endpoint"],
            "PYTHONUTF8": "1",
            "PYTHONPATH": str(ROOT / "src"),
        }
        if args.gate == "temporal":
            for level in ("server", "process"):
                result = subprocess.run(
                    [
                        sys.executable,
                        "scripts/p3/validate_temporal.py",
                        "--level",
                        level,
                        "--directory",
                        str(args.directory / level),
                        "--report",
                        str(args.directory / f"{level}.json"),
                    ],
                    cwd=ROOT,
                    env=env,
                )
                if result.returncode:
                    return result.returncode
            return 0
        command = [
            sys.executable,
            f"scripts/p3/validate_{args.gate}.py",
            "--report",
            str(args.report or args.directory / "gate.json"),
        ]
        return subprocess.run(command, cwd=ROOT, env=env).returncode
    finally:
        stop(directory)


if __name__ == "__main__":
    raise SystemExit(main())
