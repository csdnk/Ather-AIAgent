from __future__ import annotations

from datetime import UTC, datetime

from aether_agent_memory.runtime import RequestContext


def test_request_context_child_preserves_trace_and_request_id() -> None:
    context = RequestContext.from_values(
        request_id="request-1",
        trace_id="trace-1",
        tenant_id="tenant-1",
        user_id="user-1",
        agent_id="agent-1",
        session_id="session-1",
        idempotency_key="idem-1",
    )

    child = context.child(task_id="task-1")

    assert child.request_id == "request-1"
    assert child.trace_id == "trace-1"
    assert child.parent_request_id == "request-1"
    assert child.task_id == "task-1"
    assert child.scope.tenant_id == "tenant-1"
    assert child.idempotency_key == "idem-1"


def test_request_context_from_mapping_keeps_request_id_and_trace_id() -> None:
    deadline = datetime(2026, 8, 19, tzinfo=UTC)
    context = RequestContext.from_mapping(
        {
            "request_id": "request-2",
            "trace_id": "trace-2",
            "tenant_id": "tenant-2",
            "deadline": deadline.isoformat(),
        }
    )

    assert context.request_id == "request-2"
    assert context.trace_id == "trace-2"
    assert context.tenant_id == "tenant-2"
    assert context.deadline == deadline
