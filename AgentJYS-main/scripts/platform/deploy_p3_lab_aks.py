"""Deploy this isolated P3 lab beside its real dependencies; no public Service."""

import argparse
import io
import json
import os
import subprocess
import tarfile
from pathlib import Path

import httpx
import yaml
from psycopg.conninfo import conninfo_to_dict, make_conninfo

NAME = "aether-agent-p3"
IMAGE = "aetherp3acr-a0bne7gpetbpdcbq.azurecr.io/aether/p3:demo-db81fe0c2098-cadence-2d66c573"

BOOT = """import os,json,threading,sys
from http.server import HTTPServer,BaseHTTPRequestHandler
os.environ.update(json.load(open('/work/environment.private.json')))
os.environ.update(AETHER_SERVICE_CONFIG='/work/service.yaml',HF_HUB_OFFLINE='1',TOKENIZERS_PARALLELISM='false',OMP_NUM_THREADS='2',NO_PROXY='localhost,127.0.0.1')
sys.path.insert(0,'/work/src')
sys.path.insert(0,'/work/deps')
class Keys(BaseHTTPRequestHandler):
 def do_GET(self):
  if self.path!='/jwks': self.send_error(404); return
  data=open('/work/jwks.json','rb').read()
  self.send_response(200);self.send_header('Content-Type','application/json');self.end_headers();self.wfile.write(data)
 def log_message(self,*args): pass
threading.Thread(target=HTTPServer(('127.0.0.1',19081),Keys).serve_forever,daemon=True).start()
import uvicorn
uvicorn.run('aether_agent_memory.runtime.flows.application:application',factory=True,host='0.0.0.0',port=8080,access_log=False)
"""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kubeconfig", required=True)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--environment", type=Path, required=True)
    parser.add_argument("--models", type=Path, required=True)
    parser.add_argument("--update", action="store_true")
    args = parser.parse_args()
    work = args.directory.resolve()
    environment = {
        k: v
        for k, v in os.environ.items()
        if k.upper() not in {"HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"}
    }
    environment["KUBECTL_REMOTE_COMMAND_WEBSOCKETS"] = "false"
    kube = ["kubectl", "--kubeconfig", args.kubeconfig, "-n", "aether-p3-demo"]

    def call(argv, data=None, timeout=180):
        result = subprocess.run(
            kube + argv, input=data, capture_output=True, timeout=timeout, env=environment
        )
        if result.returncode:
            raise RuntimeError(
                "Kubernetes operation failed: " + result.stderr.decode(errors="replace")[:800]
            )
        return result.stdout

    exists = call(["get", "deployment", NAME, "--ignore-not-found", "-o", "name"])
    if exists:
        owned = json.loads(call(["get", "deployment", NAME, "-o", "json"]))
        if (
            not args.update
            or owned["metadata"]["labels"].get("aether-owner") != "agent-platform-20261005"
        ):
            raise RuntimeError("Existing deployment requires --update and matching ownership.")
    labels = {"app": NAME, "aether-owner": "agent-platform-20261005"}
    pvc = {
        "apiVersion": "v1",
        "kind": "PersistentVolumeClaim",
        "metadata": {"name": NAME, "labels": labels},
        "spec": {
            "accessModes": ["ReadWriteOnce"],
            "storageClassName": "managed-csi",
            "resources": {"requests": {"storage": "5Gi"}},
        },
    }
    container = {
        "name": "p3",
        "image": IMAGE,
        "command": [
            "python",
            "-u",
            "-c",
            "import os,time;\nwhile not os.path.isfile('/work/boot.py'): time.sleep(1)\n"
            "os.execv('/usr/local/bin/python',['python','-u','/work/boot.py'])",
        ],
        "securityContext": {
            "readOnlyRootFilesystem": True,
            "allowPrivilegeEscalation": False,
            "capabilities": {"drop": ["ALL"]},
        },
        "volumeMounts": [
            {"name": "work", "mountPath": "/work"},
            {"name": "tmp", "mountPath": "/tmp"},
        ],
        "resources": {
            "requests": {"cpu": "250m", "memory": "512Mi"},
            "limits": {"cpu": "2", "memory": "6Gi"},
        },
        "readinessProbe": {
            "httpGet": {"path": "/p3/readyz", "port": 8080},
            "periodSeconds": 5,
            "timeoutSeconds": 4,
        },
    }
    deployment = {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": {"name": NAME, "labels": labels},
        "spec": {
            "replicas": 1,
            "strategy": {"type": "Recreate"},
            "selector": {"matchLabels": {"app": NAME}},
            "template": {
                "metadata": {"labels": labels},
                "spec": {
                    "automountServiceAccountToken": False,
                    "securityContext": {
                        "runAsNonRoot": True,
                        "runAsUser": 10001,
                        "runAsGroup": 10001,
                        "fsGroup": 10001,
                    },
                    "imagePullSecrets": [{"name": "acr-pull"}],
                    "containers": [container],
                    "volumes": [
                        {"name": "work", "persistentVolumeClaim": {"claimName": NAME}},
                        {"name": "tmp", "emptyDir": {}},
                    ],
                },
            },
        },
    }
    if not exists:
        call(
            ["create", "-f", "-"],
            json.dumps({"apiVersion": "v1", "kind": "List", "items": [pvc, deployment]}).encode(),
        )
        print("Created isolated P3 deployment and persistent volume.", flush=True)
    import time

    pod = None
    for _ in range(36):
        values = json.loads(call(["get", "pods", "-l", "app=" + NAME, "-o", "json"]))["items"]
        if values and values[0].get("status", {}).get("phase") == "Running":
            pod = values[0]["metadata"]["name"]
            break
        time.sleep(5)
    if not pod:
        raise RuntimeError("Pod has not started; inspect owned deployment.")
    config = yaml.safe_load((work / "service.yaml").read_text())
    values = json.loads(args.environment.read_text(encoding="utf-8-sig"))
    env = json.loads((work / "environment.private.json").read_text())
    pg = conninfo_to_dict(values["P3_TEST_STATE_DSN"])
    pg.pop("hostaddr", None)
    pg.pop("options", None)
    pg.update(sslrootcert="/work/azure-ca.pem", sslmode="verify-full")
    env["AETHER_POSTGRES_DSN"] = make_conninfo("", **pg)
    env["TIKTOKEN_CACHE_DIR"] = "/work/models/tiktoken"
    config.update(
        host="0.0.0.0",
        port=8080,
        data_dir="/work/runtime",
        identity_file="/work/identities.yaml",
        embedding_config="/work/embedding.json",
        recall_config="/work/recall.json",
    )
    config["temporal"]["endpoint"] = "temporal:7233"
    store = config["azure_storage"]
    store["postgres"]["schema_name"] = "aether_agent_20261005_aks"
    old_ca = Path(store["redis"]["ca_file"])
    old_milvus_ca = Path(store["milvus"]["ca_file"])
    store["redis"].update(
        host=values["P3_TEST_REDIS_HOST"],
        port=int(values.get("P3_TEST_REDIS_PORT", 6380)),
        ca_file="/work/azure-ca.pem",
    )
    store["milvus"].update(uri=values["P3_TEST_MILVUS_URI"], ca_file="/work/milvus-ca.pem")
    identities = yaml.safe_load((work / "identities.yaml").read_text())
    config["maintenance_principals"] = [
        entry["principal"]["principal_id"]
        for entry in identities["identities"]
        if "maintenance:configure" in entry["principal"]["permissions"]
    ]
    issuer = identities["jwt_issuers"][0]["issuer"]
    # Mirror public keys over authenticated deployment transport. Tokens still
    # require real issuer/sub/audience/signature; the BFF checks live introspection.
    jwks = httpx.get(issuer + "/protocol/openid-connect/certs", trust_env=False).json()
    identities["jwt_issuers"][0]["jwks_url"] = "http://127.0.0.1:19081/jwks"
    embedding = {
        "backend": "onnx",
        "model_path": "/work/models/bge",
        "cache_dir": "/work/embedding-cache",
        "threads": 2,
    }
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w:gz") as archive:

        def add(name, data, mode=0o600):
            item = tarfile.TarInfo(name)
            item.size = len(data)
            item.mode = mode
            archive.addfile(item, io.BytesIO(data))

        for name, data in {
            "environment.private.json": json.dumps(env).encode(),
            "service.yaml": yaml.safe_dump(config).encode(),
            "identities.yaml": yaml.safe_dump(identities).encode(),
            "jwks.json": json.dumps(jwks).encode(),
            "embedding.json": json.dumps(embedding).encode(),
            "recall.json": Path(
                yaml.safe_load((work / "service.yaml").read_text())["recall_config"]
            ).read_bytes(),
            "azure-ca.pem": old_ca.read_bytes(),
            "milvus-ca.pem": old_milvus_ca.read_bytes(),
        }.items():
            add(name, data)
        root = Path(__file__).resolve().parents[2]
        for path in (root / "src").rglob("*.py"):
            add("src/" + path.relative_to(root / "src").as_posix(), path.read_bytes(), 0o644)
        for path in args.models.rglob("*"):
            if path.is_file():
                add("models/" + path.relative_to(args.models).as_posix(), path.read_bytes(), 0o644)
    call(
        ["exec", "-i", pod, "--", "tar", "--no-same-owner", "-xzf", "-", "-C", "/work"],
        stream.getvalue(),
        timeout=300,
    )
    # Start only after the complete payload is present.
    call(
        [
            "exec",
            pod,
            "--",
            "python",
            "-m",
            "pip",
            "install",
            "--no-cache-dir",
            "--disable-pip-version-check",
            "--target",
            "/work/deps",
            "opentelemetry-exporter-otlp-proto-http==1.45.0",
            "pypdf==6.19.0",
            "python-docx==1.2.0",
            "psycopg[binary,pool]==3.3.6",
            "boto3==1.43.108",
            "PyJWT[crypto]>=2.15.1,<3",
        ]
    )
    call(
        [
            "exec",
            pod,
            "--",
            "python",
            "-c",
            "import sys,json;sys.path.insert(0,'/work/deps');import psycopg;"
            "e=json.load(open('/work/environment.private.json'));"
            "c=psycopg.connect(e['AETHER_POSTGRES_DSN']);"
            "c.execute('CREATE SCHEMA IF NOT EXISTS aether_agent_20261005_aks');"
            "c.commit();c.close()",
        ]
    )
    call(
        [
            "exec",
            "-i",
            pod,
            "--",
            "python",
            "-c",
            "import sys;open('/work/boot.py','wb').write(sys.stdin.buffer.read())",
        ],
        BOOT.encode(),
    )
    (work / "aks-deployment.json").write_text(
        json.dumps(
            {
                "deployment": NAME,
                "pod": pod,
                "namespace": "aether-p3-demo",
                "kubeconfig": args.kubeconfig,
            }
        ),
        encoding="utf-8",
    )
    print(
        "Uploaded source, models and isolated configuration. Waiting for P3 readiness.",
        flush=True,
    )
    if exists:
        call(["rollout", "restart", "deployment/" + NAME])
    call(["rollout", "status", "deployment/" + NAME, "--timeout=180s"], timeout=190)
    print("P3 Azure lab ready; no public endpoint created.", flush=True)


if __name__ == "__main__":
    main()
