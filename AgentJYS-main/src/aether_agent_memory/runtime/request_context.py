from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from typing import Any

from aether_agent_memory.core.scope import Scope as Scope
from aether_agent_memory.runtime.ids import new_id


@dataclass(frozen=True, slots=True)
class RequestContext:
    request_id: str = field(default_factory=new_id)
    trace_id: str = field(default_factory=new_id)
    tenant_id: str | None = None
    user_id: str | None = None
    agent_id: str | None = None
    session_id: str | None = None
    deadline: datetime | None = None
    idempotency_key: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    task_id: str | None = None
    parent_request_id: str | None = None
    project_id: str | None = None

    @property
    def scope(self) -> Scope:
        return Scope(
            tenant_id=self.tenant_id,
            user_id=self.user_id,
            agent_id=self.agent_id,
            session_id=self.session_id,
            task_id=self.task_id,
            project_id=self.project_id,
        )

    @property
    def deadline_ms(self) -> int | None:
        if self.deadline is None:
            return None
        remaining = (self.deadline - datetime.now(UTC)).total_seconds() * 1000
        return max(int(remaining), 0)

    @property
    def expired(self) -> bool:
        return self.deadline is not None and datetime.now(UTC) >= self.deadline

    @classmethod
    def from_values(
        cls,
        *,
        request_id: str | None = None,
        trace_id: str | None = None,
        tenant_id: str | None = None,
        user_id: str | None = None,
        agent_id: str | None = None,
        session_id: str | None = None,
        task_id: str | None = None,
        deadline: datetime | None = None,
        deadline_ms: int | None = None,
        idempotency_key: str | None = None,
        parent_request_id: str | None = None,
        project_id: str | None = None,
    ) -> RequestContext:
        resolved_deadline = deadline
        if resolved_deadline is None and deadline_ms is not None:
            resolved_deadline = datetime.now(UTC) + timedelta(milliseconds=deadline_ms)
        return cls(
            request_id=request_id or new_id(),
            trace_id=trace_id or new_id(),
            tenant_id=tenant_id,
            user_id=user_id,
            agent_id=agent_id,
            session_id=session_id,
            task_id=task_id,
            deadline=resolved_deadline,
            idempotency_key=idempotency_key,
            parent_request_id=parent_request_id,
            project_id=project_id,
        )

    @classmethod
    def from_mapping(cls, payload: dict[str, Any]) -> RequestContext:
        raw_deadline = payload.get("deadline")
        deadline = raw_deadline if isinstance(raw_deadline, datetime) else None
        if deadline is None and isinstance(raw_deadline, str) and raw_deadline:
            deadline = datetime.fromisoformat(raw_deadline.replace("Z", "+00:00"))
        deadline_ms = payload.get("deadline_ms")
        return cls.from_values(
            request_id=_optional_str(payload.get("request_id")),
            trace_id=_optional_str(payload.get("trace_id")),
            tenant_id=_optional_str(payload.get("tenant_id")),
            user_id=_optional_str(payload.get("user_id")),
            agent_id=_optional_str(payload.get("agent_id")),
            session_id=_optional_str(payload.get("session_id")),
            task_id=_optional_str(payload.get("task_id")),
            deadline=deadline,
            deadline_ms=int(deadline_ms) if deadline_ms is not None else None,
            idempotency_key=_optional_str(payload.get("idempotency_key")),
            parent_request_id=_optional_str(payload.get("parent_request_id")),
            project_id=_optional_str(payload.get("project_id")),
        )

    def child(
        self,
        *,
        task_id: str | None = None,
        idempotency_key: str | None = None,
        deadline: datetime | None = None,
    ) -> RequestContext:
        return replace(
            self,
            task_id=self.task_id if task_id is None else task_id,
            idempotency_key=self.idempotency_key if idempotency_key is None else idempotency_key,
            deadline=self.deadline if deadline is None else deadline,
            parent_request_id=self.request_id,
        )

    def as_headers(self) -> dict[str, str]:
        headers = {
            "x-request-id": self.request_id,
            "x-trace-id": self.trace_id,
        }
        if self.idempotency_key:
            headers["idempotency-key"] = self.idempotency_key
        return headers

    def as_dict(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "trace_id": self.trace_id,
            "tenant_id": self.tenant_id,
            "user_id": self.user_id,
            "agent_id": self.agent_id,
            "session_id": self.session_id,
            "deadline": self.deadline.isoformat() if self.deadline else None,
            "deadline_ms": self.deadline_ms,
            "idempotency_key": self.idempotency_key,
            "created_at": self.created_at.isoformat(),
            "task_id": self.task_id,
            "parent_request_id": self.parent_request_id,
            "project_id": self.project_id,
        }


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
