"""Ready PostgreSQL writers must cover the actual parallel health probe burst."""

import asyncio

import psycopg
from test_postgres_observability import dsns as dsns
from test_postgres_observability import open_host, people

from aether_agent_memory.runtime.foundation.telemetry import observed


async def test_parallel_probe_logs_use_warm_pg_capacity_without_drops(tmp_path, dsns):
    host = open_host(tmp_path, dsns)
    people(host)
    ctx = host.identity.context("alice", timeout_seconds=120)
    with psycopg.connect(dsns["state"]) as db:
        db.execute(
            "CREATE FUNCTION slow_probe_log() RETURNS trigger LANGUAGE plpgsql AS "
            "$$ BEGIN PERFORM pg_sleep(0.08); RETURN NEW; END $$"
        )
        db.execute(
            "CREATE TRIGGER slow_probe_log BEFORE INSERT ON node_logs "
            "FOR EACH ROW EXECUTE FUNCTION slow_probe_log()"
        )

    @observed("health")
    class Probe:
        _telemetry = host.telemetry

        async def check_probe(self, ctx):
            await asyncio.sleep(0)

    try:
        await asyncio.gather(*(Probe().check_probe(ctx) for _ in range(12)))
        assert host.telemetry.dropped == 0, host.telemetry.last_error
        page = await asyncio.to_thread(host.telemetry.page, ctx, ctx.trace_id)
        assert len(page["records"]) == 24
    finally:
        host.close()
