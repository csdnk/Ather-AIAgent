"""Stage a fresh Azure release, then cut over during explicit single-instance downtime.

Build the frontend first. No databases or identity records are changed. All
release-PVC consumers must belong to the platform Deployment; otherwise fail closed.
"""

import argparse
import hashlib
import json
import subprocess
import tarfile
from pathlib import Path
from uuid import uuid4

PARTS = ("app", "deps", "files.sha256.json")


def cutover(root: Path, stage: Path) -> None:
    """Called only after all application consumers stop; retain every old file."""
    backup = stage / "previous"
    backup.mkdir(exist_ok=False)
    # Fail before touching the active release if staging is incomplete.
    for name in PARTS:
        if not (stage / name).exists() or not (root / name).exists():
            raise RuntimeError("Incomplete staged or active release: " + name)
    for name in PARTS:
        (root / name).rename(backup / name)
        (stage / name).rename(root / name)


def restore(root: Path, stage: Path) -> None:
    """Also recovers an interrupted cutover; never overwrite the previous release."""
    for name in reversed(PARTS):
        previous = stage / "previous" / name
        if previous.exists():
            active = root / name
            if active.exists():
                active.rename(stage / ("failed-" + name))
            previous.rename(active)


def check_consumers(pods: list[dict], allowed_owners: set[str], helper: str = "") -> None:
    for pod in pods:
        if pod["metadata"]["name"] == helper:
            continue
        uses_release = any(
            v.get("persistentVolumeClaim", {}).get("claimName") == "aether-platform-release"
            for v in pod["spec"].get("volumes", [])
        )
        if uses_release and not any(
            owner.get("uid") in allowed_owners
            for owner in pod["metadata"].get("ownerReferences", [])
        ):
            raise RuntimeError("Release PVC has another consumer: " + pod["metadata"]["name"])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kubeconfig", required=True)
    parser.add_argument("--namespace", default="aether-p3-demo")
    parser.add_argument("--source", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--web", type=Path, required=True)
    parser.add_argument("--work-dir", type=Path, required=True)
    args = parser.parse_args()
    if not (args.web / "index.html").is_file():
        parser.error("--web must contain a built index.html")
    work = args.work_dir.resolve()
    work.mkdir(parents=True, exist_ok=True)
    kube = ["kubectl", "--kubeconfig", args.kubeconfig, "-n", args.namespace]

    def run(*command, **kwargs):
        return subprocess.run(
            kube + list(command), check=True, capture_output=True, timeout=600, **kwargs
        ).stdout

    def read(kind, *options):
        return json.loads(run("get", kind, *options, "-o", "json"))

    deployment = read("deployment/aether-platform")
    if deployment["spec"].get("replicas", 1) != 1:
        raise RuntimeError("This updater requires exactly one platform replica")
    uid = deployment["metadata"]["uid"]
    owners = {
        rs["metadata"]["uid"]
        for rs in read("replicasets")["items"]
        if any(o.get("uid") == uid for o in rs["metadata"].get("ownerReferences", []))
    }
    pods = read("pods")["items"]
    check_consumers(pods, owners)
    node = next(
        p["spec"]["nodeName"]
        for p in pods
        if p["status"]["phase"] == "Running"
        and any(o.get("uid") in owners for o in p["metadata"].get("ownerReferences", []))
    )
    env = deployment["spec"]["template"]["spec"]["containers"][0].get("env", [])
    old_sha = next((e.get("value") for e in env if e["name"] == "AETHER_RELEASE_SHA"), None)
    name = "aether-platform-release-update"
    release_id = uuid4().hex
    stage = "/release/releases/" + release_id
    pod = {
        "apiVersion": "v1",
        "kind": "Pod",
        "metadata": {"name": name},
        "spec": {
            "nodeName": node,
            "automountServiceAccountToken": False,
            "restartPolicy": "Never",
            "containers": [
                {
                    "name": "update",
                    "image": "python:3.13-slim",
                    "command": ["sleep", "infinity"],
                    "volumeMounts": [{"name": "release", "mountPath": "/release"}],
                }
            ],
            "volumes": [
                {
                    "name": "release",
                    "persistentVolumeClaim": {"claimName": "aether-platform-release"},
                }
            ],
        },
    }
    manifest = work / "release-update-pod.json"
    manifest.write_text(json.dumps(pod), encoding="utf-8")
    archive = work / "platform-release.tar.gz"
    with tarfile.open(archive, "w:gz") as bundle:
        for source, target in [
            (args.source / "src/aether_platform", "app/src/aether_platform"),
            (args.source / "src/aether_agent_memory", "app/src/aether_agent_memory"),
            (args.source / "scripts/platform/run_cloud.py", "app/run_cloud.py"),
            (args.web, "app/web"),
        ]:
            bundle.add(
                source,
                arcname=target,
                filter=lambda entry: (
                    None if "__pycache__" in entry.name or entry.name.endswith(".pyc") else entry
                ),
            )
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    # A fixed helper name prevents two updaters from entering the cutover concurrently.
    run("create", "-f", str(manifest))
    stopped = False
    switched = False
    recovered = True

    def remote(code, **kwargs):
        return run("exec", "-i", name, "--", "python", "-c", code, **kwargs)

    def helper(action):
        remote(
            "import runpy,pathlib;m=runpy.run_path('/tmp/update_release.py');"
            f"m[{action!r}](pathlib.Path('/release'),pathlib.Path({stage!r}))"
        )

    def stop():
        run("scale", "deployment/aether-platform", "--replicas=0")
        run("wait", "--for=delete", "pod", "-l", "app=aether-platform", "--timeout=180s")
        # No consumer, even a new ReplicaSet, may remain at the filesystem boundary.
        check_consumers(read("pods")["items"], set(), name)

    try:
        run("wait", "--for=condition=Ready", "pod/" + name, "--timeout=60s")
        remote("import pathlib;pathlib.Path(" + repr(stage) + ").mkdir(parents=True)")
        remote(
            "import sys,pathlib;pathlib.Path('/tmp/update_release.py').write_bytes("
            "sys.stdin.buffer.read())",
            input=Path(__file__).read_bytes(),
        )
        requirements = (args.source / "deploy/platform/requirements-cloud.txt").read_bytes()
        remote(
            "import sys,pathlib;pathlib.Path('/tmp/requirements.txt').write_bytes("
            "sys.stdin.buffer.read())",
            input=requirements,
        )
        run(
            "exec",
            name,
            "--",
            "pip",
            "install",
            "--target",
            stage + "/deps",
            "-r",
            "/tmp/requirements.txt",
        )
        with archive.open("rb") as stream:
            remote(
                "import sys,pathlib,hashlib,tarfile;"
                "p=pathlib.Path('/tmp/release.tar.gz');p.write_bytes(sys.stdin.buffer.read());"
                f"assert hashlib.sha256(p.read_bytes()).hexdigest()=={digest!r};"
                f"tarfile.open(p).extractall({stage!r},filter='data')",
                stdin=stream,
            )
        remote(
            "import pathlib,hashlib,json;"
            f"stage=pathlib.Path({stage!r});root=stage/'app';"
            "checks={str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest() "
            "for p in root.rglob('*') if p.is_file()};"
            "(stage/'files.sha256.json').write_text(json.dumps(checks,sort_keys=True))"
        )
        # Import from only the newly installed dependencies and source, before downtime.
        run(
            "exec",
            name,
            "--",
            "env",
            "PYTHONPATH=" + stage + "/app/src:" + stage + "/deps",
            "python",
            "-B",
            "-c",
            "import aether_platform.auth.bff",
        )
        check_consumers(read("pods")["items"], owners, name)
        stopped = True
        stop()
        switched = True  # A timed-out remote operation may already have changed files.
        helper("cutover")
        run("set", "env", "deployment/aether-platform", "AETHER_RELEASE_SHA=" + digest)
        run("scale", "deployment/aether-platform", "--replicas=1")
        run("rollout", "status", "deployment/aether-platform", "--timeout=180s")
        stopped = False
    except BaseException:
        if stopped:
            recovered = False
            stop()
            if switched:
                helper("restore")
            run(
                "set",
                "env",
                "deployment/aether-platform",
                "AETHER_RELEASE_SHA=" + old_sha if old_sha is not None else "AETHER_RELEASE_SHA-",
            )
            run("scale", "deployment/aether-platform", "--replicas=1")
            run("rollout", "status", "deployment/aether-platform", "--timeout=180s")
            recovered = True
        raise
    finally:
        if recovered:
            run("delete", "pod", name, "--wait=true", "--timeout=60s")
        else:
            print("Recovery incomplete; helper retained. Inspect deployment and", stage)
    (work / "release-manifest.json").write_text(
        json.dumps({"sha256": digest, "recovery_directory": stage + "/previous"}), encoding="utf-8"
    )
    print("Deployed application release:", digest)


if __name__ == "__main__":
    main()
