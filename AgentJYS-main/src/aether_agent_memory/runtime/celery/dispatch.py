"""Bounded SQL outbox publication; lost messages are republished until claimed."""

import asyncio
from typing import Any

from aether_agent_memory.runtime.foundation.common import later


class CeleryDispatcher:
    def __init__(self, engine: Any, app: Any = None) -> None:
        self.engine = engine
        if app is None:
            from .app import create_app

            app = create_app(engine.config)
        self.app = app

    def due(self) -> Any:
        tasks = self.engine.tasks
        with tasks.uow.transaction() as tx:
            return tx.celery_due_rows(tasks.clock(), limit=self.engine.config.batch_size)

    def publish(self, key: str, intent: dict[str, Any]) -> None:
        # No metadata transaction spans broker I/O. Ambiguous ACK safely resends.
        self.app.send_task(
            "p3.celery.step",
            args=[
                intent["job_id"],
                intent["generation"],
                intent["input_hash"],
                intent["deployment_id"],
            ],
            task_id=f"{intent['job_id']}:{intent['generation']}",
            retry=False,
        )
        with self.engine.tasks.uow.transaction() as tx:
            current = tx.read("celery_dispatch_intents", key)
            if current == intent:
                tx.write(
                    "celery_dispatch_intents",
                    key,
                    {
                        **intent,
                        "due_at": later(
                            self.engine.tasks.clock(), self.engine.config.repair_seconds
                        ),
                    },
                )

    async def flush(self) -> int:
        rows = await asyncio.to_thread(self.due)
        for key, intent in rows:
            if await asyncio.to_thread(self.engine.expire, intent):
                continue
            await asyncio.to_thread(self.publish, key, intent)
        return len(rows)
