"""Bounded transfer of durable RPC intents. This never runs business handlers."""

import asyncio
import time
from typing import Protocol

from temporalio.service import RPCError

from aether_agent_memory.runtime.contracts.models import ErrorCode
from aether_agent_memory.runtime.foundation.common import FoundationError
from aether_agent_memory.runtime.foundation.tasks import TERMINAL

from .controls import settle_control
from .ledger import ExecutionLedger
from .models import ControlIntent, StartIntent, WorkflowBinding


class Gateway(Protocol):
    async def start(self, intent: StartIntent) -> WorkflowBinding: ...
    async def send_control(self, intent: ControlIntent) -> None: ...


class IntentBridge:
    def __init__(self, ledger: ExecutionLedger, gateway: Gateway) -> None:
        self.ledger, self.gateway = ledger, gateway
        self._lock = asyncio.Lock()
        self._backoff: dict[str, tuple[int, float]] = {}

    async def flush(self, limit: int = 100) -> int:
        if not 1 <= limit <= 1000:
            raise ValueError("intent limit must be 1..1000")
        async with self._lock:
            return await self._flush(limit)

    async def _flush(self, limit: int) -> int:
        uow = self.ledger.tasks.uow
        with uow.transaction() as tx:
            starts = [
                ("start", key, row) for key, row in tx.pending_intent_rows("start", limit=limit)
            ]
            controls = [
                ("control", key, row) for key, row in tx.pending_intent_rows("control", limit=limit)
            ]
        count = 0
        for kind, key, row in (starts + controls)[:limit]:
            tries, next_try = self._backoff.get(key, (0, 0))
            if time.monotonic() < next_try:
                continue
            table = f"temporal_{kind}_intents"
            try:
                binding = None
                if kind == "start":
                    start = StartIntent.model_validate(row["intent"])
                    with uow.transaction() as tx:
                        _, task = self.ledger.tasks.load(tx, start.job.job_id)
                        terminal = task.state in TERMINAL
                    if not terminal:
                        binding = await self.gateway.start(start)
                        with uow.transaction() as tx:
                            stored = self.ledger.verify_job(tx, start.job)
                            if (
                                binding.namespace != stored["namespace"]
                                or binding.workflow_id != stored["workflow_id"]
                                or binding.input_hash != start.job.input_hash
                                or binding.plan_version != start.job.plan_version
                                or (
                                    stored.get("observed_run_id")
                                    and stored["observed_run_id"] != binding.current_run_id
                                )
                                or (
                                    stored["binding"]
                                    and stored["binding"] != binding.model_dump(mode="json")
                                )
                            ):
                                tx.abort(
                                    ErrorCode.IDEMPOTENCY_CONFLICT, "start acknowledgement differs"
                                )
                            tx.write(
                                "temporal_bindings",
                                start.job.job_id,
                                {**stored, "binding": binding.model_dump(mode="json")},
                            )
                else:
                    await self.gateway.send_control(ControlIntent.model_validate(row["intent"]))
                with uow.transaction() as tx:
                    tx.write(table, key, {**row, "state": "acknowledged"})
                    if kind == "control":
                        settle_control(tx, row)
                self._backoff.pop(key, None)
                count += 1
            except FoundationError as exc:
                with uow.transaction() as tx:
                    tx.write(
                        table, key, {**row, "state": "attention_required", "error_code": exc.code}
                    )
                    if kind == "control":
                        settle_control(tx, row, error=exc.code.value)
            except (RPCError, TimeoutError, ConnectionError):
                self._backoff[key] = (tries + 1, time.monotonic() + min(30, 2 ** min(tries, 5)))
                with uow.transaction() as tx:
                    tx.write(table, key, {**row, "error_code": "TEMPORAL_UNAVAILABLE"})
        return count
