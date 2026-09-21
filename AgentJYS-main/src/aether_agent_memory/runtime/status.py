from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any


class RuntimeStatus(StrEnum):
    P3_NORMAL = "P3_NORMAL"
    P3_DEGRADED = "P3_DEGRADED"
    P3_PROTECT = "P3_PROTECT"
    P3_UNAVAILABLE = "P3_UNAVAILABLE"


class ComponentStatus(StrEnum):
    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    UNAVAILABLE = "UNAVAILABLE"
    BUSY = "BUSY"
    UNKNOWN = "UNKNOWN"


class RuntimeComponent(StrEnum):
    P3 = "P3"
    B1 = "B1"
    REDIS = "Redis"
    MILVUS = "Milvus"
    CELERY = "Celery"
    P2 = "P2"
    B3 = "B3"
    EXECUTOR = "Executor"


@dataclass(slots=True)
class ComponentHealth:
    component: RuntimeComponent
    status: ComponentStatus
    detail: str = ""
    latency_ms: float | None = None
    critical: bool = True
    checked_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "component": self.component.value,
            "status": self.status.value,
            "detail": self.detail,
            "latency_ms": self.latency_ms,
            "critical": self.critical,
            "checked_at": self.checked_at.isoformat(),
            "metadata": self.metadata,
        }


def aggregate_runtime_status(components: list[ComponentHealth]) -> RuntimeStatus:
    if any(
        item.critical and item.status == ComponentStatus.UNAVAILABLE
        for item in components
    ):
        return RuntimeStatus.P3_UNAVAILABLE
    if any(item.critical and item.status == ComponentStatus.BUSY for item in components):
        return RuntimeStatus.P3_PROTECT
    if any(
        item.status in {
            ComponentStatus.DEGRADED,
            ComponentStatus.UNAVAILABLE,
            ComponentStatus.BUSY,
            ComponentStatus.UNKNOWN,
        }
        for item in components
    ):
        return RuntimeStatus.P3_DEGRADED
    return RuntimeStatus.P3_NORMAL
