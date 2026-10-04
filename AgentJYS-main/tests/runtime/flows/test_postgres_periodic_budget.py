"""Slow real PostgreSQL periodic work must checkpoint before the Activity expires."""

import time

from test_postgres_observability import dsns as dsns
from test_postgres_observability import open_host

from aether_agent_memory.runtime.temporal.config import TemporalConfiguration
from aether_agent_memory.runtime.temporal.ledger import ExecutionLedger
from aether_agent_memory.runtime.temporal.models import PeriodicState
from aether_agent_memory.runtime.temporal.periodic import PeriodicActivities


async def test_slow_pg_periodic_batch_yields_and_resumes_committed_cursor(tmp_path, dsns):
    host = open_host(tmp_path, dsns)
    ledger = ExecutionLedger(
        host.tasks, TemporalConfiguration(deployment_id="budget", endpoint="unused")
    )
    with host.uow.transaction() as tx:
        for index in range(6):
            tx.write("budget", str(index), {"count": 0})
    runner = PeriodicActivities(ledger, None)
    # A smaller quantum makes the same production boundary economical to test.
    runner.max_batch_seconds = 0.5

    def commit(tx, key, tick, prepared):
        tx.raw.connection.execute("SELECT pg_sleep(0.2)")
        row = tx.read("budget", key)
        tx.write("budget", key, {"count": row["count"] + 1})

    runner.register("budget", commit)
    state = PeriodicState(deployment_id="budget", last_tick=1)
    try:
        started = time.monotonic()
        state = await runner.batch(state)
        assert time.monotonic() - started < 1.8
        assert state.cursor is not None
        while state.cursor is not None:
            state = await runner.batch(state)
        with host.uow.transaction() as tx:
            assert all(row["count"] == 1 for _, row in tx.rows("budget"))
        assert (await runner.batch(state)).cursor is None
    finally:
        host.close()
