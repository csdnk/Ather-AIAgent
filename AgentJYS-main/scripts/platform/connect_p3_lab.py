"""Maintain the private P3 tunnel and publish current business identity projections."""

import argparse
import hashlib
import json
import os
import subprocess
import time
from pathlib import Path

import httpx
import yaml
from prepare_p3_lab import publish_identities

REMOTE_WRITE = """import sys,json,pathlib,hashlib,os
values=json.load(sys.stdin)
for name,content in values.items():
 assert name in ('jwks.json','identities.yaml')
 p=pathlib.Path('/work')/name
 t=p.with_suffix('.next');t.write_text(content);t.chmod(0o600);t.replace(p)
hashes={n:hashlib.sha256((pathlib.Path('/work')/n).read_bytes()).hexdigest() for n in values}
print(json.dumps(hashes))
"""


def synchronize(platform, directory, kube, environment):
    publish_identities(platform, directory)
    identity = yaml.safe_load((directory / "identities.yaml").read_text(encoding="utf-8"))
    response = httpx.get(platform["issuer"] + "/protocol/openid-connect/certs", trust_env=False)
    response.raise_for_status()
    jwks = response.json()
    if not jwks.get("keys"):
        raise RuntimeError("Identity provider returned no signing keys")
    for issuer in identity["jwt_issuers"]:
        issuer["jwks_url"] = "http://127.0.0.1:19081/jwks"
    values = {
        "jwks.json": json.dumps(jwks),
        "identities.yaml": yaml.safe_dump(identity, allow_unicode=True),
    }
    expected = {n: hashlib.sha256(v.encode()).hexdigest() for n, v in values.items()}
    state = directory / "projection-readback.json"
    previous = json.loads(state.read_text()) if state.exists() else {}
    if previous.get("hashes") == expected and time.time() - previous.get("verified_at", 0) < 60:
        return
    result = subprocess.run(
        kube + ["exec", "-i", "deployment/aether-agent-p3", "--", "python", "-c", REMOTE_WRITE],
        input=json.dumps(values).encode(),
        capture_output=True,
        timeout=30,
        env=environment,
    )
    if result.returncode or json.loads(result.stdout) != expected:
        raise RuntimeError("P3 identity projection readback failed")
    temp = state.with_suffix(".next")
    temp.write_text(
        json.dumps(
            {"hashes": expected, "revision": identity["revision"], "verified_at": time.time()}
        )
    )
    temp.replace(state)
    print("P3 identity projection verified, revision", identity["revision"], flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--platform", type=Path, required=True)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--kubectl", required=True)
    parser.add_argument("--kubeconfig", required=True)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    environment = {
        k: v
        for k, v in os.environ.items()
        if k.upper() not in {"HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"}
    }
    environment["KUBECTL_REMOTE_COMMAND_WEBSOCKETS"] = "false"
    environment["KUBECTL_PORT_FORWARD_WEBSOCKETS"] = "false"
    kube = [args.kubectl, "--kubeconfig", args.kubeconfig, "-n", "aether-p3-demo"]
    owned = subprocess.run(
        kube + ["get", "deployment", "aether-agent-p3", "-o", "json"],
        capture_output=True,
        timeout=30,
        check=True,
        env=environment,
    )
    if (
        json.loads(owned.stdout)["metadata"]["labels"].get("aether-owner")
        != "agent-platform-20261005"
    ):
        raise RuntimeError("Deployment ownership mismatch")
    platform = json.loads(args.platform.read_text(encoding="utf-8-sig"))
    tunnel = None
    try:
        while True:
            try:
                synchronize(platform, args.directory, kube, environment)
                if args.once:
                    return
                if tunnel is None or tunnel.poll() is not None:
                    tunnel = subprocess.Popen(
                        kube
                        + [
                            "port-forward",
                            "--address=127.0.0.1",
                            "deployment/aether-agent-p3",
                            "19020:8080",
                        ],
                        env=environment,
                    )
                (args.directory / "connection-status.json").write_text(
                    json.dumps(
                        {
                            "updated_at": time.time(),
                            "projection_ok": True,
                            "tunnel_pid": tunnel.pid,
                        }
                    )
                )
            except Exception as error:
                # Do not emit subprocess credentials or data; BFF continues live auth checks.
                print("P3 connection needs retry:", type(error).__name__, flush=True)
                (args.directory / "connection-status.json").write_text(
                    json.dumps(
                        {
                            "updated_at": time.time(),
                            "projection_ok": False,
                        }
                    )
                )
                if args.once:
                    raise RuntimeError("P3 projection not confirmed") from None
            time.sleep(5)
    finally:
        if tunnel is not None and tunnel.poll() is None:
            tunnel.terminate()
            tunnel.wait(timeout=10)


if __name__ == "__main__":
    main()
