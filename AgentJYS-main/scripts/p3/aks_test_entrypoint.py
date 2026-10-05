"""AKS test container entrypoint; private environment arrives only on stdin."""

import hashlib
import json
import os
import subprocess
import sys
import time
import tomllib
from pathlib import Path


def main():
    values = json.load(sys.stdin)
    root = Path(__file__).resolve().parents[2]
    evidence = Path("/work/evidence")
    evidence.mkdir(exist_ok=True)
    os.chdir(root)
    env = dict(os.environ, USER=str(os.getuid()), HOME="/work/home", TMPDIR="/work/tmp",
               PYTHONDONTWRITEBYTECODE="1", PYTHONUTF8="1", PIP_NO_CACHE_DIR="1",
               XDG_CACHE_HOME="/work/cache", HF_HOME="/work/huggingface",
               TIKTOKEN_CACHE_DIR="/work/assets/tiktoken",
               PYTEST_ADDOPTS="", PYTHONUSERBASE="/work/home/python",
               PYTHONPATH=f"{root / 'src'}:{root / 'tests'}:/work/deps")
    for name in ("home", "tmp", "cache", "deps"):
        Path("/work", name).mkdir(exist_ok=True)
    project = tomllib.loads((root / "pyproject.toml").read_text())
    requirements = project["project"]["dependencies"] + project["dependency-groups"]["dev"]
    for extra in ("embedding-onnx", "resource-documents", "remember-ceph"):
        requirements += project["project"]["optional-dependencies"][extra]
    with (evidence / "install.log").open("w") as output:
        result = subprocess.run([sys.executable, "-B", "-m", "pip", "install", "--no-cache-dir",
                                 "--target", "/work/deps", *requirements], env=env,
                                stdout=output, stderr=subprocess.STDOUT, timeout=900)
    if result.returncode:
        print("test dependencies could not be installed")
        return result.returncode
    sys.path.insert(0, "/work/deps")
    import certifi
    from psycopg.conninfo import make_conninfo
    from run_aks_tests import redact

    assets = Path("/work/assets")
    native_path = assets / "native.json"
    native = json.loads(native_path.read_text())
    native["model_path"] = str(assets / "model")
    native["cache_dir"] = "/work/cache/embedding"
    native_path.write_text(json.dumps(native))
    values["P3_TEST_STATE_DSN"] = make_conninfo(values["P3_TEST_STATE_DSN"],
                                              sslrootcert=certifi.where())
    values.update(P3_TEST_NATIVE_CONFIG=str(native_path),
                  P3_NATIVE_MODEL_PATH=str(assets / "model"),
                  P3_TEST_MILVUS_CA_FILE=str(assets / "milvus-ca.crt"),
                  P3_TEST_REDIS_CA_FILE=certifi.where(), P3_TEMPORAL_CLI=str(assets / "temporal"),
                  P3_REQUIRE_POSTGRES="1", P3_REQUIRE_REDIS="1", P3_REQUIRE_MILVUS="1",
                  P3_REQUIRE_CEPH="1", P3_REQUIRE_REAL_MODEL="1")
    values.setdefault("P3_TEST_REDIS_PORT", "6380")
    values.update(P3_DIAGNOSTIC_ROOT=str(evidence), P3_DIAGNOSTIC_TAG="aks-tests")
    env.update(values)
    subprocess.run([sys.executable, "-B", "scripts/generate_proto.py"], env=env, check=True)
    hashes = json.loads(Path("/work/source-hashes.json").read_text())
    started = time.monotonic()
    command = [sys.executable, "-B", "-m", "pytest", *sys.argv[1:], "-p", "no:cacheprovider",
               "--basetemp=/work/pytest", "--junitxml=/work/evidence/junit.xml", "--tb=short"]
    with (evidence / "pytest.log").open("x") as output:
        child = subprocess.Popen(command, cwd=root, env=env, stdout=subprocess.PIPE,
                                 stderr=subprocess.STDOUT, text=True, errors="replace")
        for line in child.stdout:
            output.write(redact(line, values))
            output.flush()
        code = child.wait()
    junit = evidence / "junit.xml"
    if junit.exists():
        junit.write_text(redact(junit.read_text(), values))
    unchanged = all(hashlib.sha256((root / name).read_bytes()).hexdigest() == digest
                    for name, digest in hashes.items())
    (evidence / "execution.json").write_text(json.dumps({
        "exit": code, "seconds": round(time.monotonic() - started, 2),
        "source_unchanged": unchanged,
        "executed": not any(value in {"--collect-only", "--co"} for value in sys.argv[1:]),
        "boundary": "AKS test Pod, real Azure backends, isolated data and test-owned Temporal",
    }, indent=2))
    return code if unchanged else 1


if __name__ == "__main__":
    raise SystemExit(main())
