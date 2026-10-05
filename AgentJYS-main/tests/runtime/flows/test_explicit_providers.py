"""A missing deployment provider must fail instead of creating local state."""

import pytest

from aether_agent_memory.remember.local import RememberFactory
from aether_agent_memory.runtime.foundation.host import Foundation


def test_foundation_requires_explicit_metadata_provider(tmp_path):
    with pytest.raises(ValueError, match="explicit.*metadata|PostgreSQL"):
        Foundation(tmp_path / "state-anchor")
    assert not list(tmp_path.iterdir())


def test_remember_requires_explicit_object_provider(tmp_path):
    with pytest.raises(ValueError, match="explicit.*object|object provider"):
        RememberFactory(tmp_path / "state-anchor")
    assert not list(tmp_path.iterdir())


def test_remember_rejects_retired_redis_argument_without_creating_state(tmp_path):
    with pytest.raises(ValueError, match="body_cache"):
        RememberFactory(tmp_path / "state-anchor", p2=object(), redis=object())
    assert not list(tmp_path.iterdir())
