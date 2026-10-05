"""Slow real dependencies get an explicit bounded deployment budget."""

import pytest
from pydantic import ValidationError

from aether_agent_memory.runtime.flows.config import ServiceConfiguration
from azure_configuration_support import settings


def test_health_budget_is_explicit_and_default_stays_short(tmp_path):
    default = ServiceConfiguration.model_validate(settings(tmp_path))
    assert default.health_probe_timeout_seconds == 2
    assert default.health_snapshot_ttl_seconds == 10
    configured = ServiceConfiguration.model_validate(
        {
            **settings(tmp_path),
            "health_probe_timeout_seconds": 60,
            "health_snapshot_ttl_seconds": 60,
        }
    )
    assert configured.health_probe_timeout_seconds == 60
    assert configured.health_snapshot_ttl_seconds == 60


@pytest.mark.parametrize(
    "field,value",
    [
        ("health_probe_timeout_seconds", 0),
        ("health_probe_timeout_seconds", 61),
        ("health_probe_timeout_seconds", float("nan")),
        ("health_snapshot_ttl_seconds", 61),
    ],
)
def test_invalid_health_budget_is_rejected(tmp_path, field, value):
    with pytest.raises(ValidationError):
        ServiceConfiguration.model_validate({**settings(tmp_path), field: value})
