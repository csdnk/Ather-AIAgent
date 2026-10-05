"""Real BGE and PostgreSQL adapter must leave the event loop responsive."""

import asyncio
import os
import time
from pathlib import Path

import psycopg
import pytest
from test_postgres_observability import dsns as dsns
from test_postgres_observability import open_host, people

from aether_agent_memory.recall.contracts.models import EmbeddingRequest
from aether_agent_memory.recall.embedding.native import NativeEmbeddingSettings
from aether_agent_memory.recall.embedding.p3 import NativeP3Embedding


async def test_real_native_embedding_pg_attempts_do_not_block_heartbeats(tmp_path, dsns):
    model_path = os.environ.get("P3_NATIVE_MODEL_PATH")
    if not model_path and os.environ.get("P3_TEST_NATIVE_CONFIG"):
        import json
        model_path = json.loads(Path(os.environ["P3_TEST_NATIVE_CONFIG"]).read_text())["model_path"]
    if not model_path:
        pytest.skip("P3_NATIVE_MODEL_PATH requires the real BGE weights")
    host = open_host(tmp_path, dsns)
    people(host)
    embedding = NativeP3Embedding(
        host.uow,
        host.identity,
        NativeEmbeddingSettings(model_path=Path(model_path), cache_dir=tmp_path / "models"),
    )
    with psycopg.connect(dsns["state"]) as db:
        db.execute(
            "CREATE FUNCTION slow_attempt() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN "
            "IF NEW.namespace IN ('p3_rf_native_embedding_inputs',"
            "'p3_rf_native_embedding_attempts') THEN PERFORM pg_sleep(0.2); END IF; "
            "RETURN NEW; END $$"
        )
        db.execute(
            "CREATE TRIGGER slow_attempt BEFORE INSERT OR UPDATE ON capability_records "
            "FOR EACH ROW EXECUTE FUNCTION slow_attempt()"
        )
    ticks = []

    async def heartbeat():
        while True:
            ticks.append(time.monotonic())
            await asyncio.sleep(0.02)

    ctx = host.identity.context("alice", timeout_seconds=120)
    beat = asyncio.create_task(heartbeat())
    try:
        await asyncio.sleep(0.02)
        result = await embedding.embed(
            ctx,
            EmbeddingRequest(
                operation_id="real-native-heartbeat",
                usage="query",
                texts=("陈远喝无糖红茶",),
                model_space=embedding.model_space,
                deadline_at=ctx.deadline_at,
            ),
        )
        await asyncio.sleep(0.02)
        assert result.dimensions == 512 and len(result.items[0].vector) == 512
        with host.uow.transaction() as tx:
            assert tx.rows("native_embedding_attempts")[0][1]["state"] == "succeeded"
        longest_gap = max(b - a for a, b in zip(ticks, ticks[1:], strict=False))
        assert longest_gap < 0.15, f"Heartbeat blocked by attempt I/O for {longest_gap:.3f}s"
    finally:
        beat.cancel()
        await asyncio.gather(beat, return_exceptions=True)
        embedding.close()
        host.close()
