"""Remember uses its existing source reads and automatic Working pipeline."""

import pytest
from pydantic import ValidationError

from aether_agent_memory.remember.contracts.models import RememberRequest


def test_remember_does_not_expose_reference_only_mode():
    assert "memory_mode" not in RememberRequest.model_fields


def test_reference_only_is_not_silently_accepted():
    payload = {
        "source": {
            "kind": "text",
            "external_id": "document-1",
            "external_version": "1",
            "occurred_at": "2026-10-08T00:00:00.000Z",
        },
        "selection": {"session_id": "session-1"},
        "content": {"kind": "text", "text": "original memory"},
    }
    RememberRequest.model_validate(payload)
    with pytest.raises(ValidationError, match="memory_mode"):
        RememberRequest.model_validate({**payload, "memory_mode": "reference_only"})
