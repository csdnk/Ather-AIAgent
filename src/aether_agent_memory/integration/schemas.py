from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class RuntimeEnvelope(BaseModel):
    request_id: str
    trace_id: str
    data: dict[str, Any] = Field(default_factory=dict)
    degraded: bool = False
