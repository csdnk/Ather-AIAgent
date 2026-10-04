"""Atomic record protocols shared by PostgreSQL and consumer contracts."""

from __future__ import annotations

from contextlib import AbstractContextManager
from typing import Protocol


class RecordTransaction(Protocol):
    def get(self, namespace: str, tenant: str, key: str) -> str | None: ...
    def put(self, namespace: str, tenant: str, key: str, value: str) -> None: ...
    def delete(self, namespace: str, tenant: str, key: str) -> None: ...
    def scan(self, namespace: str) -> list[tuple[str, str, str]]: ...


class AtomicRecordStore(Protocol):
    def transaction(self) -> AbstractContextManager[RecordTransaction]: ...
