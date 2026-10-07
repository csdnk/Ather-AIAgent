"""Complete internal metadata contract for independently implemented providers.

Raw record operations are trusted infrastructure capabilities. A remote provider
must preserve the atomic scope and commit guards, not split them into blind RPCs.
"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractContextManager
from pathlib import Path
from typing import TYPE_CHECKING, Any, NoReturn, Protocol, runtime_checkable

from aether_agent_memory.runtime.capability_store import AtomicRecordStore, RecordTransaction
from aether_agent_memory.runtime.contracts.models import ErrorCode, PageRequest, RecordRef
from aether_agent_memory.runtime.contracts.ports import Transaction

if TYPE_CHECKING:
    from aether_agent_memory.runtime.foundation.telemetry import Telemetry


class ClosableRecordStore(AtomicRecordStore, Protocol):
    def close(self) -> None: ...


@runtime_checkable
class MetadataTransaction(Transaction, Protocol):
    open: bool
    failed: bool
    before_commit: list[Callable[[], None]]
    writes: dict[str, int]

    @property
    def raw(self) -> RecordTransaction: ...

    def check(self) -> None: ...
    def abort(self, code: ErrorCode, message: str) -> NoReturn: ...
    def read(self, table: str, key: str) -> Any: ...
    def write(self, table: str, key: str, value: Any) -> None: ...
    def rows(self, table: str) -> list[tuple[str, Any]]: ...
    def rows_after(
        self, table: str, cursor: str = "", *, limit: int = 100
    ) -> list[tuple[str, Any]]: ...
    def pending_intent_rows(self, kind: str, *, limit: int = 100) -> list[tuple[str, Any]]: ...
    def celery_due_rows(self, now: str, *, limit: int = 100) -> list[tuple[str, Any]]: ...
    def active_task_rows(self, *, include_attention: bool = False) -> list[tuple[str, Any]]: ...
    def pending_delivery_rows(
        self, *, include_attention: bool = False
    ) -> list[tuple[str, Any]]: ...
    def revision(self, ref: RecordRef) -> int | None: ...
    def page(
        self, items: list[tuple[str, Any]], binding: Any, page: PageRequest
    ) -> tuple[list[Any], str | None]: ...

    @staticmethod
    def key(ref: RecordRef) -> str: ...


class MetadataUnitOfWork(Protocol):
    telemetry: Telemetry | None

    @property
    def backend(self) -> str: ...

    @property
    def path(self) -> Path: ...

    @property
    def store(self) -> ClosableRecordStore: ...

    def transaction(self) -> AbstractContextManager[MetadataTransaction]: ...
    def close(self) -> None: ...
