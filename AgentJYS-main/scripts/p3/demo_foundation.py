"""Run an isolated engineering example using the actual foundation services."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import secrets
import sys
from hashlib import sha256
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from aether_agent_memory.runtime.contracts.models import Permission, Principal, Scope  # noqa: E402
from aether_agent_memory.runtime.foundation.host import Foundation  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Create an isolated P3 foundation example")
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--temporal-endpoint", default="127.0.0.1:7233")
    parser.add_argument("--postgres-dsn-env", default="AETHER_POSTGRES_DSN")
    args = parser.parse_args()
    dsn = os.environ.get(args.postgres_dsn_env)
    if not dsn:
        parser.error("set --postgres-dsn-env to the name of a PostgreSQL credential variable")
    directory = args.directory.resolve()
    directory.mkdir(parents=True, exist_ok=True)
    credential_path = directory / "demo-credentials.json"
    database = directory / "foundation.db"
    if database.exists() or credential_path.exists():
        parser.error("choose a new directory; existing demo state will not be overwritten")
    credentials = {name: secrets.token_urlsafe(32) for name in ("alice", "bob", "carol", "david")}
    with credential_path.open("x", encoding="utf-8") as file:
        json.dump(credentials, file)
    credential_path.chmod(0o600)
    app = Foundation(
        database, ROOT / "contracts/p3/profiles/foundation.yaml", engineering_profile=True,
        postgres_dsn=dsn,
    )
    try:
        principals = [
            Principal(
                principal_id=name,
                home_scope=Scope(
                    tenant_id="tenant_a" if name in {"alice", "bob"} else "tenant_b",
                    application_id="engineering",
                    user_id=name,
                    agent_id="agent_" + name,
                ),
                permissions=tuple(Permission),
                auth_epoch=1,
            )
            for name in credentials
        ]
        app.identity.provision(
            [(sha256(credentials[p.principal_id].encode()).hexdigest(), p) for p in principals]
        )

        async def execute():
            from aether_agent_memory.runtime.temporal.bridge import IntentBridge
            from aether_agent_memory.runtime.temporal.config import (
                TemporalConfiguration,
                deployment_configuration,
            )
            from aether_agent_memory.runtime.temporal.events import register_events
            from aether_agent_memory.runtime.temporal.gateway import TemporalGateway, connect_client
            from aether_agent_memory.runtime.temporal.ledger import ExecutionLedger
            from aether_agent_memory.runtime.temporal.registry import StageRegistry
            from aether_agent_memory.runtime.temporal.sample import register_sample
            from aether_agent_memory.runtime.temporal.worker import WorkerHost

            config = deployment_configuration(
                TemporalConfiguration(
                    deployment_id="demo-" + secrets.token_hex(12), endpoint=args.temporal_endpoint
                )
            )
            ledger = ExecutionLedger(app.tasks, config)
            registry = StageRegistry()
            register_sample(registry, app.sample)
            register_events(registry, ledger, app.events)
            app.tasks.on_admitted = lambda tx, task: ledger.bind_admitted(tx, task)
            context = app.identity.context(credentials["alice"], timeout_seconds=120)
            task = app.sample.submit(context, "first_sample", "公共底座工程验证。")
            client = await connect_client(config)
            bridge = IntentBridge(ledger, TemporalGateway(client, ledger))
            worker = WorkerHost(client, ledger, registry)
            try:
                await worker.start()
                async with asyncio.timeout(120):
                    while True:
                        await bridge.flush()
                        with app.uow.transaction() as tx:
                            pending = tx.active_task_rows() or tx.pending_delivery_rows()
                        if not pending:
                            break
                        await asyncio.sleep(0.05)
            finally:
                await worker.stop()
            return context, task

        context, task = asyncio.run(execute())
        evidence = app.diagnostics.task_evidence(context, task.task_id)
        evidence["trace"] = app.diagnostics.trace(context, context.trace_id, limit=500)
        (directory / "evidence.json").write_text(
            json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        success = evidence["task"]["state"] == "succeeded" and all(
            row["state"] == "acknowledged" for row in evidence["deliveries"]
        )
        print(
            json.dumps(
                {
                    "mode": "engineering_only",
                    "passed": success,
                    "task_id": task.task_id,
                    "trace_id": context.trace_id,
                    "database": str(database),
                    "credentials_file": str(credential_path),
                    "evidence": str(directory / "evidence.json"),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0 if success else 1
    finally:
        app.close()


if __name__ == "__main__":
    raise SystemExit(main())
