"""Bounded post-deployment regression on existing services; never creates Pods.

The trusted local config is not accepted over HTTP. A forced timeout or unresolved
cleanup retains the admission lock until the original run is reconciled.
"""

import argparse
import json
import os
import re
import subprocess
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from continuous import candidate, emit, live_rows

# Native scenarios exist, but the bounded supervisor has admitted only
# public-working. Keep each missing required journey in every report so a
# successful smoke cannot silently become a complete business gate.
PENDING_BUSINESS = {
    "idempotent-scheduling": "ordinary-user task diagnostics contract needs adaptation",
    "longterm-new-session": "derived artifact and cross-session cleanup not admitted",
    "correction": "lost correction receipt and both versions need cleanup verification",
    "archive-activate": "lifecycle recovery cleanup not admitted",
    "legal-hold": "retention policy restoration must be verified before execution",
    "source-revoke": "source revocation and historical result cleanup not admitted",
    "cross-tenant": "two-tenant native scenario not admitted to supervisor",
    "empty-new-session": "new-session native scenario not admitted to supervisor",
    "token-budget": "budget boundary native scenario not admitted to supervisor",
    "source-read": "source range native scenario not admitted to supervisor",
    "scheduler-auth": "scheduler denial native scenario not admitted to supervisor",
    "hot-scheduling": "owned-object diagnostic identity and thermal cleanup missing",
}


def admitted(deployment, pods, digest):
    if not re.fullmatch(r"sha256:[a-f0-9]{64}", digest):
        return False
    spec, status = deployment.get("spec", {}), deployment.get("status", {})
    replicas = spec.get("replicas", 1)
    containers = spec.get("template", {}).get("spec", {}).get("containers", [])
    if len(containers) != 1 or not containers[0].get("image", "").endswith("@" + digest):
        return False
    if replicas < 1 or status.get("observedGeneration") != deployment["metadata"]["generation"]:
        return False
    if any(
        status.get(k, 0) != replicas
        for k in ("updatedReplicas", "readyReplicas", "availableReplicas")
    ):
        return False
    if len(pods) != replicas:
        return False
    return all(
        not p.get("metadata", {}).get("deletionTimestamp")
        and len(p.get("status", {}).get("containerStatuses", [])) == 1
        and all(
            c.get("ready") and c.get("imageID", "").endswith("@" + digest)
            for c in p["status"]["containerStatuses"]
        )
        for p in pods
    )


def clean_previous(root):
    for journal in Path(root).rglob("journal.json"):
        recovery = journal.with_name("reconciliation.json")
        try:
            state = json.loads(
                (recovery if recovery.exists() else journal).read_text(encoding="utf-8")
            )
            if state.get("cleanup", {}).get("status") != "PASS" or any(
                a.get("cleanup") not in ("deleted_and_absence_verified", "not_created_verified")
                for a in state.get("accounts", [])
            ):
                return False
        except (OSError, ValueError):
            return False
    return True


def inspect(config):
    def get(*args):
        raw = subprocess.check_output(
            [
                "kubectl",
                "--kubeconfig",
                config["kubeconfig"],
                "-n",
                "aether-p3-demo",
                "get",
                *args,
                "-o",
                "json",
            ],
            timeout=25,
        )
        return json.loads(raw)

    deployment = get("deployment", "aether-ruoyi-p3")
    labels = deployment["spec"]["selector"]["matchLabels"]
    selector = ",".join(k + "=" + v for k, v in sorted(labels.items()))
    pods = get("pods", "-l", selector)["items"]
    digest = config["expected_image_digest"]
    return admitted(deployment, pods, digest), {
        "namespace": "aether-p3-demo",
        "deployment": "aether-ruoyi-p3",
        "expected_image_digest": digest,
        "observed_images": [
            c.get("imageID") for p in pods for c in p.get("status", {}).get("containerStatuses", [])
        ],
        "observed_generation": deployment["status"].get("observedGeneration"),
    }


def execute(config, output, allow_model=False):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    root = Path(config["runs_root"])
    root.mkdir(parents=True, exist_ok=True)
    lock = root / "active-run.lock"
    rows = []
    source = candidate()
    acquired = False
    may_release = True
    try:
        if not clean_previous(root):
            raise ValueError("previous owned-data cleanup unresolved")
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        acquired = True
        with os.fdopen(fd, "w") as stream:
            json.dump({"output": str(output), "pid": os.getpid()}, stream)
        ok, observed = inspect(config)
        source["deployment"] = observed
        rows.append(
            dict(
                id="deployment/immutable-image",
                layer="admission",
                status="PASS" if ok else "BLOCKED",
                reason="expected digest, actual Pod digest and rollout readiness",
            )
        )
        if not ok:
            raise ValueError("candidate differs or rollout incomplete")
        live = root / uuid.uuid4().hex
        command = [
            config["python"],
            str(Path(__file__).with_name("live_business.py")),
            "--credentials",
            config["credentials"],
            "--native",
            config["native"],
            "--output",
            str(live),
        ]
        command += ["--allow-existing-model"] if allow_model else ["--prepare-only"]
        may_release = False
        with (output / "supervisor.log").open("wb") as stream:
            try:
                subprocess.run(
                    command, stdout=stream, stderr=subprocess.STDOUT, timeout=850, check=False
                )
            except subprocess.TimeoutExpired:
                may_release = False
                raise
        state = json.loads((live / "journal.json").read_text(encoding="utf-8"))
        result = (
            json.loads((live / "result.json").read_text(encoding="utf-8"))
            if (live / "result.json").exists()
            else {}
        )
        rows.extend(live_rows(state, result))
        if not allow_model:
            rows[-3].update(
                id="live/identity-prepare",
                layer="identity",
                reason="per-run ordinary accounts provisioned and removed; no memory write",
            )
            rows.append(
                dict(
                    id="live/business",
                    layer="system",
                    status="NOT_RUN",
                    reason="existing model execution not enabled for this invocation",
                )
            )
        may_release = clean_previous(root)
    except Exception as error:
        # Keep raw errors and credentials out of the report.
        rows.append(
            dict(
                id="deployment/runner",
                layer="admission",
                status="BLOCKED",
                reason=type(error).__name__ + "; inspect local evidence and admission lock",
            )
        )
        if acquired:
            may_release = may_release and clean_previous(root)
        elif lock.exists():
            may_release = False
    finally:
        rows.extend(
            dict(
                id="business/" + key,
                layer="system",
                status="NOT_RUN",
                required=True,
                reason=reason,
            )
            for key, reason in PENDING_BUSINESS.items()
        )
        for layer in (
            "browser-e2e",
            "performance",
            "capacity",
            "security-full",
            "recovery",
            "soak",
        ):
            rows.append(
                dict(
                    id=layer + "/acceptance",
                    layer=layer,
                    status="NOT_RUN",
                    required=False,
                    reason="separate release evidence; outside this deployment functional gate",
                )
            )
        report = emit(output, rows, source, "post-deploy")
        report["cleanup_pending"] = not may_release
        (output / "summary.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        if acquired and may_release:
            lock.unlink()
    return report


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--config", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--allow-existing-model", action="store_true")
    a = p.parse_args()
    report = execute(
        json.loads(a.config.read_text(encoding="utf-8-sig")), a.output, a.allow_existing_model
    )
    print(json.dumps({k: report[k] for k in ("run_id", "execution_status", "cleanup_pending")}))
    raise SystemExit({"PASS": 0, "FAIL": 1, "BLOCKED": 2}[report["execution_status"]])
