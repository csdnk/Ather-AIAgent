from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Scope:
    """Scoping identity shared by request context and memory records.

    Defined in the ``core`` package to avoid an import cycle between
    ``runtime.request_context`` and ``memory.models``.
    """

    tenant_id: str | None = None
    user_id: str | None = None
    agent_id: str | None = None
    session_id: str | None = None
    task_id: str | None = None

    def as_dict(self) -> dict[str, str | None]:
        return {
            "tenant_id": self.tenant_id,
            "user_id": self.user_id,
            "agent_id": self.agent_id,
            "session_id": self.session_id,
            "task_id": self.task_id,
        }
