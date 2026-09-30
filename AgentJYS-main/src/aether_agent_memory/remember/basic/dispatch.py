"""Optional legacy wake-up transport; Temporal owns P3 execution."""

import asyncio
import secrets
from typing import Any

from aether_agent_memory.runtime.foundation.common import later


class CeleryWakeups:
    def __init__(self, celery: Any, queue: str = "remember") -> None:
        self.celery, self.queue = celery, queue

    async def dispatch(self, runtime: Any, limit: int = 100) -> int:
        now = runtime.identity.clock()
        with runtime.uow.transaction() as tx:
            pending = [
                (key, row)
                for key, row in tx.rows("remember_outbox")
                if row["next_attempt_at"] <= now
            ][:limit]
        sent = 0
        for key, row in pending:
            with runtime.uow.transaction() as tx:
                task = tx.read("tasks", row["task_id"])
                terminal = task is None or task["record"]["state"] in {
                    "succeeded",
                    "failed",
                    "cancelled",
                    "attention_required",
                }
                if terminal:
                    tx.write(
                        "remember_outbox",
                        key,
                        {**row, "state": "completed", "next_attempt_at": "9999-01-01T00:00:00Z"},
                    )
                    continue
            state = "broker_unavailable"
            try:
                await asyncio.to_thread(
                    self.celery.send_task,
                    "p3.remember.wakeup",
                    args=[row["task_id"]],
                    task_id=secrets.token_hex(16),
                    queue=self.queue,
                )
                state, sent = "broker_accepted", sent + 1
            except Exception:
                pass
            with runtime.uow.transaction() as tx:
                tx.write(
                    "remember_outbox",
                    key,
                    {
                        **row,
                        "state": state,
                        "attempts": row["attempts"] + 1,
                        "next_attempt_at": later(now, 30),
                    },
                )
        return sent


def register_worker(celery: Any, runtime_factory: Any) -> Any:
    """Register an optional wake-up adapter for a deployment-bound Temporal service.

    Duplicate/delayed messages only forward committed Temporal start intents. No body,
    credential or serialized TrustedContext is transported through the broker.
    """

    def wakeup(task_id: str) -> bool:
        async def execute() -> bool:
            runtime = runtime_factory()
            try:
                with runtime.foundation.uow.transaction() as tx:
                    row = tx.read("tasks", task_id)
                    if row is None or row["class"] != "remember":
                        return False
                from aether_agent_memory.runtime.temporal.bridge import IntentBridge
                from aether_agent_memory.runtime.temporal.gateway import (
                    TemporalGateway,
                    connect_client,
                )

                execution = getattr(runtime, "execution", None)
                if execution is None:
                    raise RuntimeError("P3 wakeup requires the configured Temporal service")
                client = await connect_client(execution.config.temporal)
                await IntentBridge(
                    execution.ledger, TemporalGateway(client, execution.ledger)
                ).flush()
                return True

            finally:
                if hasattr(runtime, "aclose"):
                    await runtime.aclose()
                else:
                    runtime.close()

        return asyncio.run(execute())

    return celery.task(name="p3.remember.wakeup", acks_late=True, reject_on_worker_lost=True)(
        wakeup
    )
