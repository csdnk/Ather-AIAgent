"""Authorized bounded dependency probes and scoped runtime detection.

Detection reports evidence only; it never restarts processes or retries actions.
"""

from __future__ import annotations

import asyncio
import sqlite3
import time
from collections.abc import Awaitable, Callable
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from aether_agent_memory.runtime.contracts.models import Permission, RecordRef, TrustedContext
from aether_agent_memory.runtime.foundation.common import now
from aether_agent_memory.runtime.foundation.telemetry import observed

if TYPE_CHECKING:
    from .host import ThreeFlows

Probe = Callable[[TrustedContext], Awaitable[dict[str, Any]]]


def sqlite_probe(path: Path, *, write: bool = False) -> dict[str, Any]:
    """Open existing database only; never create a missing dependency during a probe."""
    db = sqlite3.connect(path.resolve().as_uri() + "?mode=rw", uri=True, timeout=0.25)
    try:
        db.execute("SELECT name FROM sqlite_master LIMIT 1").fetchone()
        if write:
            db.execute("BEGIN IMMEDIATE")
            db.execute("CREATE TABLE IF NOT EXISTS __p3_probe(value INTEGER)")
            db.execute("INSERT INTO __p3_probe VALUES (1)")
            assert db.execute("SELECT value FROM __p3_probe LIMIT 1").fetchone() == (1,)
            db.rollback()
        return {"state": "available", "reason": "write_rollback" if write else "read_ok"}
    finally:
        db.close()


@observed("health")
class Health:
    def __init__(self, app: ThreeFlows) -> None:
        self.app = app
        self.uow = app.foundation.uow
        self.probes: dict[str, Probe] = {}
        self.required_dependencies: tuple[str, ...] = ("database", "logs")

    def register(self, name: str, probe: Probe, *, replace: bool = False) -> None:
        if name in self.probes and not replace:
            raise ValueError("duplicate health probe")
        self.probes[name] = probe

    def authorize(self, ctx: TrustedContext) -> None:
        self.app.foundation.diagnostics.authorize(ctx)

    @staticmethod
    def liveness() -> dict[str, Any]:
        return {"state": "alive", "checked_at": now(), "meaning": "process_can_respond"}

    async def check_probe(
        self, ctx: TrustedContext, name: str, probe: Probe, timeout: float
    ) -> dict[str, Any]:
        start = time.monotonic()
        try:
            result = await asyncio.wait_for(probe(ctx), timeout)
            if result.get("state") not in {
                "available",
                "unavailable",
                "degraded",
                "unknown",
                "disabled",
            }:
                raise ValueError("invalid probe result")
            # Provider messages may contain secrets; keep only a controlled code.
            result = {"state": result["state"], "reason": "probe_" + result["state"]}
        except Exception as exc:
            result = {
                "state": "unavailable",
                "reason": "timeout" if isinstance(exc, TimeoutError) else type(exc).__name__,
            }
        return {
            "dependency": name,
            **result,
            "checked_at": now(),
            "elapsed_ms": round((time.monotonic() - start) * 1000, 3),
        }

    async def report(self, ctx: TrustedContext, timeout_seconds: float = 2) -> dict[str, Any]:
        if not 0 < timeout_seconds <= 10:
            raise ValueError("probe timeout must be in (0,10]")
        self.authorize(ctx)
        with self.uow.transaction() as tx:
            configuration = tx.read("runtime_configuration", "active")
            config_version = (
                configuration["version"]
                if configuration
                else self.app.foundation.monitoring.config_version
            )
        checks = await asyncio.gather(
            *(
                self.check_probe(ctx, name, probe, timeout_seconds)
                for name, probe in self.probes.items()
            )
        )
        dependencies = {row["dependency"]: row for row in checks}
        log = self.app.foundation.telemetry
        if log.dropped:
            dependencies["logs"]["state"] = "degraded"
            dependencies["logs"]["dropped_records_this_process"] = log.dropped
        runtime = (
            self.runtime(ctx)
            if dependencies["database"]["state"] == "available"
            else {"state": "unknown", "reason": "database_unavailable"}
        )

        def available(key: str) -> bool:
            return bool(dependencies[key]["state"] == "available")

        capabilities = {
            "save": "available" if available("database") else "unavailable",
            "working_read": "available" if available("database") else "unavailable",
            "long_term": "available"
            if all(available(k) for k in ("database", "embedding", "vectors"))
            else "degraded",
            "extraction": dependencies["extraction"]["state"],
            "scheduling": "available"
            if available("executor") and runtime.get("worker_state") == "available"
            else "degraded",
        }
        capabilities["context_budget"] = dependencies["tokenizer"]["state"]
        capabilities["rerank"] = dependencies["reranker"]["state"]
        capabilities["deployment_dependencies"] = (
            "available" if all(available(k) for k in self.required_dependencies) else "unavailable"
        )
        if self.app.recall_settings.rerank_policy == "required" and not available("reranker"):
            capabilities["long_term"] = "degraded"
            capabilities["working_read"] = "degraded"
        self.authorize(ctx)
        report = {
            "checked_at": now(),
            "config_version": config_version,
            "profile": "local_" + self.app.embedding_profile,
            "model_space": self.app.model_space,
            "liveness": self.liveness(),
            "readiness": "ready"
            if all(available(k) for k in self.required_dependencies)
            else "not_ready",
            "capabilities": capabilities,
            "dependencies": dependencies,
            "runtime": runtime,
            "production_acceptance": False,
            "interpretation": "request readiness only; inspect capability and worker states",
        }
        snapshot = self.app.foundation.monitoring.publish(ctx, report)
        report["contract"] = snapshot.model_dump(mode="json")
        return report

    def runtime(self, ctx: TrustedContext) -> dict[str, Any]:
        self.authorize(ctx)
        clock = datetime.fromisoformat(now())

        def age(value: str) -> float:
            return max(0, (clock - datetime.fromisoformat(value)).total_seconds())

        with self.uow.transaction() as tx:
            self.app.foundation.identity.revalidate(tx, ctx)

            def permitted(raw: dict[str, Any]) -> bool:
                return self.app.foundation.identity.permits(
                    tx, ctx, Permission.DIAGNOSE, RecordRef.model_validate(raw)
                )

            tasks = [
                r for _, r in tx.active_task_rows(include_attention=True)
                if permitted(r["record"]["subject"])
            ]
            pending = [
                r
                for r in tasks
                if r["record"]["state"] in {"pending", "running", "retry_wait", "recovery_wait"}
            ]
            expired = [
                r["record"]["task_id"]
                for r in pending
                if r["record"].get("lease") and age(r["record"]["lease"]["until"]) > 0
            ]
            deliveries = []
            for _, delivery in tx.pending_delivery_rows(include_attention=True):
                event = tx.read("outbox", delivery["event_id"])
                if event and permitted(event["event"]["subject"]):
                    deliveries.append(delivery)
            workers = [
                {
                    "worker_id": key,
                    "state": r["state"],
                    "execution_class": r.get("execution_class", "unknown"),
                    "last_seen": r["last_seen"],
                    "age_seconds": round(age(r["last_seen"]), 3),
                }
                for key, r in tx.rows("workers")
            ]
            active = [
                r
                for r in workers
                if r["state"] != "stopped"
                and r["age_seconds"] <= max(10, self.app.foundation.tasks.lease_seconds * 2)
            ]
            # Worker identifiers are shared infrastructure; no task IDs or tenant
            # details from other users are returned in this view.
            actions = []
            for key, raw in tx.rows("operate_actions"):
                ref = {
                    "owner": "operate",
                    "object_type": "action",
                    "object_id": key,
                    "scope": raw["intent"]["decision"]["memory"]["scope"],
                }
                if raw["state"] == "unknown" and permitted(ref):
                    actions.append(key)
            return {
                "state": "attention_required" if expired or actions else "observed",
                "worker_state": "available"
                if {"remember", "operate"}.issubset({r["execution_class"] for r in active})
                else "unavailable",
                "workers": workers,
                "pending_tasks": len(pending),
                "oldest_task_age_seconds": max((age(r["created_at"]) for r in pending), default=0),
                "expired_lease_task_ids": expired,
                "unacknowledged_deliveries": len(deliveries),
                "attention_task_ids": [
                    r["record"]["task_id"]
                    for r in tasks
                    if r["record"]["state"] == "attention_required"
                ],
                "unknown_action_ids": actions,
                "recovery_policy": "domain_handler_only",
            }
