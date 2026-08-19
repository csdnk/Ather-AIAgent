from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from aether_agent_memory.runtime.request_context import RequestContext


def request_context_from_payload(payload: Mapping[str, Any] | object) -> RequestContext:
    if isinstance(payload, Mapping):
        data = dict(payload)
    else:
        dump = getattr(payload, "model_dump", None)
        data = dump() if callable(dump) else dict(vars(payload))
    return RequestContext.from_mapping(data)
