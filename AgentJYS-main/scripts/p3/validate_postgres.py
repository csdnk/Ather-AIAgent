"""Run the tenant/log/trace gate against one disposable upstream PostgreSQL database."""

import argparse
import json
import os
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

from psycopg.conninfo import conninfo_to_dict

ROOT = Path(__file__).resolve().parents[2]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--connections-file", type=Path)
    args = parser.parse_args()
    directory = args.directory.resolve()
    repository = next((p for p in ROOT.parents if (p / ".git").exists()), ROOT)
    if directory.is_relative_to(repository):
        parser.error("test output and private connections must stay outside the repository")
    env = {**os.environ, "PYTHONUTF8": "1", "P3_REQUIRE_POSTGRES": "1"}
    try:
        directory.mkdir(parents=True, exist_ok=True)
        for name in ("postgres-summary.json", "postgres.junit.xml"):
            (directory / name).unlink(missing_ok=True)
        if args.connections_file:
            value = json.loads(args.connections_file.read_text("utf-8"))
            if not isinstance(value, dict) or not isinstance(value.get("P3_TEST_STATE_DSN"), str):
                raise ValueError("invalid private connections file")
            env["P3_TEST_STATE_DSN"] = value["P3_TEST_STATE_DSN"]
        dsn = env.get("P3_TEST_STATE_DSN", "")
        if not dsn or not conninfo_to_dict(dsn).get("dbname", "").startswith("p3_test_"):
            raise ValueError("a disposable p3_test_* database is required")
    except Exception:
        parser.error("invalid test configuration; use a dedicated p3_test_* PostgreSQL database")
    report = directory / "postgres.junit.xml"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "tests/runtime/flows/test_postgres_observability.py",
            "-q",
            "-o",
            f"cache_dir={directory / 'pytest-cache'}",
            f"--basetemp={directory / 'pytest-temp'}",
            f"--junitxml={report}",
        ],
        cwd=ROOT,
        env=env,
        check=False,
    )
    if result.returncode:
        return result.returncode
    counts = {key: 0 for key in ("tests", "failures", "errors", "skipped")}
    for suite in ET.parse(report).getroot().iter("testsuite"):
        for key in counts:
            counts[key] += int(suite.get(key, "0"))
    passed = counts["tests"] > 0 and not any(
        counts[key] for key in ("failures", "errors", "skipped")
    )
    (directory / "postgres-summary.json").write_text(
        json.dumps({"passed": passed, **counts}), "utf-8"
    )
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
