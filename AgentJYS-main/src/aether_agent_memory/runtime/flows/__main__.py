"""CLI for the basic two-business-entry host; Operate runs inside the worker."""

import argparse
import asyncio
import json
import os
import signal
import sys
from pathlib import Path

from pydantic import BaseModel

from aether_agent_memory.operate.contracts.models import ActionRecord
from aether_agent_memory.recall.contracts.models import RecallRequest
from aether_agent_memory.remember.contracts.models import (
    CorrectionRequest,
    DeleteRequest,
    LifecycleRequest,
    RememberRequest,
    SourceInput,
    TextInput,
)
from aether_agent_memory.runtime.contracts.models import Flow, Permission, RecordRef, ScopeSelector
from aether_agent_memory.runtime.foundation.common import FoundationError, now
from aether_agent_memory.runtime.foundation.requests import request_key

from .host import ThreeFlows


def main() -> int:
    parser = argparse.ArgumentParser(
        description="P3 basic local profile, Remember / Recall / internal Operate"
    )
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--cache-root", type=Path, required=True)
    parser.add_argument("--operation-id")
    parser.add_argument("--embedding-profile", choices=["native", "lexical"], default="native")
    parser.add_argument("--embedding-config", type=Path)
    parser.add_argument("--recall-config", type=Path)
    parser.add_argument("--log-db", type=Path)
    parser.add_argument("--log-retention-days", type=int, default=14)
    parser.add_argument("--log-max-records", type=int, default=200_000)
    sub = parser.add_subparsers(dest="command", required=True)
    remember = sub.add_parser("remember")
    remember.add_argument("--text-file", type=Path, required=True)
    remember.add_argument("--session-id")
    query = sub.add_parser("recall")
    query.add_argument("--query", required=True)
    query.add_argument(
        "--sources", choices=["auto", "working", "long_term", "both"], default="auto"
    )
    query.add_argument("--budget", type=int, default=1024)
    query.add_argument("--session-id")
    sub.add_parser("warmup")
    sub.add_parser("memory").add_argument("--memory-id", required=True)
    sub.add_parser("result").add_argument("--recall-id", required=True)
    sub.add_parser("task").add_argument("--task-id", required=True)
    change = sub.add_parser("correct")
    change.add_argument("--memory-id", required=True)
    change.add_argument("--version", type=int, required=True)
    change.add_argument("--text-file", type=Path, required=True)
    change.add_argument("--reason", required=True)
    delete = sub.add_parser("delete")
    delete.add_argument("--memory-id", required=True)
    delete.add_argument("--revision", type=int, required=True)
    delete.add_argument("--reason", required=True)
    lifecycle = sub.add_parser("lifecycle")
    lifecycle.add_argument("--memory-id", required=True)
    lifecycle.add_argument("--version", type=int, required=True)
    lifecycle.add_argument("--target", choices=["active", "archived"], required=True)
    lifecycle.add_argument("--reason", required=True)
    sub.add_parser("actions")
    sub.add_parser("health")
    trace = sub.add_parser("trace")
    trace.add_argument("--trace-id", required=True)
    trace.add_argument("--after", type=int, default=0)
    trace.add_argument("--limit", type=int, default=100)
    trace.add_argument("--jsonl", action="store_true")
    worker = sub.add_parser("worker")
    worker.add_argument("--drain", action="store_true")
    args = parser.parse_args()
    app = ThreeFlows(
        args.db,
        args.cache_root,
        embedding_profile=args.embedding_profile,
        embedding_config=args.embedding_config,
        recall_config=args.recall_config,
        log_path=args.log_db,
        log_retention_days=args.log_retention_days,
        log_max_records=args.log_max_records,
    )
    try:
        if args.command == "worker":

            async def work() -> None:
                if args.drain:
                    await app.drain()
                    return
                stop = asyncio.Event()
                loop = asyncio.get_running_loop()

                def halt(signum: int, frame: object) -> None:
                    loop.call_soon_threadsafe(stop.set)

                signal.signal(signal.SIGINT, halt)
                signal.signal(signal.SIGTERM, halt)
                while not stop.is_set():
                    await app.tick(periodic=True)
                    await asyncio.sleep(0.25)

            asyncio.run(work())
            return 0
        ctx = app.foundation.identity.context(
            os.environ.get("P3_API_KEY", ""), timeout_seconds=300, operation_id=args.operation_id
        )
        # Publish correlation before execution so failures are traceable too.
        # Preserve the existing business-result stdout format.
        print(json.dumps({"trace_id": ctx.trace_id, "request_id": ctx.request_id}), file=sys.stderr)
        if args.command == "warmup":
            app.foundation.diagnostics.authorize(ctx)

            async def warmup() -> None:
                if app.owned_reranker:
                    await app.owned_reranker.rerank(ctx, "健康检测", ("服务健康检测",))
                if app.owned_vectors:
                    await app.owned_vectors.prepare(ctx)

            asyncio.run(warmup())
            print(
                json.dumps(
                    {
                        "state": "ready",
                        "tokenizer": app.recall.tokenizer.identifier,
                        "rerank_policy": app.recall_settings.rerank_policy,
                    }
                )
            )
            return 0
        if args.command == "trace":
            page = app.foundation.diagnostics.trace(
                ctx, args.trace_id, after=args.after, limit=args.limit
            )
            if args.jsonl:
                for record in page["records"]:
                    print(json.dumps(record, ensure_ascii=False))
                print(json.dumps({"page": {k: v for k, v in page.items() if k != "records"}}))
            else:
                print(json.dumps(page, ensure_ascii=False, indent=2))
            return 0
        result: BaseModel

        def input_source(endpoint: str) -> SourceInput:
            with app.foundation.uow.transaction() as tx:
                previous = tx.read("remember_operations", request_key(ctx, endpoint))
                metadata = (
                    None
                    if previous is None
                    else tx.read("remember_source_input", previous["result"]["source"]["source_id"])
                )
            return (
                SourceInput.model_validate(metadata)
                if metadata
                else SourceInput(
                    kind="text",
                    external_id=ctx.operation_id,
                    external_version="1",
                    occurred_at=now(),
                )
            )

        if args.command == "remember":
            result = asyncio.run(
                app.remember.save(
                    ctx,
                    RememberRequest(
                        source=input_source("remember.save"),
                        selection=ScopeSelector(session_id=args.session_id),
                        content=TextInput(
                            kind="text", text=args.text_file.read_text(encoding="utf-8")
                        ),
                    ),
                )
            )
        elif args.command == "recall":
            result = asyncio.run(
                app.recall.recall(
                    ctx,
                    RecallRequest(
                        query=args.query,
                        sources=args.sources,
                        token_budget=args.budget,
                        selection=ScopeSelector(session_id=args.session_id),
                    ),
                )
            )
        elif args.command == "memory":
            result = app.remember.get(ctx, args.memory_id)
        elif args.command == "result":
            result = app.recall.result(ctx, args.recall_id)
        elif args.command == "correct":
            result = app.remember.correct(
                ctx,
                args.memory_id,
                CorrectionRequest(
                    expected_version=args.version,
                    content=args.text_file.read_text(encoding="utf-8"),
                    source=input_source("correct_" + args.memory_id),
                    reason=args.reason,
                ),
            )
        elif args.command == "delete":
            result = app.remember.delete(
                ctx,
                args.memory_id,
                DeleteRequest(expected_revision=args.revision, reason=args.reason),
            )
        elif args.command == "lifecycle":
            result = app.remember.lifecycle(
                ctx,
                args.memory_id,
                LifecycleRequest(
                    expected_version=args.version, target=args.target, reason=args.reason
                ),
            )
        elif args.command == "task":
            task = app.foundation.diagnostics.task(ctx, args.task_id)
            with app.foundation.uow.transaction() as tx:
                output = {
                    "task": task.model_dump(mode="json"),
                    "result": None if task.result_ref is None else tx.get(task.result_ref),
                }
            print(json.dumps(output, ensure_ascii=False, indent=2))
            return 0
        elif args.command == "actions":
            values = []
            with app.foundation.uow.transaction() as tx:
                for _, raw in tx.rows("operate_actions"):
                    action = ActionRecord.model_validate(raw)
                    ref = RecordRef(
                        owner=Flow.OPERATE,
                        object_type="action",
                        object_id=action.intent.action_id,
                        scope=action.intent.decision.memory.scope,
                    )
                    if app.foundation.identity.permits(tx, ctx, Permission.READ, ref):
                        values.append(action.model_dump(mode="json"))
            print(json.dumps(values, ensure_ascii=False, indent=2))
            return 0
        else:
            print(json.dumps(asyncio.run(app.health.report(ctx)), ensure_ascii=False, indent=2))
            return 0
        print(result.model_dump_json(indent=2))
        return 0
    except FoundationError as exc:
        print(json.dumps({"error_code": exc.code, "message": str(exc)}))
        return 2
    except (OSError, ValueError, TimeoutError):
        print(json.dumps({"error_code": "INVALID_INPUT_OR_DEPENDENCY_FAILURE"}))
        return 2
    finally:
        app.close()


if __name__ == "__main__":
    raise SystemExit(main())
