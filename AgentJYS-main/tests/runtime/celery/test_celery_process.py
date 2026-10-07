"""Linux prefork + real Redis + PG, including worker death after an effect."""

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "temporal"))
from test_celery_execution import executor
from test_ledger import admit
from test_ledger import foundation as foundation
from test_ledger import ledger as ledger

from aether_agent_memory.runtime.celery.config import CeleryConfiguration
from aether_agent_memory.runtime.celery.dispatch import CeleryDispatcher


@pytest.mark.asyncio
@pytest.mark.parametrize("key", ["normal", "kill_after_write"])
async def test_real_worker_duplicate_and_recover_after_effect(
    foundation, ledger, owned_azure_resources, tmp_path, monkeypatch, key
):
    assert sys.platform == "linux", "production Celery certification requires Linux"
    monkeypatch.setenv("P3_CELERY_QUEUE", "test_" + uuid4().hex)

    async def unused(step):
        pytest.fail("API process must not execute business stage")

    engine = executor(ledger, unused)
    engine.config = CeleryConfiguration(repair_seconds=1, lease_grace_seconds=1)
    dispatcher = CeleryDispatcher(engine)
    job, _ = admit(foundation, ledger, key)
    # Admission has committed, but no publication occurred: dispatcher restart repairs it.
    environment = {
        **os.environ,
        "PYTHONPATH": os.pathsep.join(
            [str(Path(__file__).parent), "/workspace/src", "/workspace/tests"]
        ),
        "AETHER_CELERY_RUNTIME_FACTORY": "process_runtime:factory",
        "CELERY_TEST_MANIFEST": json.dumps(owned_azure_resources.manifest()),
        "CELERY_TEST_DATABASE": str(tmp_path / "business.db"),
    }
    log = (tmp_path / "worker.log").open("w")
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "celery",
            "-A",
            "aether_agent_memory.runtime.celery.app:app",
            "worker",
            "--pool=prefork",
            "--concurrency=2",
            "--loglevel=WARNING",
            "--without-gossip",
            "--without-mingle",
        ],
        env=environment,
        stdout=log,
        stderr=subprocess.STDOUT,
    )
    try:
        await dispatcher.flush()
        # Deliberately publish duplicate identical deliveries. Celery task_id is not dedup.
        with foundation.uow.transaction() as tx:
            intent = tx.read("celery_dispatch_intents", job.job_id)
        await asyncio.to_thread(dispatcher.publish, job.job_id, intent)
        async with asyncio.timeout(35):
            while True:
                await dispatcher.flush()
                with foundation.uow.transaction() as tx:
                    task = ledger.tasks.load(tx, job.job_id)[1]
                if task.state in {"succeeded", "attention_required", "failed"}:
                    break
                await asyncio.sleep(0.2)
        with foundation.uow.transaction() as tx:
            assert task.state == "succeeded"
            assert tx.read("celery_test_effects", job.job_id) == 1
            if key == "kill_after_write":
                assert tx.read("celery_test_queries", job.job_id) is True
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        log.close()
        engine.close()
    assert process.returncode in {0, -15}
