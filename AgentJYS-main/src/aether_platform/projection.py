"""Publish authoritative platform identities beside P3, without desktop tunnels."""

import argparse
import json
import time
from pathlib import Path
from typing import Any

import httpx
import yaml

from aether_platform.directory import Directory


def build_projection(
    tenants: list[dict[str, Any]], users: list[dict[str, Any]], issuer: str
) -> dict[str, Any]:
    value: dict[str, Any] = {
        "revision": 1,
        "tenants": [{"tenant_id": t["id"], "enabled": t["enabled"]} for t in tenants]
        + [{"tenant_id": "aether_platform_operations", "enabled": True}],
        "identities": [],
        "jwt_issuers": [],
    }
    mappings = []
    for user in users:
        if not user["enabled"] or user["role"] not in {"user", "platform_admin"}:
            continue
        principal = {
            "principal_id": user["id"],
            "home_scope": {
                "tenant_id": user["tenant_id"] or "aether_platform_operations",
                "user_id": user["id"],
                "application_id": "agent-platform",
                "agent_id": "aether",
            },
            "permissions": [
                "memory:read",
                "memory:write",
                "memory:correct",
                "memory:delete",
                "memory:history",
            ]
            if user["role"] == "user"
            else ["maintenance:diagnose", "maintenance:recover", "maintenance:configure"],
            "auth_epoch": user["version"],
        }
        value["identities"].append({"principal": principal})
        mappings.append({"subject": user["subject"], "principal_id": user["id"]})
    if mappings:
        value["jwt_issuers"].append(
            {
                "issuer": issuer,
                "jwks_url": "http://127.0.0.1:19081/jwks",
                "audience": "platform-bff",
                "subject_mappings": mappings,
            }
        )
    return value


def synchronize(config: dict[str, Any], target: Path) -> int:
    with Directory(config["database_dsn"]).connection() as conn:
        tenants = conn.execute("SELECT id,enabled FROM tenants ORDER BY id").fetchall()
        users = conn.execute("SELECT * FROM users ORDER BY id").fetchall()
    value = build_projection(tenants, users, config["issuer"])
    response = httpx.get(
        config["identity_connect_issuer"] + "/protocol/openid-connect/certs",
        timeout=15,
        trust_env=False,
    )
    response.raise_for_status()
    if not response.json().get("keys"):
        raise ValueError("No signing keys")
    path = target / "identities.yaml"
    if path.exists():
        old = yaml.safe_load(path.read_text())
        value["revision"] = old["revision"]
        if old != value:
            value["revision"] += 1
    # Publish keys first; the identity file is the revision boundary P3 reloads.
    for name, content in [
        ("jwks.json", json.dumps(response.json())),
        ("identities.yaml", yaml.safe_dump(value, allow_unicode=True)),
    ]:
        path = target / name
        if not path.exists() or path.read_text() != content:
            pending = path.with_suffix(".cloud-next")
            pending.write_text(content, encoding="utf-8")
            pending.chmod(0o600)
            pending.replace(path)
    return int(value["revision"])


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--target", type=Path, required=True)
    args = parser.parse_args()
    config = json.loads(args.config.read_text())
    while True:
        try:
            revision = synchronize(config, args.target)
            print("Cloud identity projection verified, revision", revision, flush=True)
        except Exception as error:
            print("Cloud identity projection retry:", type(error).__name__, flush=True)
        time.sleep(5)


if __name__ == "__main__":
    main()
