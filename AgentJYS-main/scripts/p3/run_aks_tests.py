"""Run this checkout in a unique AKS Pod against isolated real Azure resources.

Private environment JSON and model/CA/Temporal assets must live outside Git.
Results are written to a new external directory. The runner never replaces a
resident Pod, changes a Deployment, or passes credentials on a command line.
"""

import argparse
import hashlib
import io
import json
import os
import subprocess
import sys
import tarfile
import tempfile
import xml.etree.ElementTree as ET
from contextlib import suppress
from pathlib import Path
from uuid import uuid4

REQUIRED = (
    "P3_TEST_STATE_DSN",
    "P3_TEST_REDIS_HOST",
    "P3_TEST_REDIS_PASSWORD",
    "P3_TEST_MILVUS_URI",
    "P3_TEST_MILVUS_TOKEN",
    "P3_TEST_MILVUS_SERVER_NAME",
    "P3_TEST_MILVUS_DATABASE",
    "P3_TEST_CEPH_ENDPOINT",
    "P3_TEST_CEPH_BUCKET",
    "P3_TEST_CEPH_ACCESS",
    "P3_TEST_CEPH_SECRET",
    "P3_TEST_LLM_ENDPOINT",
    "P3_TEST_LLM_MODEL",
    "P3_TEST_LLM_KEY",
)
DEFAULT_IMAGE = (
    "python:3.13-slim@sha256:bb2988715db2cf7ace7b53f38f3cffbef7c7046a656bee66245eb0ed386e2e81"
)


def external_directory(directory, repository):
    value = Path(directory).expanduser().resolve()
    if value == repository or repository in value.parents:
        raise ValueError("processing files must live outside the repository")
    return value


def owns_pod(pod, uid):
    return bool(uid) and pod.get("metadata", {}).get("uid") == uid


def delete_owned_pod(call, namespace, name, uid):
    """The API server checks UID atomically, even if a name was reused."""
    if not uid:
        return False
    options = {"apiVersion": "v1", "kind": "DeleteOptions", "preconditions": {"uid": uid}}
    result = call(
        ["delete", f"--raw=/api/v1/namespaces/{namespace}/pods/{name}", "-f", "-"],
        data=json.dumps(options).encode(),
    )
    return result.returncode == 0


def cleanup_owned_pod(call, namespace, name, uid):
    """Confirm absence of the original UID; query errors never prove cleanup."""

    def inspect():
        result = call(["get", "pod", name, "--ignore-not-found", "-o", "json"])
        if result.returncode:
            raise RuntimeError("API could not confirm the test Pod identity")
        if not result.stdout.strip():
            return "absent"
        pod = json.loads(result.stdout)
        current_uid = pod.get("metadata", {}).get("uid")
        if not current_uid:
            raise RuntimeError("API returned a Pod without its identity")
        return "owned" if current_uid == uid else "replaced"

    state = inspect()
    if state != "owned":
        return {"confirmed": True, "state": state}
    if not delete_owned_pod(call, namespace, name, uid):
        raise RuntimeError("API refused UID-conditioned deletion of the test Pod")
    call(["wait", "--for=delete", "pod/" + name, "--timeout=60s"], timeout=75)
    state = inspect()
    if state == "owned":
        raise RuntimeError("the original test Pod still exists after deletion")
    return {"confirmed": True, "state": state}


def validate_environment(values):
    for key in REQUIRED:
        if not isinstance(values.get(key), str) or not values[key]:
            raise ValueError("missing required Azure test setting: " + key)
    from psycopg.conninfo import conninfo_to_dict

    pg = conninfo_to_dict(values["P3_TEST_STATE_DSN"])
    if not pg.get("dbname", "").startswith("p3_test_"):
        raise ValueError("AKS tests require an independent p3_test_ PostgreSQL database")
    if pg.get("sslmode") != "verify-full":
        raise ValueError("Azure PostgreSQL test connection requires sslmode=verify-full")


def redact(text, values):
    secrets = [
        value
        for key, value in values.items()
        if any(word in key for word in ("DSN", "PASSWORD", "TOKEN", "KEY", "SECRET", "ACCESS"))
    ]
    if values.get("P3_TEST_STATE_DSN"):
        from psycopg.conninfo import conninfo_to_dict

        with suppress(Exception):
            secrets.append(conninfo_to_dict(values["P3_TEST_STATE_DSN"]).get("password", ""))
    for value in sorted(set(secrets), key=len, reverse=True):
        if value:
            text = text.replace(value, "[REDACTED]")
    return text


def verify_evidence(junit, execution, returncode, *, allow_skips=False):
    try:
        cases = list(ET.parse(junit).iter("testcase"))
    except (OSError, ET.ParseError):
        cases = []
    skipped = sum(case.find("skipped") is not None for case in cases)
    failed = sum(any(case.find(tag) is not None for tag in ("failure", "error")) for case in cases)
    return {
        "passed": bool(
            returncode == 0
            and execution.get("exit") == 0
            and cases
            and execution.get("source_unchanged") is True
            and execution.get("executed") is True
            and not failed
            and (allow_skips or not skipped)
            and len(cases) > skipped
        ),
        "test_cases": len(cases),
        "failed": failed,
        "skipped": skipped,
    }


def add_bytes(package, name, data, mode=0o644):
    item = tarfile.TarInfo(name)
    item.size, item.mode = len(data), mode
    package.addfile(item, io.BytesIO(data))


def source_archive(root, assets):
    stream, hashes = io.BytesIO(), {}
    with tarfile.open(fileobj=stream, mode="w:gz") as package:
        marker = tarfile.TarInfo("repository/.git")
        marker.type = tarfile.DIRTYPE
        package.addfile(marker)
        paths = []
        for directory in (
            "src",
            "tests",
            "scripts",
            "configs",
            "contracts",
            "docs",
            "examples",
            "deploy",
            "engine/proto",
            "web",
        ):
            paths.extend((root / directory).rglob("*"))
        paths.extend(
            root / name
            for name in (
                "pyproject.toml",
                "README.md",
                "CONTRIBUTING.md",
                "compose.p3.yaml",
                "Dockerfile.p3",
                "Makefile",
            )
        )
        for path in sorted(set(paths)):
            if not path.is_file() or path.is_symlink():
                continue
            if set(path.parts) & {"__pycache__", "node_modules", "dist", ".vite", ".git"}:
                continue
            if path.suffix in {".pyc", ".pyo"}:
                continue
            if (path.name == ".env" or path.name.startswith(".env.")) and not (
                path.name.endswith(".example") or path.name.endswith(".template")
            ):
                continue
            if path.name in {"credential", "credentials.json", "connections.json"}:
                continue
            name, data = path.relative_to(root).as_posix(), path.read_bytes()
            hashes[name] = hashlib.sha256(data).hexdigest()
            add_bytes(package, "repository/AgentJYS-main/" + name, data)
        add_bytes(package, "source-hashes.json", json.dumps(hashes).encode())
        for path in sorted(assets.rglob("*")):
            if path.is_symlink():
                raise ValueError("test assets must contain regular files, without symlinks")
            if path.is_file():
                rel = path.relative_to(assets).as_posix()
                add_bytes(
                    package,
                    "assets/" + rel,
                    path.read_bytes(),
                    0o755 if rel == "temporal" else 0o644,
                )
    return stream.getvalue(), hashes


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kubeconfig", type=Path)
    parser.add_argument("--namespace", default="aether-p3-demo")
    parser.add_argument("--environment-file", required=True, type=Path)
    parser.add_argument("--assets-directory", required=True, type=Path)
    parser.add_argument("--directory", required=True, type=Path)
    parser.add_argument("--image", default=DEFAULT_IMAGE)
    parser.add_argument("--image-pull-secret")
    parser.add_argument("--timeout-seconds", type=int, default=7200)
    parser.add_argument(
        "--allow-skips",
        action="store_true",
        help="Report optional capability skips explicitly; Azure gates use no skips",
    )
    parser.add_argument("pytest_args", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    root = Path(__file__).resolve().parents[2]
    repository = next((p for p in (root, *root.parents) if (p / ".git").exists()), root)
    try:
        external_directory(args.environment_file, repository)
        values = json.loads(args.environment_file.read_text(encoding="utf-8-sig"))
        assets = external_directory(args.assets_directory, repository)
        directory = external_directory(args.directory, repository)
        validate_environment(values)
        for name in ("native.json", "milvus-ca.crt", "temporal"):
            if not (assets / name).is_file():
                raise ValueError("required external test asset is missing: " + name)
        if not (assets / "model").is_dir():
            raise ValueError("required external BGE model directory is missing")
        if not 60 <= args.timeout_seconds <= 28800:
            raise ValueError("timeout must be between 60 and 28800 seconds")
    except (ValueError, OSError) as error:
        parser.error(str(error))
    directory.mkdir(parents=True, exist_ok=True)
    run = Path(tempfile.mkdtemp(prefix="aks-tests-", dir=directory))
    pod_name = "p3-test-" + uuid4().hex[:20]
    env = dict(os.environ)
    kube = ["kubectl"]
    if args.kubeconfig:
        kube.extend(("--kubeconfig", str(args.kubeconfig.resolve())))
    kube.extend(("-n", args.namespace))

    def call(arguments, *, data=None, timeout=60):
        return subprocess.run(
            kube + arguments, input=data, env=env, capture_output=True, timeout=timeout
        )

    container = {
        "name": "tests",
        "image": args.image,
        "command": ["python", "-u", "-c", f"import time;time.sleep({args.timeout_seconds})"],
        "securityContext": {
            "readOnlyRootFilesystem": True,
            "allowPrivilegeEscalation": False,
            "capabilities": {"drop": ["ALL"]},
        },
        "resources": {
            "requests": {"cpu": "250m", "memory": "512Mi"},
            "limits": {"cpu": "2", "memory": "10Gi"},
        },
        "volumeMounts": [
            {"name": "work", "mountPath": "/work"},
            {"name": "tmp", "mountPath": "/tmp"},
        ],
    }
    manifest = {
        "apiVersion": "v1",
        "kind": "Pod",
        "metadata": {
            "name": pod_name,
            "namespace": args.namespace,
            "labels": {"app": "p3-isolated-tests"},
        },
        "spec": {
            "restartPolicy": "Never",
            "activeDeadlineSeconds": args.timeout_seconds,
            "automountServiceAccountToken": False,
            "securityContext": {
                "runAsNonRoot": True,
                "runAsUser": 10001,
                "runAsGroup": 10001,
                "fsGroup": 10001,
                "seccompProfile": {"type": "RuntimeDefault"},
            },
            "volumes": [
                {"name": "work", "emptyDir": {"sizeLimit": "10Gi"}},
                {"name": "tmp", "emptyDir": {"sizeLimit": "1Gi"}},
            ],
            "containers": [container],
        },
    }
    if args.image_pull_secret:
        manifest["spec"]["imagePullSecrets"] = [{"name": args.image_pull_secret}]
    archive, hashes = source_archive(root, assets)
    (run / "source-hashes.json").write_text(json.dumps(hashes, indent=2), "utf-8")
    uid, code = None, 1
    gate = {"passed": False, "test_cases": 0, "failed": 0, "skipped": 0}
    cleanup = {"confirmed": True, "state": "not_created"}
    try:
        created = call(["create", "-f", "-", "-o", "json"], data=json.dumps(manifest).encode())
        if created.returncode:
            raise RuntimeError(
                "AKS Pod creation failed: " + created.stderr.decode(errors="replace")
            )
        uid = json.loads(created.stdout)["metadata"]["uid"]
        print(f"AKS test Pod: {args.namespace}/{pod_name}; evidence: {run}", flush=True)
        ready = call(
            ["wait", "--for=condition=Ready", "pod/" + pod_name, "--timeout=120s"], timeout=135
        )
        if ready.returncode:
            raise RuntimeError(
                "test Pod did not become ready; check image, network and namespace RBAC"
            )
        uploaded = call(
            ["exec", "-i", pod_name, "--", "tar", "--no-same-owner", "-xzf", "-", "-C", "/work"],
            data=archive,
            timeout=300,
        )
        if uploaded.returncode:
            raise RuntimeError("test snapshot upload failed")
        arguments = args.pytest_args
        if arguments[:1] == ["--"]:
            arguments = arguments[1:]
        result = call(
            [
                "exec",
                "-i",
                pod_name,
                "--",
                "python",
                "-B",
                "/work/repository/AgentJYS-main/scripts/p3/aks_test_entrypoint.py",
                *(arguments or ["tests"]),
            ],
            data=json.dumps(values).encode(),
            timeout=args.timeout_seconds,
        )
        (run / "transport.log").write_text(
            redact((result.stdout + result.stderr).decode(errors="replace"), values), "utf-8"
        )
        fetched = call(
            ["exec", pod_name, "--", "tar", "-czf", "-", "-C", "/work", "evidence"], timeout=120
        )
        if fetched.returncode == 0:
            with tarfile.open(fileobj=io.BytesIO(fetched.stdout), mode="r:gz") as package:
                for item in package.getmembers():
                    if item.isfile() and Path(item.name).suffix in {".json", ".xml", ".log"}:
                        data = package.extractfile(item).read().decode(errors="replace")
                        (run / Path(item.name).name).write_text(redact(data, values), "utf-8")
        execution_path = run / "execution.json"
        execution = json.loads(execution_path.read_text()) if execution_path.is_file() else {}
        gate = verify_evidence(
            run / "junit.xml", execution, result.returncode, allow_skips=args.allow_skips
        )
        code = int(not gate["passed"])
    except (RuntimeError, OSError, ValueError, subprocess.TimeoutExpired) as error:
        (run / "runner-error.log").write_text(redact(str(error), values), "utf-8")
        print(
            "AKS test execution did not complete; inspect external runner-error.log",
            file=sys.stderr,
        )
    finally:
        if uid:
            try:
                cleanup = cleanup_owned_pod(call, args.namespace, pod_name, uid)
            except (RuntimeError, OSError, ValueError, subprocess.TimeoutExpired) as error:
                cleanup = {"confirmed": False, "state": "unconfirmed"}
                (run / "cleanup-error.log").write_text(redact(str(error), values), "utf-8")
                code = 1
    report = {
        "pod": pod_name,
        "pod_uid": uid,
        "namespace": args.namespace,
        "image": args.image,
        **gate,
        "tests_passed": gate["passed"],
        "passed": code == 0 and cleanup["confirmed"],
        "exit": code,
        "cleanup": cleanup,
        "source_files": len(hashes),
        "resident_deployments_modified": False,
    }
    (run / "summary.json").write_text(json.dumps(report, indent=2), "utf-8")
    print(json.dumps(report), flush=True)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
