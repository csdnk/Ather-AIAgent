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


@pytest.mark.parametrize("sizes,expected", [([3999, 4001, 2], 2), ([9000, 1], 1), ([1] * 40, 32)])
def test_batch_preserves_whole_messages(sizes, expected):
    from aether_agent_memory.remember.basic.batching import select_batch

    rows = [(str(i), {"bytes": size}) for i, size in enumerate(sizes)]
    assert select_batch(rows, RememberPolicy()) == rows[:expected]


def test_source_descriptor_never_contains_excerpt_or_summary_promise():
    from aether_agent_memory.remember.basic.batching import source_descriptor

    descriptor = source_descriptor("source-1", 1, 8000)
    assert "source-1@1" in descriptor
    assert "8000" in descriptor
    assert "摘要" not in descriptor
