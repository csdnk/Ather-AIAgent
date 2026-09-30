"""Durable periodic timer. Every item and its cursor commit in P3, outside history."""

import asyncio
from contextlib import suppress
from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError

with workflow.unsafe.imports_passed_through():
    from .models import ControlIntent, PeriodicState


@workflow.defn
class P3PeriodicWorkflow:
    def __init__(self) -> None:
        self.snapshot: PeriodicState | None = None
        self.controls: set[str] = set()
        self.cancel_requested = False
        self.reconcile_requested = False

    @workflow.signal
    def control(self, intent: ControlIntent) -> None:
        if intent.control_id in self.controls:
            return
        self.controls.add(intent.control_id)
        if intent.action == "cancel":
            self.cancel_requested = True
        else:
            self.reconcile_requested = True

    @workflow.query
    def state(self) -> dict[str, object]:
        return {
            "acknowledged_controls": sorted(self.controls),
            "last_tick": self.snapshot.last_tick if self.snapshot else -1,
            "cursor": self.snapshot.cursor if self.snapshot else None,
            "cancel_requested": self.cancel_requested,
        }

    @workflow.run
    async def run(self, state: PeriodicState) -> None:
        self.controls.update(state.acknowledged_controls)
        self.snapshot = state
        ticks = 0
        while not self.cancel_requested:
            self.reconcile_requested = False
            if workflow.info().is_continue_as_new_suggested():
                await workflow.wait_condition(workflow.all_handlers_finished)
                workflow.continue_as_new(
                    state.model_copy(update={"acknowledged_controls": tuple(sorted(self.controls))})
                )
            if state.cursor is None:
                # Missed inspection ticks coalesce; all durable due items remain eligible.
                tick = max(
                    state.last_tick + 1, int(workflow.now().timestamp() / state.interval_seconds)
                )
                state = state.model_copy(update={"last_tick": tick, "cursor": ""})
            try:
                state = await workflow.execute_activity(
                    "p3.periodic_batch",
                    state,
                    result_type=PeriodicState,
                    start_to_close_timeout=timedelta(seconds=30),
                    retry_policy=RetryPolicy(maximum_attempts=3),
                )
            except ActivityError:
                await workflow.sleep(state.interval_seconds)
                continue
            except asyncio.CancelledError:
                self.cancel_requested = True
                break
            state = state.model_copy(update={"acknowledged_controls": tuple(sorted(self.controls))})
            self.snapshot = state
            if state.cursor is not None:
                continue
            ticks += 1
            if not self.cancel_requested:
                wake_on_control = workflow.patched("p3-periodic-control-wake-v1")

                def control_requested(wake: bool = wake_on_control) -> bool:
                    return self.cancel_requested or (wake and self.reconcile_requested)

                with suppress(TimeoutError):
                    await workflow.wait_condition(
                        control_requested,
                        timeout=state.interval_seconds,
                    )
            if self.cancel_requested:
                break
            if ticks >= state.continue_after or workflow.info().is_continue_as_new_suggested():
                await workflow.wait_condition(workflow.all_handlers_finished)
                state = state.model_copy(
                    update={"acknowledged_controls": tuple(sorted(self.controls))}
                )
                workflow.continue_as_new(state)
