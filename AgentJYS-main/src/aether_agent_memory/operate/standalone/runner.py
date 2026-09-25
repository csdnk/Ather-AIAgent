"""Small demo driver. Simulator progression belongs here, not in the controller."""

import math
from dataclasses import dataclass

from .controller import Controller
from .mock_p2 import MockP2


@dataclass
class ManualClock:
    value: float = 0.0

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        if not math.isfinite(seconds) or seconds < 0:
            raise ValueError("time cannot move backwards")
        self.value += seconds


async def settle(
    controller: Controller, p2: MockP2, clock: ManualClock, *, max_steps: int = 100
) -> None:
    """Advance pending operations/retry timers until currently requested work settles.

    Future cooling/audit timers are left to the caller; this function is bounded.
    """
    for _ in range(max_steps):
        await controller.tick()
        p2.advance()
        if controller.store.pending:
            clock.advance(controller.settings.retry_seconds)
        elif not controller.store.ready:
            return
    raise TimeoutError("demo did not settle; inspect pending actions and waiting reasons")
