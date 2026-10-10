"""Dedicated, quota-bounded Temporal 1.32 functional-test service.

Uses only the owned quality PostgreSQL. Its disposable storage and private
plaintext transport are explicitly not release durability/security acceptance.
"""

import json
import os
import re
from pathlib import Path

OWNER = "aether-continuous-quality"
SERVER = "temporalio/server@sha256:c3e752127759616bb1615e0f9ba0e21635aeb5fdeb922de4f371c350955f46ae"
TOOLS = (
    "temporalio/admin-tools@sha256:a9f84fb9a374b2374fe2e67c8efc0468ff3f1c66c8a0b14597ec86e349e62bca"
)
PYTHON = "aetherp3acr-a0bne7gpetbpdcbq.azurecr.io/aether/ruoyi-p3@sha256:700417ea63f3202c06465d4fc9e42859da19d3b2be02cd29e749f28faf3d715b"  # noqa: E501
DATABASES = ("quality_temporal", "quality_temporal_visibility")


def server_config(password, pod_ip):
    if not password or not pod_ip:
        raise ValueError("runtime password and pod IP are required")
    stores = {
        name: {
            "sql": {
                "pluginName": "postgres12",
                "databaseName": db,
                "connectAddr": "postgres:5432",
                "connectProtocol": "tcp",
                "user": "postgres",
                "password": password,
                "maxConns": 5,
                "maxIdleConns": 2,
                "maxConnLifetime": "1h",
            }
        }
        for name, db in zip(("default", "visibility"), DATABASES, strict=True)
    }
    return {
        "log": {"stdout": True, "level": "warn"},
        "persistence": {
            "defaultStore": "default",
            "visibilityStore": "visibility",
            "numHistoryShards": 1,
            "datastores": stores,
        },
        "global": {
            "membership": {"maxJoinDuration": "30s", "broadcastAddress": pod_ip},
            "metrics": {"prometheus": {"listenAddress": "0.0.0.0:9090", "timerType": "histogram"}},
        },
        "services": {
            name: {"rpc": {"grpcPort": port, "membershipPort": port - 300, "bindOnIP": "0.0.0.0"}}
            for name, port in [
                ("frontend", 7233),
                ("history", 7234),
                ("matching", 7235),
                ("worker", 7239),
            ]
        },
        "clusterMetadata": {
            "enableGlobalNamespace": False,
            "failoverVersionIncrement": 10,
            "masterClusterName": "quality",
            "currentClusterName": "quality",
            "clusterInformation": {
                "quality": {
                    "enabled": True,
                    "initialFailoverVersion": 1,
                    "rpcAddress": "127.0.0.1:7233",
                }
            },
        },
        "publicClient": {"hostPort": "127.0.0.1:7233"},
        "archival": {"history": {"state": "disabled"}, "visibility": {"state": "disabled"}},
        "namespaceDefaults": {
            "archival": {"history": {"state": "disabled"}, "visibility": {"state": "disabled"}}
        },
    }


def manifests(namespace):
    if not re.fullmatch(r"aether-quality-[a-z0-9-]{1,30}", namespace):
        raise ValueError("an owned quality namespace is required")

    def obj(kind, name, **values):
        return {
            "apiVersion": {"Deployment": "apps/v1", "Job": "batch/v1"}.get(kind, "v1"),
            "kind": kind,
            "metadata": {"name": name, "namespace": namespace, "labels": {"owner": OWNER}},
            **values,
        }

    def limits(memory="512Mi"):
        return {
            "requests": {"cpu": "100m", "memory": "128Mi"},
            "limits": {"cpu": "1", "memory": memory},
        }

    password = {
        "name": "SQL_PASSWORD",
        "valueFrom": {"secretKeyRef": {"name": "quality-database", "key": "postgres-password"}},
    }
    env = [
        password,
        {"name": "SQL_HOST", "value": "postgres"},
        {"name": "SQL_PORT", "value": "5432"},
        {"name": "SQL_USER", "value": "postgres"},
        {"name": "SQL_PLUGIN", "value": "postgres12"},
    ]
    mount = [
        {"name": "runtime", "mountPath": "/runtime"},
        {"name": "tools", "mountPath": "/tools", "readOnly": True},
    ]
    security = {"allowPrivilegeEscalation": False, "capabilities": {"drop": ["ALL"]}}
    schema = """set -eu
run_sql() {
 temporal-sql-tool "$@" >/tmp/schema-private.log 2>&1 || { echo 'schema operation failed; private log withheld'; exit 1; }
}
run_sql --database quality_temporal setup-schema -v 0.0
run_sql --database quality_temporal update-schema --schema-name postgresql/v12/temporal
run_sql --database quality_temporal_visibility setup-schema -v 0.0
run_sql --database quality_temporal_visibility update-schema --schema-name postgresql/v12/visibility
echo 'both dedicated Temporal schemas initialized'
"""  # noqa: E501
    job = obj(
        "Job",
        "quality-temporal-schema",
        spec={
            "backoffLimit": 0,
            "activeDeadlineSeconds": 300,
            "template": {
                "metadata": {"labels": {"app": "quality-temporal-schema", "owner": OWNER}},
                "spec": {
                    "restartPolicy": "Never",
                    "automountServiceAccountToken": False,
                    "enableServiceLinks": False,
                    "containers": [
                        {
                            "name": "schema",
                            "image": TOOLS,
                            "command": ["/bin/sh", "-ec", schema],
                            "env": env,
                            "resources": limits(),
                            "securityContext": security,
                        }
                    ],
                },
            },
        },
    )
    deployment = obj(
        "Deployment",
        "temporal",
        spec={
            "replicas": 1,
            "strategy": {"type": "Recreate"},
            "selector": {"matchLabels": {"app": "quality-temporal"}},
            "template": {
                "metadata": {"labels": {"app": "quality-temporal", "owner": OWNER}},
                "spec": {
                    "automountServiceAccountToken": False,
                    "enableServiceLinks": False,
                    "terminationGracePeriodSeconds": 60,
                    "securityContext": {"runAsUser": 1000, "runAsGroup": 1000, "fsGroup": 1000},
                    "imagePullSecrets": [{"name": "acr-pull"}],
                    "initContainers": [
                        {
                            "name": "render",
                            "image": PYTHON,
                            "command": ["python", "/tools/temporal_environment.py"],
                            "env": [
                                password,
                                {
                                    "name": "POD_IP",
                                    "valueFrom": {"fieldRef": {"fieldPath": "status.podIP"}},
                                },
                            ],
                            "resources": limits(),
                            "securityContext": security,
                            "volumeMounts": mount,
                        }
                    ],
                    "containers": [
                        {
                            "name": "temporal",
                            "image": SERVER,
                            "command": [
                                "temporal-server",
                                "--config-file",
                                "/runtime/config.yaml",
                                "start",
                            ],
                            "resources": limits("1Gi"),
                            "securityContext": security,
                            "ports": [{"containerPort": 7233}, {"containerPort": 9090}],
                            "volumeMounts": [
                                {"name": "runtime", "mountPath": "/runtime", "readOnly": True}
                            ],
                            "readinessProbe": {"tcpSocket": {"port": 7233}, "periodSeconds": 5},
                            "startupProbe": {
                                "tcpSocket": {"port": 7233},
                                "periodSeconds": 5,
                                "failureThreshold": 24,
                            },
                        }
                    ],
                    "volumes": [
                        {"name": "runtime", "emptyDir": {"medium": "Memory", "sizeLimit": "8Mi"}},
                        {"name": "tools", "configMap": {"name": "quality-temporal-tools"}},
                    ],
                },
            },
        },
    )
    return [
        obj(
            "ConfigMap",
            "quality-temporal-tools",
            data={"temporal_environment.py": Path(__file__).read_text(encoding="utf-8")},
        ),
        job,
        obj(
            "Service",
            "temporal",
            spec={
                "type": "ClusterIP",
                "selector": {"app": "quality-temporal"},
                "ports": [{"name": "grpc", "port": 7233}, {"name": "metrics", "port": 9090}],
            },
        ),
        deployment,
    ]


if __name__ == "__main__":
    value = server_config(os.environ["SQL_PASSWORD"], os.environ["POD_IP"])
    descriptor = os.open("/runtime/config.yaml", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        json.dump(value, stream)
