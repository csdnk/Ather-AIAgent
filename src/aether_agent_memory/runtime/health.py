from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from aether_agent_memory.runtime.status import (
    ComponentHealth,
    ComponentStatus,
    RuntimeComponent,
    RuntimeStatus,
    aggregate_runtime_status,
)


@dataclass(slots=True)
class RuntimeHealth:
    runtime_profile: str
    components: list[ComponentHealth] = field(default_factory=list)
    checked_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    @property
    def overall(self) -> RuntimeStatus:
        return aggregate_runtime_status(self.components)

    @property
    def live(self) -> bool:
        return any(
            item.component == RuntimeComponent.P3
            and item.status != ComponentStatus.UNAVAILABLE
            for item in self.components
        )

    @property
    def ready(self) -> bool:
        return self.overall in {RuntimeStatus.P3_NORMAL, RuntimeStatus.P3_DEGRADED}

    def to_dict(self) -> dict[str, Any]:
        return {
            "runtime_profile": self.runtime_profile,
            "overall": self.overall.value,
            "live": self.live,
            "ready": self.ready,
            "checked_at": self.checked_at.isoformat(),
            "components": [item.to_dict() for item in self.components],
        }
