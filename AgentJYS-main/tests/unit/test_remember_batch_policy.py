"""Complete-message scheduling; byte thresholds do not depend on a tokenizer."""

import pytest

from aether_agent_memory.remember.basic.policy import RememberPolicy
from aether_agent_memory.remember.contracts.models import RememberRequest


def test_defaults_are_observation_and_one_hour_byte_batch():
    policy = RememberPolicy()
    assert RememberRequest.model_fields["trigger"].default == "observe"
    assert policy.compression_min_bytes == 8000
    assert policy.consolidation_bytes == 8000
    assert policy.consolidation_messages == 32
    assert policy.consolidation_seconds == 3600


@pytest.mark.parametrize("sizes,expected", [([3999, 4001, 2], 2), ([1] * 40, 32)])
def test_batch_preserves_whole_messages(sizes, expected):
    from aether_agent_memory.remember.basic.batching import select_batch

    rows = [(str(i), {"bytes": size}) for i, size in enumerate(sizes)]
    assert select_batch(rows, RememberPolicy()) == rows[:expected]


def test_short_batch_excludes_long_originals_and_keeps_messages_whole():
    from aether_agent_memory.remember.basic.batching import select_batch

    rows = [("long", {"bytes": 8000}), ("short-a", {"bytes": 3999}), ("short-b", {"bytes": 4001})]
    assert select_batch(rows, RememberPolicy()) == rows[1:]
