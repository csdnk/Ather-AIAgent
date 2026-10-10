"""Execute portable product checks even when full real-backend admission is closed."""

import argparse
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from continuous import candidate, emit, junit_rows, run_command

PORTABLE = {
    "unit": [
        "tests/unit/" + name + ".py"
        for name in (
            "test_cache_locations",
            "test_cache_address_reads",
            "test_body_address_reads",
            "test_recall_address_assembly",
            "test_body_read_consumers",
        )
    ],
    "integration": ["tests/integration/test_p2_tls_transport.py"],
    "platform": ["tests/platform"],
}
REQUIRED = (
    "P3_TEST_STATE_DSN",
    "P3_TEST_REDIS_HOST",
    "P3_TEST_REDIS_PASSWORD",
    "P3_TEST_REDIS_CA_FILE",
    "P3_TEST_CEPH_ENDPOINT",
    "P3_TEST_CEPH_BUCKET",
    "P3_TEST_CEPH_ACCESS",
    "P3_TEST_CEPH_SECRET",
    "P3_TEST_MILVUS_URI",
    "P3_TEST_MILVUS_TOKEN",
    "P3_TEST_MILVUS_DATABASE",
    "P3_TEST_MILVUS_CA_FILE",
    "P3_TEST_MILVUS_SERVER_NAME",
    "P3_TEMPORAL_CLI",
)


def select(layer, env):
    missing = [key for key in REQUIRED if not env.get(key)]
    # Full cloud-backed fixtures need an explicit owned-test-target attestation;
    # merely finding production credentials is never permission to use them.
    admitted = not missing and env.get("AETHER_OWNED_TEST_TARGETS") == "verified"
    return {
        "paths": ["tests/" + layer] if admitted else PORTABLE[layer],
        "missing": missing,
        "full_layer_status": "NOT_RUN" if admitted else "BLOCKED",
    }


def scratch_directory(output, env):
    root = Path(env.get("QUALITY_TEST_TEMP_ROOT", Path(output).parent)).resolve()
    root.mkdir(parents=True, exist_ok=True)
    return Path(tempfile.mkdtemp(prefix="qt-", dir=root))


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--layer", choices=PORTABLE, required=True)
    p.add_argument("--output", type=Path, required=True)
    a = p.parse_args()
    a.output.mkdir(parents=True, exist_ok=False)
    selection = select(a.layer, os.environ)
    file = a.output / "raw.xml"
    rc = run_command(
        [
            sys.executable,
            "-m",
            "pytest",
            *selection["paths"],
            "-q",
            "--junitxml=" + str(file),
            "--basetemp=" + str(scratch_directory(a.output, os.environ)),
        ],
        a.output / "pytest.log",
        1200,
    )
    rows = junit_rows(file, a.layer)
    if rc and all(r["status"] == "PASS" for r in rows):
        rows.append(
            dict(
                id=a.layer + "/runner-exit",
                layer=a.layer,
                status="BLOCKED",
                reason="test process exited without complete evidence",
            )
        )
    if selection["full_layer_status"] == "BLOCKED":
        rows.append(
            dict(
                id=a.layer + "/real-backend-admission",
                layer=a.layer,
                status="BLOCKED",
                reason="Full layer requires verified owned test targets and: "
                + ", ".join(selection["missing"]),
            )
        )
    result = emit(a.output, rows, candidate(), "commit-" + a.layer)
    return {"PASS": 0, "FAIL": 1, "BLOCKED": 2}[result["execution_status"]]


if __name__ == "__main__":
    raise SystemExit(main())
