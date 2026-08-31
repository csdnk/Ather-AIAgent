from __future__ import annotations

from datetime import UTC, datetime

from pydantic import BaseModel, Field

from aether_agent_memory.core.scope import Scope


class SkillRecord(BaseModel):
    """A versioned agent capability description, independent of execution."""

    skill_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    description: str = ""
    instructions: str = ""
    scope: Scope
    version: str = "1"
    enabled: bool = True
    source: str = "p3"
    revision: int = Field(default=1, ge=1)
    metadata: dict[str, str] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
