"""Installable entry point for the unified service and explicit deployment setup."""

import argparse
import hashlib
import json
import os
import secrets
from pathlib import Path
from typing import Any

import yaml

from aether_agent_memory.runtime.contracts.models import Permission

from .config import ServiceConfiguration


def initialize(
    directory: Path,
    *,
    embedding_profile: str,
    model: str | None,
    endpoint: str,
    host: str = "127.0.0.1",
    port: int = 8080,
    temporal_endpoint: str = "127.0.0.1:7233",
    temporal_namespace: str = "default",
    deployment_id: str | None = None,
) -> Path:
    directory = directory.resolve()
    paths = [
        directory / name
        for name in ("service.yaml", "identities.yaml", "credential", "embedding.json")
    ]
    if any(p.exists() for p in paths):
        raise FileExistsError("deployment files already exist; refusing to overwrite credentials")
    directory.mkdir(parents=True, exist_ok=True)
    token = secrets.token_urlsafe(32)
    identity = {
        "revision": 1,
        "tenants": [{"tenant_id": "local", "enabled": True}],
        "identities": [
            {
                "credential_sha256": hashlib.sha256(token.encode()).hexdigest(),
                "principal": {
                    "principal_id": "local_admin",
                    "auth_epoch": 1,
                    "home_scope": {
                        "tenant_id": "local",
                        "application_id": "app",
                        "user_id": "local_admin",
                        "agent_id": "agent",
                    },
                    "permissions": [p.value for p in Permission],
                },
            }
        ],
        "grants": [],
    }
    config: dict[str, Any] = {
        "profile": "local",
        "host": host,
        "port": port,
        "data_dir": "state",
        "identity_file": "identities.yaml",
        "embedding_profile": embedding_profile,
        "maintenance_principals": ["local_admin"],
        "temporal": {
            "deployment_id": deployment_id or secrets.token_hex(16),
            "endpoint": temporal_endpoint,
            "namespace": temporal_namespace,
        },
    }
    if embedding_profile == "native":
        config["embedding_config"] = "embedding.json"
        paths[3].write_text(json.dumps({"cache_dir": str(directory / "models")}), encoding="utf-8")
    if model:
        config["language_model"] = {"model": model, "endpoint": endpoint}
    paths[0].write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    paths[1].write_text(yaml.safe_dump(identity, sort_keys=False), encoding="utf-8")
    descriptor = os.open(paths[2], os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as output:
        output.write(token)
    return paths[0]


def main() -> int:
    parser = argparse.ArgumentParser(description="Persistent P3 memory service")
    commands = parser.add_subparsers(dest="command", required=True)
    init = commands.add_parser("init", help="create a deployment outside the source checkout")
    init.add_argument("--directory", type=Path, required=True)
    init.add_argument("--embedding-profile", choices=["native", "lexical"], default="native")
    init.add_argument("--model", help="configured chat model for semantic processing")
    init.add_argument("--model-endpoint", default="http://127.0.0.1:11434/v1")
    init.add_argument("--host", default="127.0.0.1")
    init.add_argument("--port", type=int, default=8080)
    init.add_argument("--temporal-endpoint", default="127.0.0.1:7233")
    init.add_argument("--temporal-namespace", default="default")
    init.add_argument("--deployment-id")
    serve = commands.add_parser("serve", help="run HTTP and persistent background workers")
    serve.add_argument("--config", type=Path, required=True)
    check = commands.add_parser(
        "check-config", help="validate deployment configuration without starting services"
    )
    check.add_argument("--config", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "init":
        path = initialize(
            args.directory,
            embedding_profile=args.embedding_profile,
            model=args.model,
            endpoint=args.model_endpoint,
            host=args.host,
            port=args.port,
            temporal_endpoint=args.temporal_endpoint,
            temporal_namespace=args.temporal_namespace,
            deployment_id=args.deployment_id,
        )
        print(
            json.dumps(
                {
                    "configuration": str(path),
                    "credential_file": str(path.parent / "credential"),
                    "semantic_processing": "model" if args.model else "literal_baseline",
                }
            )
        )
        return 0
    config = ServiceConfiguration.load(args.config)
    if args.command == "check-config":
        from .config import IdentityConfiguration

        IdentityConfiguration.model_validate(
            yaml.safe_load(config.identity_file.read_text(encoding="utf-8"))
        )
        print(json.dumps({"valid": True, "profile": config.profile}))
        return 0
    import uvicorn

    os.environ["AETHER_SERVICE_CONFIG"] = str(args.config.resolve())
    uvicorn.run(
        "aether_agent_memory.runtime.flows.application:application",
        factory=True,
        host=config.host,
        port=config.port,
        workers=1,
    )
    return 0
