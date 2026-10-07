"""One persistent asyncio runner and independently constructed clients per process."""

import asyncio
import importlib
import os
from pathlib import Path
from typing import Any

from celery.signals import worker_process_shutdown

from .app import app

_runner: asyncio.Runner | None = None
_engine: Any = None
_service: Any = None


def engine() -> Any:
    global _runner, _engine, _service
    if _engine is None:
        _runner = asyncio.Runner()
        factory = os.environ.get("AETHER_CELERY_RUNTIME_FACTORY")
        if factory:
            module, name = factory.split(":", 1)
            _engine = getattr(importlib.import_module(module), name)()
        else:
            from aether_agent_memory.runtime.flows.application import Service
            from aether_agent_memory.runtime.flows.config import ServiceConfiguration

            config = ServiceConfiguration.load(Path(os.environ["AETHER_SERVICE_CONFIG"]))
            config = config.model_copy(
                update={"data_dir": config.data_dir / "workers" / str(os.getpid())}
            )
            _service = Service(config, worker_role=True)
            _engine = _service.execution.celery
    return _engine


@app.task(name="p3.celery.step", ignore_result=True)  # type: ignore[untyped-decorator]
def step(job_id: str, generation: int, input_hash: str, deployment_id: str) -> None:
    runtime = engine()
    assert _runner is not None
    _runner.run(runtime.run(job_id, generation, input_hash, deployment_id))


@app.task(name="p3.celery.tick", ignore_result=True)  # type: ignore[untyped-decorator]
def tick() -> None:
    from .dispatch import CeleryDispatcher

    runtime = engine()
    assert _runner is not None
    if _service is not None:
        _runner.run(_service.execution.periodic_tick())
    _runner.run(CeleryDispatcher(runtime, app).flush())


@worker_process_shutdown.connect  # type: ignore[untyped-decorator]
def shutdown(**kwargs: Any) -> None:
    if _runner is not None:
        if _service is not None:
            _runner.run(_service.close())
        elif _engine is not None:
            _engine.close()
        _runner.close()
