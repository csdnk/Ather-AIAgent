"""Actual production routing and HTTP receipts with Temporal unavailable."""

import json
import os
import subprocess
import sys
from pathlib import Path

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parents[1] / "temporal"))
from process_service import HybridService
from test_temporal_http import configuration, headers, request, wait_ready, wait_result


def test_http_remember_real_celery_without_temporal(tmp_path, owned_azure_resources):
    config = configuration(tmp_path, "127.0.0.1:1")
    service = HybridService(config)
    environment = {
        **os.environ,
        "PYTHONPATH": os.pathsep.join(
            [str(Path(__file__).parent), "/workspace/src", "/workspace/tests"]
        ),
        "AETHER_CELERY_RUNTIME_FACTORY": "process_service:factory",
        "CELERY_TEST_CONFIGURATION": config.model_dump_json(),
        "CELERY_TEST_MANIFEST": json.dumps(owned_azure_resources.manifest()),
        "P3_CELERY_QUEUE": service.execution.dispatcher.app.conf.task_default_queue,
    }
    with (tmp_path / "worker.log").open("w") as log:
        worker = subprocess.Popen(
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
            with TestClient(service.app()) as client:
                wait_ready(client, service)
                response = client.post("/p3/remember", json=request(), headers=headers())
                location = response.headers["location"]
                result = wait_result(client, location) if response.status_code == 400 else response
                assert result.status_code == 200, result.text
                job_id = response.headers["x-p3-job-id"]
                with service.runtime.foundation.uow.transaction() as tx:
                    binding = tx.read("temporal_bindings", job_id)
                    assert binding["backend"] == "celery"
                    assert binding["workflow_id"].startswith("celery/")
                    assert not tx.pending_intent_rows("start")
                repeated = client.post("/p3/remember", json=request(), headers=headers())
                assert repeated.headers["x-p3-job-id"] == job_id
                assert repeated.json() == result.json()
        finally:
            worker.terminate()
            try:
                worker.wait(timeout=10)
            except subprocess.TimeoutExpired:
                worker.kill()
                worker.wait(timeout=5)
