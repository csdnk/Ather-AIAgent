from __future__ import annotations

import asyncio
import os
from collections.abc import Callable
from typing import Any

from aether_agent_memory.b2.task_status import RedisTaskStatusStore
from aether_agent_memory.runtime.dtos import LongMemorySubmission, TaskStatusRecord
from aether_agent_memory.runtime.errors import ScopeError
from aether_agent_memory.runtime.request_context import RequestContext
from aether_agent_memory.runtime.status import ComponentHealth, ComponentStatus, RuntimeComponent

try:
    from aether_agent_memory.b2.celery_app import TASK_STATUS_URL, submit_long_text
except ImportError as exc:
    TASK_STATUS_URL = os.getenv(
        "AETHER_B2_TASK_STATUS_URL",
        os.getenv("AETHER_B2_REDIS_URL", "redis://localhost:6379/0"),
    )
    _CELERY_IMPORT_ERROR = exc

    def submit_long_text(
        *,
        text: str,
        tenant_id: str,
        user_id: str,
        agent_id: str,
        session_id: str,
        source_id: str,
        object_id: str | None = None,
        content_ref: str | None = None,
        request_id: str | None = None,
        trace_id: str | None = None,
    ) -> dict[str, str]:
        raise RuntimeError(f"B2 Celery path is unavailable: {_CELERY_IMPORT_ERROR}")


class CeleryLongMemoryTaskAdapter:
    def __init__(
        self,
        submitter: Callable[..., dict[str, str]] = submit_long_text,
        *,
        redis_url: str = TASK_STATUS_URL,
    ) -> None:
        self._submitter = submitter
        self._redis_url = redis_url

    async def submit_long_memory(
        self,
        *,
        text: str,
        tenant_id: str,
        user_id: str,
        agent_id: str,
        session_id: str,
        source_id: str,
        object_id: str | None,
        content_ref: str | None,
        context: RequestContext,
    ) -> LongMemorySubmission:
        payload = await asyncio.to_thread(
            self._submitter,
            text=text,
            tenant_id=tenant_id,
            user_id=user_id,
            agent_id=agent_id,
            session_id=session_id,
            source_id=source_id,
            object_id=object_id,
            content_ref=content_ref,
            request_id=context.request_id,
            trace_id=context.trace_id,
        )
        return LongMemorySubmission.from_mapping(payload)

    async def health(self) -> ComponentHealth:
        try:
            from redis import Redis

            def _ping() -> bool:
                client = Redis.from_url(
                    self._redis_url,
                    socket_connect_timeout=2,
                    socket_timeout=2,
                )
                try:
                    return bool(client.ping())
                finally:
                    client.close()

            reachable = await asyncio.to_thread(_ping)
            if not reachable:
                raise RuntimeError("Redis ping returned false")
            return ComponentHealth(
                component=RuntimeComponent.CELERY,
                status=ComponentStatus.HEALTHY,
                detail="Celery broker (Redis) reachable",
                critical=False,
            )
        except Exception as exc:
            return ComponentHealth(
                component=RuntimeComponent.CELERY,
                status=ComponentStatus.UNAVAILABLE,
                detail=f"{type(exc).__name__}: {exc}",
                critical=False,
            )


class RedisTaskStatusAdapter:
    def __init__(self, redis_url: str = TASK_STATUS_URL) -> None:
        self._store = RedisTaskStatusStore(redis_url)

    async def get_task(
        self,
        task_id: str,
        context: RequestContext,
    ) -> TaskStatusRecord | None:
        record = await asyncio.to_thread(self._store.get, task_id)
        if record is None:
            return None
        _assert_task_scope(record, context)
        return TaskStatusRecord.from_mapping(record)


def _assert_task_scope(record: dict[str, Any], context: RequestContext) -> None:
    """Reject reads that cross the tenant/user/agent boundary.

    A caller that provides no scope is allowed (internal/debug paths); a caller
    that provides a scope must match the ownership recorded on the task.
    """
    for field, expected in (
        ("tenant_id", context.tenant_id),
        ("user_id", context.user_id),
        ("agent_id", context.agent_id),
    ):
        recorded = record.get(field)
        if recorded and expected and str(recorded) != str(expected):
            raise ScopeError(
                f"task does not belong to this {field.removesuffix('_id')}"
            )
