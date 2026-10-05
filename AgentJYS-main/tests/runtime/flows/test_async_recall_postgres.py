"""Recall planning must not starve heartbeats while using real PostgreSQL."""

import asyncio
import time

import psycopg
import pytest
from test_generation_candidates import example
from test_postgres_observability import dsns as dsns
from test_postgres_observability import people

from aether_agent_memory.recall.basic.assembly import ContextAssembly
from aether_agent_memory.recall.contracts.foundation import RecallPlanRequest
from aether_agent_memory.remember.basic.boundary import RememberBoundary
from aether_agent_memory.remember.contracts.foundation import CandidateQualificationTarget
from aether_agent_memory.runtime.contracts.models import ScopeSelector
from azure_test_runtime import ThreeFlows


@pytest.mark.parametrize("operation", ["readiness", "qualify"])
async def test_recall_remember_metadata_boundary_does_not_block_heartbeat(
    tmp_path, dsns, operation
):
    runtime = ThreeFlows(
        tmp_path / "unused.db",
        tmp_path / "cache",
        postgres_dsn=dsns["state"],
        embedding_profile="injected",
    )
    people(runtime.foundation)
    ctx = runtime.foundation.identity.context("alice", timeout_seconds=120)
    boundary = RememberBoundary(runtime.remember)
    ticks = []

    async def heartbeat():
        while True:
            ticks.append(time.monotonic())
            await asyncio.sleep(0.01)

    beat = asyncio.create_task(heartbeat())
    try:
        await asyncio.sleep(0.01)
        if operation == "readiness":
            result = await boundary.projection_readiness(ctx, ScopeSelector(), "long_term")
            assert result.complete and result.ready_count == 0
        else:
            target = CandidateQualificationTarget.model_validate(
                example("remember.CandidateQualificationTarget")
            )
            target = target.model_copy(
                update={
                    "memory": target.memory.model_copy(update={"scope": ctx.principal.home_scope})
                }
            )
            results = await boundary.qualify(ctx, (target,), "recall")
            assert len(results) == 1 and results[0].decision == "excluded"
        await asyncio.sleep(0.01)
        longest_gap = max(b - a for a, b in zip(ticks, ticks[1:], strict=False))
        assert longest_gap < 0.08, (
            f"Heartbeat blocked by B metadata {operation} for {longest_gap:.3f}s"
        )
    finally:
        beat.cancel()
        await asyncio.gather(beat, return_exceptions=True)
        runtime.close()


async def test_empty_recall_plan_pg_checks_and_persistence_leave_heartbeat_runnable(tmp_path, dsns):
    runtime = ThreeFlows(
        tmp_path / "unused.db",
        tmp_path / "cache",
        postgres_dsn=dsns["state"],
        embedding_profile="injected",
    )
    people(runtime.foundation)
    ctx = runtime.foundation.identity.context("alice", timeout_seconds=120)
    search = example("recall.MemorySearchRequest")
    search.update(
        query="没有保存的信息", selection={}, memory_source="long_term", deadline_at=ctx.deadline_at
    )
    request = RecallPlanRequest(
        recall_id="heartbeat-empty-plan",
        query=search["query"],
        selection={},
        sources=("long_term",),
        long_term_search=search,
        token_budget=1000,
        context_tokenizer=runtime.recall.tokenizer.identifier,
        policy_version=runtime.recall.policy_version,
        deadline_at=ctx.deadline_at,
    )
    assembly = ContextAssembly(runtime.recall, None, None, None)
    discovered = {
        "snapshots": {},
        "conflicts": {},
        "reasons": [],
        "excluded": [],
        "coverage": {"long_term": "complete", "working": "not_requested"},
        "source_ranks": {"long_term": {}},
        "manifests": {},
        "candidate_guards": {},
        "relation_guards": {},
    }
    with psycopg.connect(dsns["state"]) as db:
        db.execute(
            "CREATE FUNCTION slow_recall_log() RETURNS trigger LANGUAGE plpgsql AS "
            "$$ BEGIN PERFORM pg_sleep(0.2); RETURN NEW; END $$"
        )
        db.execute(
            "CREATE TRIGGER slow_recall_log BEFORE INSERT ON node_logs "
            "FOR EACH ROW EXECUTE FUNCTION slow_recall_log()"
        )
        db.execute(
            "CREATE FUNCTION slow_recall_plan() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN "
            "IF NEW.namespace='p3_rf_recall_assembly' THEN PERFORM pg_sleep(0.2); END IF; "
            "RETURN NEW; END $$"
        )
        db.execute(
            "CREATE TRIGGER slow_recall_plan BEFORE INSERT OR UPDATE ON capability_records "
            "FOR EACH ROW EXECUTE FUNCTION slow_recall_plan()"
        )
    ticks = []

    async def heartbeat():
        while True:
            ticks.append(time.monotonic())
            await asyncio.sleep(0.02)

    beat = asyncio.create_task(heartbeat())
    try:
        await asyncio.sleep(0.02)
        plan = await assembly.assemble_discovered(ctx, request, discovered)
        await asyncio.sleep(0.02)
        longest_gap = max(b - a for a, b in zip(ticks, ticks[1:], strict=False))
        assert plan.rendered_context == "" and plan.units == ()
        with runtime.foundation.uow.transaction() as tx:
            saved = tx.read("recall_assembly", "heartbeat-empty-plan")
            assert (
                saved["plan"]["rendered_context"] == ""
                and saved["coverage"]["long_term"] == "complete"
            )
        assert runtime.foundation.telemetry.dropped == 0
        assert longest_gap < 0.15, f"Heartbeat blocked by Recall PostgreSQL for {longest_gap:.3f}s"
    finally:
        beat.cancel()
        await asyncio.gather(beat, return_exceptions=True)
        runtime.close()
