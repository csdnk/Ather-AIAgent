"""Bounded transfer of durable RPC intents. This never runs business handlers."""

import asyncio
import time
from collections import deque
from typing import Any, Protocol

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
        self._next_kind = "control"
        self._next_recall = True

    def pop_start(self, queue: deque[tuple[str, str, Any]]) -> tuple[str, str, Any]:
        """Alternate recall and background attempts even across one-item flushes."""
        index = next(
            (
                i
                for i, item in enumerate(queue)
                if (item[2]["intent"]["job"].get("kind") == "recall.execute") == self._next_recall
            ),
            0,
        )
        item = queue[index]
        del queue[index]
        self._next_recall = item[2]["intent"]["job"].get("kind") != "recall.execute"
        return item

    async def flush(self, limit: int = 100) -> int:
        if not 1 <= limit <= 1000:
            raise ValueError("intent limit must be 1..1000")
        async with self._lock:
            return await self._flush(limit)

    async def _flush(self, limit: int) -> int:
        uow = self.ledger.tasks.uow

        def pending() -> tuple[list[tuple[str, str, Any]], list[tuple[str, str, Any]]]:
            with uow.transaction() as tx:
                return (
                    [
                        ("start", key, row)
                        for key, row in tx.pending_intent_rows("start", limit=max(2, limit))
                    ],
                    [
                        ("control", key, row)
                        for key, row in tx.pending_intent_rows("control", limit=limit)
                    ],
                )

        starts, controls = await asyncio.to_thread(pending)
        count = 0
        # A long document can atomically create hundreds of projection/signal
        # intents. Yield between RPCs so the coordinator refreshes readiness and
        # worker heartbeats while this durable backlog drains. Never cancel an
        # in-flight RPC merely to meet the quantum: its normal timeout and stable
        # intent identity still govern acknowledgement-loss recovery.
        stop_at = time.monotonic() + 2.0
        queues = {
            "start": deque(
                item for item in starts if self._backoff.get(item[1], (0, 0))[1] <= time.monotonic()
            ),
            "control": deque(
                item
                for item in controls
                if self._backoff.get(item[1], (0, 0))[1] <= time.monotonic()
            ),
        }
        for _ in range(limit):
            if time.monotonic() >= stop_at:
                break
            selected = self._next_kind
            if not queues[selected]:
                selected = "start" if selected == "control" else "control"
            if not queues[selected]:
                break
            kind, key, row = (
                self.pop_start(queues[selected])
                if selected == "start"
                else queues[selected].popleft()
            )
            # Alternate attempts, including failed RPCs, across flush calls.
            # Even limit=1 or one slow start cannot indefinitely delay controls.
            self._next_kind = "start" if kind == "control" else "control"
            tries, next_try = self._backoff.get(key, (0, 0))
            if time.monotonic() < next_try:
                continue
            table = f"temporal_{kind}_intents"
            try:
                binding = None
                if kind == "start":
                    start = StartIntent.model_validate(row["intent"])

                    def terminal_task(start: StartIntent) -> bool:
                        with uow.transaction() as tx:
                            _, task = self.ledger.tasks.load(tx, start.job.job_id)
                            terminal = task.state in TERMINAL
                        return terminal

                    terminal = await asyncio.to_thread(terminal_task, start)
                    if not terminal:
                        binding = await self.gateway.start(start)

                        def bind_start(binding: WorkflowBinding | None, start: StartIntent) -> None:
                            assert binding is not None
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
                                        ErrorCode.IDEMPOTENCY_CONFLICT,
                                        "start acknowledgement differs",
                                    )
                                tx.write(
                                    "temporal_bindings",
                                    start.job.job_id,
                                    {**stored, "binding": binding.model_dump(mode="json")},
                                )

                        await asyncio.to_thread(bind_start, binding, start)
                else:
                    await self.gateway.send_control(ControlIntent.model_validate(row["intent"]))

                def acknowledge(table: str, key: str, row: Any, kind: str) -> None:
                    with uow.transaction() as tx:
                        tx.write(table, key, {**row, "state": "acknowledged"})
                        if kind == "control":
                            settle_control(tx, row)

                await asyncio.to_thread(acknowledge, table, key, row, kind)
                self._backoff.pop(key, None)
                count += 1
            except FoundationError as exc:
                error_code = exc.code

                def attention(
                    table: str, key: str, row: Any, error_code: ErrorCode, kind: str
                ) -> None:
                    with uow.transaction() as tx:
                        tx.write(
                            table,
                            key,
                            {**row, "state": "attention_required", "error_code": error_code},
                        )
                        if kind == "control":
                            settle_control(tx, row, error=error_code.value)

                await asyncio.to_thread(attention, table, key, row, error_code, kind)
            except (RPCError, TimeoutError, ConnectionError):
                self._backoff[key] = (tries + 1, time.monotonic() + min(30, 2 ** min(tries, 5)))

                def unavailable(table: str, key: str, row: Any) -> None:
                    with uow.transaction() as tx:
                        tx.write(table, key, {**row, "error_code": "TEMPORAL_UNAVAILABLE"})

                await asyncio.to_thread(unavailable, table, key, row)
        return count
