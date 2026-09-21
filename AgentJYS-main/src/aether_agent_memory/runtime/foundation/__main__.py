"""Local maintenance CLI. No product routes or anonymous management server."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import secrets
import signal
from pathlib import Path

from aether_agent_memory.runtime.contracts.models import (
    AuthorizationGrant,
    Principal,
    RecoveryRequest,
)

from .common import FoundationError
from .host import Foundation


def main() -> int:
    parser = argparse.ArgumentParser(
        description="P3 single-host foundation; engineering sample only"
    )
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--profile", type=Path)
    sub = parser.add_subparsers(dest="command", required=True)
    init = sub.add_parser(
        "configure", help="trusted deployment operation; requires database file access"
    )
    init.add_argument("--identities", type=Path, required=True)
    submit = sub.add_parser("submit")
    submit.add_argument("--key", required=True)
    submit.add_argument("--text-file", type=Path, required=True)
    for command in ("status", "trace"):
        sub.add_parser(command).add_argument("--task-id", required=True)
    recover = sub.add_parser("recover")
    recover.add_argument("--task-id", required=True)
    recover.add_argument("--revision", type=int, required=True)
    recover.add_argument("--reason", required=True)
    recover.add_argument("--operation-id", required=True)
    worker = sub.add_parser("worker")
    worker.add_argument("--once", action="store_true")
    worker.add_argument("--worker-id", default="worker_" + secrets.token_hex(4))
    worker.add_argument("--execution-class", default="engineering")
    sub.add_parser("health")
    args = parser.parse_args()
    app = Foundation(args.db, args.profile)
    try:
        if args.command == "configure":
            data = json.loads(args.identities.read_text(encoding="utf-8"))
            app.identity.provision(
                [
                    (item["credential_sha256"], Principal.model_validate(item["principal"]))
                    for item in data["identities"]
                ],
                [AuthorizationGrant.model_validate(g) for g in data.get("grants", [])],
            )
            print(json.dumps({"configured": True, "mode": "engineering_only"}))
            return 0
        if args.command == "worker":

            async def run() -> None:
                stop = asyncio.Event()
                loop = asyncio.get_running_loop()

                def halt(signum: int, frame: object) -> None:
                    loop.call_soon_threadsafe(stop.set)

                signal.signal(signal.SIGINT, halt)
                signal.signal(signal.SIGTERM, halt)
                await app.worker(
                    args.worker_id, stop, once=args.once, execution_class=args.execution_class
                )

            asyncio.run(run())
            return 0
        ctx = app.identity.context(os.environ.get("P3_API_KEY", ""), timeout_seconds=300)
        if args.command == "submit":
            result = app.sample.submit(
                ctx, args.key, args.text_file.read_text(encoding="utf-8")
            ).model_dump(mode="json")
        elif args.command == "status":
            result = app.diagnostics.task(ctx, args.task_id).model_dump(mode="json")
        elif args.command == "trace":
            result = app.diagnostics.task_evidence(ctx, args.task_id)
        elif args.command == "recover":
            with app.uow.transaction() as tx:
                result = app.tasks.request_recovery(
                    tx,
                    ctx,
                    RecoveryRequest(
                        operation_id=args.operation_id,
                        task_id=args.task_id,
                        expected_revision=args.revision,
                        reason=args.reason,
                    ),
                ).model_dump(mode="json")
        else:
            result = app.diagnostics.health(ctx).model_dump(mode="json")
            result["foundation_database"] = "available"
            result["mode"] = "engineering_only"
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except FoundationError as exc:
        print(json.dumps({"error_code": exc.code, "message": str(exc)}))
        return 2
    except (ValueError, OSError):
        print(json.dumps({"error_code": "INVALID_CONFIGURATION_OR_INPUT"}))
        return 2
    finally:
        app.close()


if __name__ == "__main__":
    raise SystemExit(main())
