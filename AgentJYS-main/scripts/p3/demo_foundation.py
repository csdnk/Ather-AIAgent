"""Run an isolated engineering example using the actual foundation services."""

from __future__ import annotations

import argparse
import asyncio
import json
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
    args = parser.parse_args()
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
    app = Foundation(database, ROOT / "contracts/p3/profiles/foundation.yaml")
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
        context = app.identity.context(credentials["alice"])
        task = app.sample.submit(
            context, "first_sample", "这是公共底座工程验证，不是正式记忆写入。"
        )
        asyncio.run(app.tasks.run_once("demo_worker", "engineering"))
        app.events.dispatch_once("demo_dispatcher")
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
