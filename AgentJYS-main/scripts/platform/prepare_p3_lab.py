"""Prepare the isolated Agent P3 instance, reusing explicitly supplied dependencies."""

import argparse
import asyncio
import json
from pathlib import Path

import psycopg
import yaml
from google.protobuf.duration_pb2 import Duration
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from temporalio.api.workflowservice.v1 import RegisterNamespaceRequest
from temporalio.client import Client
from temporalio.service import RPCError, RPCStatusCode

from aether_platform.directory import Directory


def publish_identities(platform: dict, directory: Path) -> None:
    db = Directory(platform["database_dsn"])
    with db.connection() as conn:
        tenants = conn.execute("SELECT id,enabled FROM tenants ORDER BY id").fetchall()
        users = conn.execute(
            "SELECT * FROM users WHERE role IN ('user','platform_admin') ORDER BY id"
        ).fetchall()
    if any(t["id"] == "aether_platform_operations" for t in tenants):
        raise ValueError("Reserved P3 operator scope conflicts with a business tenant")
    tenants.append({"id": "aether_platform_operations", "enabled": True})
    identity = {
        "revision": 1,
        "tenants": [{"tenant_id": t["id"], "enabled": t["enabled"]} for t in tenants],
        "identities": [],
        "jwt_issuers": [],
    }
    mappings = []
    for user in users:
        if not user["enabled"]:
            continue
        identity["identities"].append(
            {
                "principal": {
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
            }
        )
        mappings.append({"subject": user["subject"], "principal_id": user["id"]})
    identity["jwt_issuers"].append(
        {
            "issuer": platform["issuer"],
            "jwks_url": platform["issuer"] + "/protocol/openid-connect/certs",
            "audience": "platform-bff",
            "subject_mappings": mappings,
        }
    )
    target = directory / "identities.yaml"
    if not mappings:
        identity["jwt_issuers"] = []
    previous = yaml.safe_load(target.read_text(encoding="utf-8")) if target.exists() else None
    if previous:
        identity["revision"] = previous["revision"]
        if identity != previous:
            identity["revision"] += 1
    from aether_agent_memory.runtime.flows.config import IdentityConfiguration

    IdentityConfiguration.model_validate(identity)
    content = yaml.safe_dump(identity, allow_unicode=True, sort_keys=False)
    if not target.exists() or target.read_text(encoding="utf-8") != content:
        temp = target.with_suffix(".tmp")
        temp.write_text(content, encoding="utf-8")
        temp.replace(target)


async def namespace():
    client = await Client.connect("127.0.0.1:17233")
    try:
        await client.workflow_service.register_namespace(
            RegisterNamespaceRequest(
                namespace="aether-agent-20261005",
                workflow_execution_retention_period=Duration(seconds=604800),
            )
        )
    except RPCError as error:
        if error.status != RPCStatusCode.ALREADY_EXISTS:
            raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--platform", type=Path, required=True)
    parser.add_argument("--environment", type=Path, required=True)
    parser.add_argument("--template", type=Path, required=True)
    parser.add_argument("--directory", type=Path, required=True)
    args = parser.parse_args()
    directory = args.directory.resolve()
    directory.mkdir(parents=True, exist_ok=True)
    platform = json.loads(args.platform.read_text(encoding="utf-8-sig"))
    values = json.loads(args.environment.read_text(encoding="utf-8-sig"))
    template = args.template.resolve()
    config = yaml.safe_load(template.read_text(encoding="utf-8"))
    ca = template.parent / "certificates" / "azure-ca.pem"
    pg = conninfo_to_dict(values["P3_TEST_STATE_DSN"])
    pg.update(hostaddr="127.0.0.1", port="45432", sslmode="verify-full", sslrootcert=str(ca))
    pg.pop("options", None)
    dsn = make_conninfo("", **pg)
    schema = "aether_agent_20261005"
    with psycopg.connect(dsn) as conn:
        conn.execute(sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(sql.Identifier(schema)))
    config.update(
        host="127.0.0.1",
        port=19020,
        data_dir=str(directory / "runtime"),
        identity_file=str(directory / "identities.yaml"),
        maintenance_principals=[],
        identity_reload_seconds=2,
        http_wait_seconds=0.1,
    )
    storage = config["azure_storage"]
    storage["namespace"] = "aether-agent-20261005"
    storage["postgres"]["schema_name"] = schema
    storage["redis"].update(port=46380, ca_file=str(ca))
    storage["milvus"].update(
        uri="https://127.0.0.1:49530",
        collection="aether_agent_20261005",
        ca_file=str(template.parent / "certificates" / "milvus-ca.pem"),
    )
    config["temporal"] = {
        "deployment_id": "aether-agent-20261005",
        "endpoint": "127.0.0.1:17233",
        "namespace": "aether-agent-20261005",
        "task_queue_prefix": "agent",
    }
    embedding = {
        "backend": "onnx",
        "model_path": str(template.parent.parent / "models" / "bge"),
        "cache_dir": str(directory / "embedding-cache"),
        "threads": 2,
    }
    (directory / "embedding.json").write_text(json.dumps(embedding), encoding="utf-8")
    config["embedding_config"] = str(directory / "embedding.json")
    config["recall_config"] = str(template.parent / "recall.json")
    # Reuse the already selected real model provider; no private values go into YAML.
    config["language_model"].update(
        endpoint=platform["llm"]["endpoint"], model=platform["llm"]["model"]
    )
    environment = {
        "AETHER_POSTGRES_DSN": dsn,
        "AETHER_REDIS_PASSWORD": values["P3_TEST_REDIS_PASSWORD"],
        "AETHER_MILVUS_TOKEN": values["P3_TEST_MILVUS_TOKEN"],
        "AETHER_CEPH_ACCESS_KEY": values["P3_TEST_CEPH_ACCESS"],
        "AETHER_CEPH_SECRET_KEY": values["P3_TEST_CEPH_SECRET"],
        "AETHER_LLM_API_KEY": platform["llm"]["api_key"],
        "TIKTOKEN_CACHE_DIR": str(template.parent.parent / "models" / "tiktoken"),
    }
    (directory / "environment.private.json").write_text(json.dumps(environment), encoding="utf-8")
    (directory / "service.yaml").write_text(
        yaml.safe_dump(config, sort_keys=False), encoding="utf-8"
    )
    publish_identities(platform, directory)
    asyncio.run(namespace())
    print("Isolated P3 configuration and namespace prepared; existing P3 data unchanged.")


if __name__ == "__main__":
    main()
