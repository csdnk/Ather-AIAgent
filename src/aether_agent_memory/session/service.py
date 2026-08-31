from __future__ import annotations

from datetime import UTC, datetime

from aether_agent_memory.context_store.models import (
    ContextLayer,
    ContextProjectionWorkItem,
)
from aether_agent_memory.context_store.ports import ContextProjectionQueuePort
from aether_agent_memory.core.scope import Scope
from aether_agent_memory.session.models import (
    SessionArchive,
    SessionCommitResult,
    SessionExtractionWorkItem,
    SessionMessage,
    SessionRecord,
)
from aether_agent_memory.session.ports import (
    SessionExtractionQueuePort,
    SessionStorePort,
)


class SessionConflictError(RuntimeError):
    pass


class SessionService:
    """P3 session lifecycle with bounded records and optimistic concurrency."""

    def __init__(
        self,
        store: SessionStorePort,
        *,
        max_messages: int = 1000,
        max_archives: int = 100,
        write_retries: int = 3,
        extraction_queue: SessionExtractionQueuePort | None = None,
        context_projection_queue: ContextProjectionQueuePort | None = None,
    ) -> None:
        if max_messages < 1 or max_archives < 1 or write_retries < 1:
            raise ValueError("session limits and write_retries must be positive")
        self._store = store
        self._max_messages = max_messages
        self._max_archives = max_archives
        self._write_retries = write_retries
        self._extraction_queue = extraction_queue
        self._context_projection_queue = context_projection_queue

    async def get(self, scope: Scope) -> SessionRecord | None:
        return await self._store.get(scope)

    async def get_archive(self, scope: Scope, archive_id: str) -> SessionArchive | None:
        record = await self._store.get(scope)
        if record is None:
            return None
        return next(
            (archive for archive in record.archives if archive.archive_id == archive_id),
            None,
        )

    async def append(self, scope: Scope, message: SessionMessage) -> SessionRecord:
        for _ in range(self._write_retries):
            current = await self._store.get(scope) or SessionRecord(scope=scope)
            if len(current.messages) >= self._max_messages:
                raise ValueError("session message limit reached; commit the session first")
            updated = current.model_copy(
                update={
                    "revision": current.revision + 1,
                    "messages": [*current.messages, message],
                    "updated_at": datetime.now(UTC),
                }
            )
            if await self._store.save(updated, expected_revision=current.revision):
                return updated
        raise SessionConflictError("session append conflicted after retries")

    async def commit(
        self,
        scope: Scope,
        *,
        keep_recent_count: int = 5,
    ) -> SessionCommitResult:
        if keep_recent_count < 0:
            raise ValueError("keep_recent_count must not be negative")
        for _ in range(self._write_retries):
            current = await self._store.get(scope) or SessionRecord(scope=scope)
            split_at = max(len(current.messages) - keep_recent_count, 0)
            archived_messages = current.messages[:split_at]
            retained_messages = current.messages[split_at:]
            if not archived_messages:
                return SessionCommitResult(
                    session_id=scope.session_id or "",
                    archived_messages=0,
                    retained_messages=len(retained_messages),
                    revision=current.revision,
                    status="skipped",
                )
            archive = _build_archive(
                archived_messages,
                source_revision=current.revision + 1,
                extraction_status=(
                    "pending" if self._extraction_queue is not None else "not_implemented"
                ),
            )
            archives = [*current.archives, archive][-self._max_archives :]
            updated = current.model_copy(
                update={
                    "revision": current.revision + 1,
                    "messages": retained_messages,
                    "archives": archives,
                    "updated_at": datetime.now(UTC),
                }
            )
            if await self._store.save(updated, expected_revision=current.revision):
                extraction_status = await self._enqueue_extraction(scope, archive)
                projection_status = await self._enqueue_context_projection(
                    scope, archive, updated.revision
                )
                return SessionCommitResult(
                    session_id=scope.session_id or "",
                    archive_id=archive.archive_id,
                    archived_messages=len(archived_messages),
                    retained_messages=len(retained_messages),
                    revision=updated.revision,
                    status="committed",
                    memory_extraction_status=extraction_status,
                    context_projection_status=projection_status,
                )
        raise SessionConflictError("session commit conflicted after retries")

    async def close(self) -> None:
        await self._store.close()

    async def set_archive_extraction_status(
        self,
        scope: Scope,
        archive_id: str,
        status: str,
    ) -> bool:
        """Persist worker state without holding a lock across model calls."""
        for _ in range(self._write_retries):
            current = await self._store.get(scope)
            if current is None:
                return False
            archive_index = next(
                (
                    index
                    for index, archive in enumerate(current.archives)
                    if archive.archive_id == archive_id
                ),
                None,
            )
            if archive_index is None:
                return False
            archive = current.archives[archive_index]
            if archive.extraction_status == status:
                return True
            archives = [archive_item.model_copy(deep=True) for archive_item in current.archives]
            archives[archive_index] = archive.model_copy(
                update={"extraction_status": status}
            )
            updated = current.model_copy(
                update={
                    "revision": current.revision + 1,
                    "archives": archives,
                    "updated_at": datetime.now(UTC),
                }
            )
            if await self._store.save(updated, expected_revision=current.revision):
                return True
        raise SessionConflictError("session archive status conflicted after retries")

    async def _enqueue_extraction(
        self,
        scope: Scope,
        archive: SessionArchive,
    ) -> str:
        if self._extraction_queue is None:
            return "not_implemented"
        try:
            await self._extraction_queue.enqueue(
                SessionExtractionWorkItem(scope=scope, archive_id=archive.archive_id)
            )
        except Exception:
            # The archive is authoritative once CAS succeeds. Surface queue
            # loss explicitly so a reconciler can enqueue it later.
            return "queue_failed"
        return "pending"

    async def _enqueue_context_projection(
        self,
        scope: Scope,
        archive: SessionArchive,
        source_revision: int,
    ) -> str:
        if self._context_projection_queue is None:
            return "not_implemented"
        try:
            from aether_agent_memory.context_store.mapping import session_archive_uri

            await self._context_projection_queue.enqueue(
                ContextProjectionWorkItem(
                    uri=session_archive_uri(scope, archive.archive_id),
                    source_revision=source_revision,
                    scope=scope,
                    layers=[ContextLayer.ABSTRACT, ContextLayer.OVERVIEW],
                )
            )
        except Exception:
            return "queue_failed"
        return "pending"


def _build_archive(
    messages: list[SessionMessage],
    *,
    source_revision: int = 1,
    extraction_status: str = "not_implemented",
) -> SessionArchive:
    joined = " ".join(message.content.strip() for message in messages if message.content.strip())
    abstract = _truncate(joined, 160)
    overview = _truncate(
        "\n".join(f"{message.role.value}: {message.content.strip()}" for message in messages),
        2000,
    )
    return SessionArchive(
        messages=[message.model_copy(deep=True) for message in messages],
        abstract=abstract,
        overview=overview,
        source_revision=source_revision,
        extraction_status=extraction_status,
    )


def _truncate(text: str, limit: int) -> str:
    normalized = " ".join(text.split()) if "\n" not in text else text.strip()
    return normalized if len(normalized) <= limit else normalized[: limit - 3].rstrip() + "..."
