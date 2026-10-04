"""Real slow PostgreSQL log writes must leave transport heartbeats runnable."""

import asyncio
import time

import psycopg
from test_postgres_observability import dsns as dsns
from test_postgres_observability import open_host, people

from aether_agent_memory.runtime.foundation.telemetry import current_node, observed


async def test_observed_async_provider_keeps_heartbeat_running_during_pg_writes(tmp_path, dsns):
    host = open_host(tmp_path, dsns)
    people(host)
    ctx = host.identity.context("alice")
    with psycopg.connect(dsns["state"]) as db:
        db.execute(
            "CREATE FUNCTION slow_log() RETURNS trigger LANGUAGE plpgsql AS "
            "$$ BEGIN PERFORM pg_sleep(0.3); RETURN NEW; END $$"
        )
        db.execute(
            "CREATE TRIGGER slow_log BEFORE INSERT ON node_logs "
            "FOR EACH ROW EXECUTE FUNCTION slow_log()"
        )

    @observed("test")
    class Provider:
        _telemetry = host.telemetry

        async def parent(self, ctx):
            parent = current_node.get()
            assert parent is not None
            child = await self.child(ctx)
            assert current_node.get() is parent
            return child

        async def child(self, ctx):
            return current_node.get().parent_id

    ticks = []

    async def heartbeat():
        while True:
            ticks.append(time.monotonic())
            await asyncio.sleep(0.02)

    beat = asyncio.create_task(heartbeat())
    try:
        await asyncio.sleep(0.02)
        parent_id = await Provider().parent(ctx)
        await asyncio.sleep(0.02)
        page = await asyncio.to_thread(host.telemetry.page, ctx, ctx.trace_id)
        records = page["records"]
        assert len(records) == 4
        assert [r["phase"] for r in records] == ["started", "started", "returned", "returned"]
        assert records[0]["span_id"] == parent_id == records[1]["parent_span_id"]
        assert current_node.get() is None
        assert host.telemetry.dropped == 0
        longest_gap = max(b - a for a, b in zip(ticks, ticks[1:], strict=False))
        assert longest_gap < 0.15, f"Heartbeat blocked by log I/O for {longest_gap:.3f}s"
    finally:
        beat.cancel()
        await asyncio.gather(beat, return_exceptions=True)
        host.close()
