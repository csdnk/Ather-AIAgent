from __future__ import annotations

from aether_agent_memory.runtime import RuntimeHealth
from aether_agent_memory.runtime.status import (
    ComponentHealth,
    ComponentStatus,
    RuntimeComponent,
    RuntimeStatus,
)


def test_runtime_health_aggregates_noncritical_degradation() -> None:
    health = RuntimeHealth(
        runtime_profile="local",
        components=[
            ComponentHealth(RuntimeComponent.P3, ComponentStatus.HEALTHY),
            ComponentHealth(
                RuntimeComponent.B3,
                ComponentStatus.UNAVAILABLE,
                critical=False,
            ),
        ],
    )

    assert health.overall == RuntimeStatus.P3_DEGRADED
    assert health.ready is True
    assert health.to_dict()["runtime_profile"] == "local"


def test_runtime_health_critical_unavailable_is_not_ready() -> None:
    health = RuntimeHealth(
        runtime_profile="production",
        components=[
            ComponentHealth(RuntimeComponent.P3, ComponentStatus.HEALTHY),
            ComponentHealth(RuntimeComponent.REDIS, ComponentStatus.UNAVAILABLE),
        ],
    )

    assert health.overall == RuntimeStatus.P3_UNAVAILABLE
    assert health.ready is False
