"""The retained reflection design must not schedule work from the public timer."""

from copy import deepcopy
from types import SimpleNamespace

import pytest

from aether_agent_memory.remember.basic.reflection import Reflection


@pytest.mark.parametrize(
    "status",
    [
        "enrolled",
        "waiting_evidence",
        "provider_unavailable",
        "scheduled",
        "running",
        "needs_recovery",
    ],
)
def test_periodic_keeps_reflection_as_prototype_without_reading_memory_or_models(status):
    row = {
        "status": status,
        "policy": {"enabled": True},
        "last_task_id": "existing-task",
        "last_inputs": ["old-input"],
        "reviewed": [],
    }

    class Tx:
        def read(self, table, key):
            assert table == "remember_reflection_policies" and key == "policy"
            return deepcopy(row)

        def write(self, table, key, value):
            assert table == "remember_reflection_policies" and key == "policy"
            row.update(value)

    reflection = Reflection(SimpleNamespace())
    assert reflection.periodic_item(Tx(), "policy") == 0
    assert row["status"] == (
        status if status in {"scheduled", "running", "needs_recovery"} else "prototype_only"
    )
    assert row["last_task_id"] == "existing-task"
    assert row["last_inputs"] == ["old-input"]
    assert row["reviewed"] == []
