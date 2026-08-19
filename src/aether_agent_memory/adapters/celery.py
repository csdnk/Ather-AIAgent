from __future__ import annotations

import asyncio
import os
from collections.abc import Callable
from typing import Any

from aether_agent_memory.b2.task_status import RedisTaskStatusStore
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
    def __init__(self, submitter: Callable[..., dict[str, str]] = submit_long_text) -> None:
        self._submitter = submitter

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
    ) -> dict[str, Any]:
        return await asyncio.to_thread(
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

    async def health(self) -> ComponentHealth:
        return ComponentHealth(
            component=RuntimeComponent.CELERY,
            status=ComponentStatus.UNKNOWN,
            detail="Celery broker health is delegated to Redis/task submission path",
            critical=False,
        )


class RedisTaskStatusAdapter:
    def __init__(self, redis_url: str = TASK_STATUS_URL) -> None:
        self._store = RedisTaskStatusStore(redis_url)

    async def get_task(self, task_id: str, context: RequestContext) -> dict[str, Any] | None:
        return await asyncio.to_thread(self._store.get, task_id)
