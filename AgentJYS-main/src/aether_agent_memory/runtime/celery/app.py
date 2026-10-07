"""Linux Celery entrypoint; application clients are constructed only after fork."""

import os

from celery import Celery

from .config import CeleryConfiguration


def create_app(config: CeleryConfiguration | None = None) -> Celery:
    if config is None and os.environ.get("AETHER_SERVICE_CONFIG"):
        from pathlib import Path

        from aether_agent_memory.runtime.flows.config import ServiceConfiguration
        from aether_agent_memory.runtime.foundation.common import fingerprint

        service = ServiceConfiguration.load(Path(os.environ["AETHER_SERVICE_CONFIG"]))
        config = service.celery.model_copy(
            update={
                "queue_prefix": service.celery.queue_prefix
                + "."
                + fingerprint(service.temporal.deployment_id)[:16]
            }
        )
    config = config or CeleryConfiguration()
    application = Celery(
        "p3-remember",
        broker=config.broker_url(),
        include=["aether_agent_memory.runtime.celery.worker"],
    )
    application.conf.update(
        accept_content=["json"],
        task_serializer="json",
        result_serializer="json",
        task_ignore_result=True,
        task_acks_late=True,
        task_reject_on_worker_lost=True,
        worker_prefetch_multiplier=1,
        task_time_limit=3660,
        broker_connection_retry_on_startup=True,
        broker_connection_timeout=5,
        broker_transport_options={
            "visibility_timeout": config.visibility_timeout,
            "socket_timeout": 5,
            "socket_connect_timeout": 5,
        },
        task_default_queue=os.environ.get("P3_CELERY_QUEUE", config.queue_prefix),
        beat_schedule={"remember-dispatch": {"task": "p3.celery.tick", "schedule": 2.0}},
        timezone="UTC",
    )
    return application


app = create_app()
