"""SQLite reference implementation of the capability atomic-record boundary.

Services own record semantics; this store only supplies atomic transactions. A
deployment can replace it with an RF adapter implementing the same protocol.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import AbstractContextManager, contextmanager
from pathlib import Path
from threading import RLock
from typing import Protocol


class RecordTransaction(Protocol):
    def get(self, namespace: str, tenant: str, key: str) -> str | None: ...
    def put(self, namespace: str, tenant: str, key: str, value: str) -> None: ...
    def delete(self, namespace: str, tenant: str, key: str) -> None: ...
    def scan(self, namespace: str) -> list[tuple[str, str, str]]: ...


class AtomicRecordStore(Protocol):
    def transaction(self) -> AbstractContextManager[RecordTransaction]: ...


class _SQLiteTransaction:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def get(self, namespace: str, tenant: str, key: str) -> str | None:
        row = self.connection.execute(
            "SELECT value FROM capability_records WHERE namespace=? AND tenant=? AND key=?",
            (namespace, tenant, key),
        ).fetchone()
        return None if row is None else str(row[0])

    def put(self, namespace: str, tenant: str, key: str, value: str) -> None:
        self.connection.execute(
            "INSERT INTO capability_records VALUES(?,?,?,?) "
            "ON CONFLICT(namespace,tenant,key) DO UPDATE SET value=excluded.value",
            (namespace, tenant, key, value),
        )

    def delete(self, namespace: str, tenant: str, key: str) -> None:
        self.connection.execute(
            "DELETE FROM capability_records WHERE namespace=? AND tenant=? AND key=?",
            (namespace, tenant, key),
        )

    def scan(self, namespace: str) -> list[tuple[str, str, str]]:
        return self.connection.execute(
            "SELECT tenant,key,value FROM capability_records WHERE namespace=?", (namespace,)
        ).fetchall()


class SQLiteCapabilityStore:
    def __init__(self, path: str | Path) -> None:
        self._lock = RLock()
        self._connection = sqlite3.connect(str(path), isolation_level=None, check_same_thread=False)
        self._connection.execute("PRAGMA journal_mode=WAL")
        self._connection.execute("PRAGMA synchronous=FULL")
        self._connection.execute(
            "CREATE TABLE IF NOT EXISTS capability_records ("
            "namespace TEXT NOT NULL, tenant TEXT NOT NULL, key TEXT NOT NULL, "
            "value TEXT NOT NULL, PRIMARY KEY(namespace,tenant,key))"
        )

    @contextmanager
    def transaction(self) -> Iterator[RecordTransaction]:
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                yield _SQLiteTransaction(self._connection)
            except BaseException:
                self._connection.rollback()
                raise
            else:
                self._connection.commit()

    def close(self) -> None:
        with self._lock:
            self._connection.close()
