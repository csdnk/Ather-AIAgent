"""Render isolated AKS candidate resources. Does not contact or mutate Kubernetes.

Application images must be immutable digests. Start with replicas=0, migrate and
verify dedicated state, then explicitly render replicas=1 for internal checks.
No gateway routes, old workloads, existing PVCs, or plaintext Secrets are emitted.
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

PREFIX = "aether-ruoyi-"
LABELS = {"app.kubernetes.io/part-of": "aether-ruoyi-candidate"}


def render(images: dict[str, str], namespace: str, replicas: int) -> dict:
    required = {"backend", "frontend", "platform", "p3"}
    if required - images.keys():
        raise ValueError(f"Missing image digests: {sorted(required - images.keys())}")
    for name in required:
        if not re.fullmatch(r"[^\s]+@sha256:[0-9a-f]{64}", images[name]):
            raise ValueError(f"{name} must use an immutable image@sha256 digest")
    items: list[dict] = []

    def resource(kind: str, name: str, spec: dict, api: str = "v1") -> dict:
        return {"apiVersion": api, "kind": kind,
                "metadata": {"name": PREFIX + name, "namespace": namespace,
                             "labels": LABELS | {"app": PREFIX + name}}, "spec": spec}

    def volume_claim(name: str, size: str) -> None:
        items.append(resource("PersistentVolumeClaim", name, {
            "accessModes": ["ReadWriteOnce"], "storageClassName": "managed-csi",
            "resources": {"requests": {"storage": size}},
        }))

    def deploy(name: str, image: str, port: int, *, secret: str | None = None,
               pvc: tuple[str, str] | None = None, env: list | None = None,
               command: list | None = None, args: list | None = None,
               uid: int = 10001, memory: str = "512Mi", cpu: str = "100m",
               config_file: str | None = None, extra_volumes: list | None = None,
               extra_mounts: list | None = None, env_from: str | None = None,
               health: str | None = None) -> None:
        volumes = [{"name": "tmp", "emptyDir": {}}]
        mounts = [{"name": "tmp", "mountPath": "/tmp"}]
        if secret:
            volumes.append({"name": "config", "secret": {"secretName": PREFIX + secret}})
            mounts.append({"name": "config", "mountPath": "/config", "readOnly": True})
        if pvc:
            volumes.append(
                {"name": "data", "persistentVolumeClaim": {"claimName": PREFIX + pvc[0]}}
            )
            mounts.append({"name": "data", "mountPath": pvc[1]})
        volumes.extend(extra_volumes or [])
        mounts.extend(extra_mounts or [])
        probe = (
            {"httpGet": {"path": health, "port": port}} if health else {"tcpSocket": {"port": port}}
        )
        container = {
            "name": name, "image": image, "imagePullPolicy": "IfNotPresent",
            "ports": [{"containerPort": port}], "volumeMounts": mounts,
            "resources": {"requests": {"cpu": cpu, "memory": memory},
                          "limits": {
                              "cpu": "2", "memory": "3Gi" if name in {"p3", "backend"} else "1Gi"}},
            "securityContext": {"allowPrivilegeEscalation": False,
                                "capabilities": {"drop": ["ALL"]},
                                "readOnlyRootFilesystem": True},
            "startupProbe": probe | {"periodSeconds": 5, "failureThreshold": 120},
            "readinessProbe": probe | {"periodSeconds": 10, "failureThreshold": 3},
            "livenessProbe": {
                "tcpSocket": {"port": port}, "periodSeconds": 20, "failureThreshold": 6},
        }
        if command:
            container["command"] = command
        if args:
            container["args"] = args
        if env:
            container["env"] = env
        if env_from:
            container["envFrom"] = [{"secretRef": {"name": PREFIX + env_from}}]
        pod = {
            "automountServiceAccountToken": False,
            "securityContext": {"runAsNonRoot": True, "runAsUser": uid,
                                "runAsGroup": uid, "fsGroup": uid,
                                "seccompProfile": {"type": "RuntimeDefault"}},
            "terminationGracePeriodSeconds": 60,
            "containers": [container], "volumes": volumes,
        }
        items.append(resource("Deployment", name, {
            "replicas": replicas, "strategy": {"type": "Recreate"},
            "selector": {"matchLabels": {"app": PREFIX + name}},
            "template": {"metadata": {"labels": LABELS | {"app": PREFIX + name}}, "spec": pod},
        }, "apps/v1"))
        items.append(resource("Service", name, {
            "type": "ClusterIP", "selector": {"app": PREFIX + name},
            "ports": [{"port": port, "targetPort": port}],
        }))

    for name, size in [("mysql-data", "10Gi"), ("redis-data", "2Gi"),
                       ("platform-database", "10Gi"), ("p3-data", "10Gi"),
                       ("ops-backups", "10Gi")]:
        volume_claim(name, size)
    deploy("mysql", images.get(
        "mysql", "mysql@sha256:7dcddc01f13bab2f15cde676d44d01f61fc9f99fe7785e86196dfc07d358ae2b"
    ), 3306, uid=999,
           pvc=("mysql-data", "/var/lib/mysql"), env_from="mysql",
           args=["--datadir=/var/lib/mysql/data", "--socket=/tmp/mysql.sock",
                 "--pid-file=/tmp/mysql.pid"])
    deploy("redis", images.get(
        "redis", "redis@sha256:858f009f9709ce576febc734aa78b8f6d624b82571f9ddb6bda4377c833b3499"
    ), 6379, uid=999,
           secret="redis", pvc=("redis-data", "/data"), memory="128Mi",
           command=["redis-server", "/config/redis.conf"])
    deploy("platform-db", images.get(
        "postgres",
        "postgres@sha256:00bc86618629af00d2937fdc5a5d63db3ff8450acf52f0636ec813c7f4902929"
    ), 5432, uid=999,
           pvc=("platform-database", "/var/lib/postgresql/data"), env_from="platform-database",
           env=[{"name": "PGDATA", "value": "/var/lib/postgresql/data/pgdata"},
                {"name": "PGHOST", "value": "/tmp"}],
           args=["postgres", "-c", "unix_socket_directories=/tmp"])
    deploy("backend", images["backend"], 48080, secret="backend-config", memory="1Gi",
           env_from="backend-environment",
           env=[{"name": "LOGGING_FILE_NAME", "value": "/tmp/yudao-server.log"}])
    deploy("frontend", images["frontend"], 8080, uid=101, memory="64Mi", health="/healthz")
    deploy("platform", images["platform"], 19010, secret="platform-config",
           pvc=("ops-backups", "/var/lib/aether-ops/backups"),
           extra_volumes=[{"name": "ruoyi-client", "secret": {"secretName": PREFIX + "client"}}],
           extra_mounts=[{"name": "ruoyi-client", "mountPath": "/run/secrets", "readOnly": True}])
    deploy("p3", images["p3"], 8080, secret="p3-config", pvc=("p3-data", "/work"),
           memory="1Gi", cpu="250m", env_from="p3-environment",
           extra_volumes=[{"name": "ruoyi-client", "secret": {"secretName": PREFIX + "client"}}],
           extra_mounts=[{"name": "ruoyi-client", "mountPath": "/run/secrets", "readOnly": True}])
    return {"apiVersion": "v1", "kind": "List", "items": items}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--images", type=Path, required=True, help="JSON component -> immutable image ref")
    parser.add_argument(
        "--output", type=Path, required=True, help="External process workspace output")
    parser.add_argument("--namespace", default="aether-p3-demo")
    parser.add_argument("--replicas", type=int, choices=[0, 1], default=0)
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    result = render(
        json.loads(args.images.read_text(encoding="utf-8-sig")), args.namespace, args.replicas)
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(f"Rendered {len(result['items'])} candidate resources with replicas={args.replicas}.")


if __name__ == "__main__":
    main()
