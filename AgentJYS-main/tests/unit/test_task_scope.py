"""Task ownership scope tests (get_task must not cross tenant/user/agent)."""

from __future__ import annotations

import pytest

from aether_agent_memory.adapters.celery import _assert_task_scope
from aether_agent_memory.application.services import GetTaskStatusUseCase
from aether_agent_memory.runtime.dependencies import RuntimeDependencies
from aether_agent_memory.runtime.errors import ScopeError
from aether_agent_memory.runtime.request_context import RequestContext

_RECORD = {"tenant_id": "t1", "user_id": "u1", "agent_id": "a1"}


def test_task_scope_matching_passes() -> None:
    ctx = RequestContext.from_values(tenant_id="t1", user_id="u1", agent_id="a1")
    _assert_task_scope(_RECORD, ctx)  # must not raise


def test_task_scope_mismatch_raises() -> None:
    ctx = RequestContext.from_values(tenant_id="t2", user_id="u1", agent_id="a1")
    with pytest.raises(ScopeError, match="tenant"):
        _assert_task_scope(_RECORD, ctx)


def test_task_scope_mismatch_agent_raises() -> None:
    ctx = RequestContext.from_values(tenant_id="t1", user_id="u1", agent_id="a2")
    with pytest.raises(ScopeError, match="agent"):
        _assert_task_scope(_RECORD, ctx)


def test_task_scope_without_caller_scope_passes() -> None:
    ctx = RequestContext.from_values()
    _assert_task_scope(_RECORD, ctx)  # internal/debug read is allowed


@pytest.mark.asyncio
async def test_task_status_rejects_non_capability_id_before_store_access() -> None:
    class _TaskStatus:
        def __init__(self) -> None:
            self.calls: list[str] = []

        async def get_task(self, task_id: str, context: RequestContext):
            del context
            self.calls.append(task_id)
            return None

    status = _TaskStatus()
    use_case = GetTaskStatusUseCase(
        RuntimeDependencies(
            embedding=None,  # type: ignore[arg-type]
            memory_events=None,  # type: ignore[arg-type]
            context_builder=None,  # type: ignore[arg-type]
            task_status=status,
        )
    )

    assert await use_case.execute("task-1", RequestContext()) is None
    assert status.calls == []

    task_id = "0123456789abcdef0123456789abcdef"
    assert await use_case.execute(task_id, RequestContext(task_id=task_id)) is None
    assert status.calls == [task_id]
