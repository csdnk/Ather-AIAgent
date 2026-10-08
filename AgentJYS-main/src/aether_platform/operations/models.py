from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

Resource = Literal[
    "overview",
    "requests",
    "tasks",
    "memories",
    "incidents",
    "configuration",
    "backups",
    "usage",
    "resources",
    "support",
    "audit",
    "commands",
    "rules",
    "quotas",
]


class Command(BaseModel):
    model_config = ConfigDict(extra="forbid")
    command_id: str = Field(min_length=8, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")
    resource: Resource
    action: str = Field(min_length=1, max_length=48, pattern=r"^[a-z_]+$")
    target_id: str | None = Field(default=None, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")
    expected_version: int | str | None = None
    parameters: dict[str, Any] = Field(default_factory=dict)


def now() -> str:
    return datetime.now(UTC).isoformat()


def page[T](
    items: list[T], total: int | None = None, *, status: str = "ok", **extra: Any
) -> dict[str, Any]:
    return {
        "items": items,
        "total": len(items) if total is None else total,
        "observed_at": now(),
        "status": status,
        **extra,
    }


def public_request(row: dict[str, Any]) -> dict[str, Any]:
    """Explicit projection: never transport prompts, answers or arbitrary error text."""
    allowed = (
        "id",
        "conversation_id",
        "user_id",
        "tenant_id",
        "status",
        "phase",
        "created_at",
        "started_at",
        "first_token_at",
        "first_token_ms",
        "attempt",
    )
    result = {key: row[key] for key in allowed if key in row}
    evidence = row.get("memory_evidence") or {}
    result["memory_status"] = evidence.get("status") or "not_connected"
    result["has_error"] = bool(row.get("error"))
    for key in ("job_id", "operation_id", "memory_id", "recall_id"):
        value = evidence.get(key)
        if isinstance(value, str) and len(value) <= 128:
            result[key] = value
    return result
