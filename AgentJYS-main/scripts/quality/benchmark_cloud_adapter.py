"""Bounded disposable component job in the already owned quality namespace."""

import argparse
import hashlib
import json
import subprocess
import sys
import tarfile
from pathlib import Path

NS = "aether-quality-20261009"
OWNER = "aether-continuous-quality"
IMAGE = "aetherp3acr-a0bne7gpetbpdcbq.azurecr.io/aether/ruoyi-p3@sha256:700417ea63f3202c06465d4fc9e42859da19d3b2be02cd29e749f28faf3d715b"  # noqa: E501


def cleanup(call, dest):
    name = "quality-component-" + dest.name[:8]
    raw = call(["-n", NS, "get", "pod", name, "--ignore-not-found", "-o", "json"])
    if raw.strip():
        current = json.loads(raw)
        meta = current["metadata"]
        if meta["labels"].get("benchmark-job") != dest.name or meta["labels"].get("owner") != OWNER:
            raise RuntimeError("Cleanup ownership mismatch")
        # Kubernetes server enforces the UID, including if a replacement races this check.
        options = {
            "apiVersion": "v1",
            "kind": "DeleteOptions",
            "preconditions": {"uid": meta["uid"]},
        }
        call(
            ["delete", "--raw", "/api/v1/namespaces/" + NS + "/pods/" + name, "-f", "-"],
            json.dumps(options).encode(),
        )
        call(["-n", NS, "wait", "--for=delete", "pod/" + name, "--timeout=50s"])
    remains = call(["-n", NS, "get", "pod", name, "--ignore-not-found", "-o", "name"])
    if remains.strip():
        raise RuntimeError("Cleanup incomplete")
    (dest / "cleanup.json").write_text(json.dumps({"pod": name, "deleted": True}), encoding="utf-8")


def check_archive(path, kind):
    if not path.is_file():
        return False
    needed = (
        {
            "model.safetensors",
            "vocab.txt",
            "tokenizer_config.json",
            "special_tokens_map.json",
            "tokenizer.json",
            "config.json",
        }
        if kind == "compression"
        else {"bge/model_optimized.onnx", "bge/tokenizer.json", "bge/config.json"}
    )
    try:
        with tarfile.open(path) as archive:
            names = set(archive.getnames())
            if not needed <= names:
                return False
            for member in archive.getmembers():
                if (
                    member.name.startswith("/")
                    or ".." in Path(member.name).parts
                    or member.issym()
                    or member.islnk()
                ):
                    return False
                if member.isfile():
                    with archive.extractfile(member) as f:
                        count = 0
                        while chunk := f.read(1024 * 1024):
                            count += len(chunk)
                        if count != member.size:
                            return False
        return True
    except (tarfile.TarError, OSError, EOFError):
        return False


def run(config, kind, dest, cleanup_only=False):
    archive = Path(config[kind + "_archive"])
    if not cleanup_only and not check_archive(archive, kind):
        return {
            "status": "BLOCKED",
            "reason": "MODEL_ARCHIVE_MISSING_OR_INCOMPLETE",
            "profile": kind,
            "release_gate": "BLOCKED",
            "metrics": {"passed": 0},
            "cloud_job_created": False,
        }
    dataset = Path(config[kind + "_dataset"])
    script = Path(__file__).with_name("component_benchmark.py")
    if not cleanup_only and not dataset.is_file():
        return {"status": "BLOCKED", "reason": "FROZEN_DATASET_MISSING", "release_gate": "BLOCKED"}
    prefix = ["kubectl", "--kubeconfig", config["kubeconfig"], "--request-timeout=90s"]

    def call(args, data=None, timeout=100):
        p = subprocess.run(prefix + args, input=data, capture_output=True, timeout=timeout)
        if p.returncode:
            with (dest / "kubernetes-errors.log").open("ab") as f:
                f.write(p.stderr)
            raise RuntimeError("KUBERNETES_OPERATION_FAILED")
        return p.stdout

    ns = json.loads(call(["get", "namespace", NS, "-o", "json"]))
    if ns["metadata"]["labels"].get("owner") != OWNER:
        raise RuntimeError("Namespace owner mismatch")
    if cleanup_only:
        cleanup(call, dest)
        return {"status": "PASS", "cleanup_only": True}
    name = "quality-component-" + dest.name[:8]
    labels = {"owner": OWNER, "benchmark-job": dest.name}
    pod = {
        "apiVersion": "v1",
        "kind": "Pod",
        "metadata": {"name": name, "namespace": NS, "labels": labels},
        "spec": {
            "restartPolicy": "Never",
            "activeDeadlineSeconds": 480,
            "automountServiceAccountToken": False,
            "enableServiceLinks": False,
            "imagePullSecrets": [{"name": "acr-pull"}],
            "containers": [
                {
                    "name": "runner",
                    "image": IMAGE,
                    "command": ["python", "-c", "import time;time.sleep(480)"],
                    "resources": {
                        "requests": {
                            "cpu": "100m",
                            "memory": "1Gi" if kind == "compression" else "128Mi",
                        },
                        "limits": {
                            "cpu": "500m",
                            "memory": "3Gi" if kind == "compression" else "1Gi",
                        },
                    },
                    "securityContext": {
                        "allowPrivilegeEscalation": False,
                        "capabilities": {"drop": ["ALL"]},
                    },
                }
            ],
        },
    }
    (dest / "pod-intent.json").write_text(
        json.dumps({"name": name, "namespace": NS, "labels": labels}), encoding="utf-8"
    )
    report = None
    try:
        json.loads(call(["create", "-f", "-", "-o", "json"], json.dumps(pod).encode()))
        call(["-n", NS, "wait", "--for=condition=Ready", "pod/" + name, "--timeout=50s"])
        for source, target in [
            (archive, "/tmp/model.tar"),
            (dataset, "/tmp/dataset.json"),
            (script, "/tmp/component_benchmark.py"),
        ]:
            with source.open("rb") as f:
                p = subprocess.run(
                    prefix
                    + [
                        "-n",
                        NS,
                        "exec",
                        "-i",
                        name,
                        "--",
                        "python",
                        "-c",
                        'import shutil,sys;f=open(sys.argv[1],"wb");shutil.copyfileobj(sys.stdin.buffer,f);f.close()',  # noqa: E501
                        target,
                    ],
                    stdin=f,
                    capture_output=True,
                    timeout=240,
                )
            if p.returncode:
                raise RuntimeError("MODEL_OR_DATA_TRANSFER_FAILED")
        model = "/tmp/models/llmlingua" if kind == "compression" else "/tmp/models"
        call(
            [
                "-n",
                NS,
                "exec",
                name,
                "--",
                "python",
                "-c",
                'import pathlib,tarfile,sys;p=pathlib.Path(sys.argv[1]);p.mkdir(parents=True);tarfile.open("/tmp/model.tar").extractall(p,filter="data")',  # noqa: E501
                model,
            ]
        )
        args = [
            "python",
            "/tmp/component_benchmark.py",
            "--kind",
            kind,
            "--dataset",
            "/tmp/dataset.json",
            "--model-path",
            model if kind == "compression" else model + "/bge",
            "--output",
            "/tmp/results",
            "--hardware-id",
            "aks-shared-cpu-limit500m-memory"
            + ("3Gi" if kind == "compression" else "1Gi")
            + "-threads1",
            "--candidate",
            IMAGE.split("@")[1],
            "--repeats",
            "3",
            "--threads",
            "1",
            "--max-seconds",
            "180",
        ]
        p = subprocess.run(
            prefix + ["-n", NS, "exec", name, "--", *args], capture_output=True, timeout=210
        )
        (dest / "component-stdout.json").write_bytes(p.stdout)
        (dest / "component-stderr.log").write_bytes(p.stderr)
        initial = json.loads(p.stdout)
        run_id = initial["run_id"]
        for filename in ["summary.json", "agent-report.md", "worker.log", "events.jsonl"]:
            data = call(
                ["-n", NS, "exec", name, "--", "cat", "/tmp/results/" + run_id + "/" + filename]
            )
            (dest / filename).write_bytes(data)
        report = json.loads((dest / "summary.json").read_text(encoding="utf-8"))
        if p.returncode != {"PASS": 0, "FAIL": 1, "BLOCKED": 2}[report["status"]]:
            raise RuntimeError("Component exit disagrees")
        report["adapter"] = {
            "image": IMAGE,
            "namespace": NS,
            "dataset_sha256": hashlib.sha256(dataset.read_bytes()).hexdigest(),
            "profile": kind,
        }
    finally:
        cleanup(call, dest)
    return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", choices=["embedding", "compression"], required=True)
    parser.add_argument("--job-dir", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--cleanup-only", action="store_true")
    args = parser.parse_args()
    args.job_dir.mkdir(parents=True, exist_ok=True)
    try:
        report = run(
            json.loads(args.config.read_text(encoding="utf-8-sig")),
            args.profile,
            args.job_dir,
            args.cleanup_only,
        )
    except Exception as exc:
        report = {
            "status": "FAIL",
            "reason": type(exc).__name__,
            "release_gate": "BLOCKED",
            "cleanup_pending": not (args.job_dir / "cleanup.json").exists(),
        }
        (args.job_dir / "adapter-error.txt").write_text(str(exc), encoding="utf-8")
    (args.job_dir / ("cleanup-result.json" if args.cleanup_only else "summary.json")).write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return {"PASS": 0, "FAIL": 1, "BLOCKED": 2}[report["status"]]


if __name__ == "__main__":
    sys.exit(main())
