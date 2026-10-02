"""PostgreSQL implementation of the shared foundation transaction boundary.

All cooperating workers acquire one database-wide transaction advisory lock.
This deliberately preserves SQLite BEGIN IMMEDIATE's serialized read/modify/write
semantics, including multi-record CAS and commit guards. It is a correctness-first
backend, not a claim of concurrent write throughput: transactions must stay short
and must not perform remote I/O while holding the lock. Lock and statement waits
are bounded. Each UOW owns one connection; independent workers own independent
connections and PostgreSQL coordinates them across processes.

Raw values remain TEXT so migration and RecordTransaction retain exact strings.
A JSONB projection accelerates queue queries without reserializing stored values.
The inherited filesystem path is an anchor for existing local artifact APIs.
Business transactions never use SQLite; startup may verify a retained migration
source through an explicitly read-only SQLite snapshot.
"""

from __future__ import annotations

import hashlib
import json
import secrets
import sqlite3
from collections.abc import Iterator
from contextlib import closing, contextmanager
from pathlib import Path
from threading import RLock
from typing import Any

import psycopg
from psycopg import Connection, IsolationLevel
from psycopg.types.json import Jsonb

from aether_agent_memory.runtime.contracts.models import ErrorCode

from .common import FoundationError, encode
from .storage import SQLiteTransaction, SQLiteUnitOfWork, _active, business_write_guard
from .telemetry import Telemetry, current_node

# A stable signed bigint shared by every process using this schema contract.
# It is database-scoped, intentionally also serializing distinct schemas in one DB.
_TRANSACTION_LOCK = 0x4133503352460001
_CONNECT_TIMEOUT_SECONDS = 5
_LOCAL_LOCK_TIMEOUT_SECONDS = 10
_LOCK_TIMEOUT_MS = 5000
_STATEMENT_TIMEOUT_MS = 10000
_IDLE_TRANSACTION_TIMEOUT_MS = 15000
_PROJECTED_NAMESPACES = frozenset(
    {
        "p3_rf_tasks",
        "p3_rf_deliveries",
        "p3_rf_temporal_start_intents",
        "p3_rf_temporal_control_intents",
    }
)


def verify_sqlite_migration_source(path: Path, marker: Any) -> None:
    """Bind an existing, retained SQLite source to the v1 offline migration proof.

    No PG transaction may be held during this scan. The Service directory lock
    owns the retained source; its exact UTF-8 digest matches the migration CLI.
    """
    if (
        not isinstance(marker, dict)
        or marker.get("version") != 1
        or type(marker.get("record_count")) is not int
        or marker["record_count"] < 0
        or not isinstance(marker.get("source_hash"), str)
    ):
        raise ValueError("invalid PostgreSQL migration proof for SQLite source")
    digest, count = hashlib.sha256(), 0
    try:
        with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as db:
            db.execute("PRAGMA query_only=ON")
            db.execute("BEGIN")
            rows = db.execute(
                "SELECT namespace,tenant,key,value FROM capability_records "
                "ORDER BY namespace COLLATE BINARY,tenant COLLATE BINARY,key COLLATE BINARY"
            )
            for row in rows:
                for value in row:
                    if not isinstance(value, str):
                        raise ValueError("invalid SQLite source for PostgreSQL migration")
                    encoded = value.encode("utf-8")
                    digest.update(len(encoded).to_bytes(8, "big"))
                    digest.update(encoded)
                count += 1
    except (sqlite3.Error, UnicodeError):
        raise ValueError("cannot verify SQLite source for PostgreSQL migration") from None
    if (count, digest.hexdigest()) != (marker["record_count"], marker["source_hash"]):
        raise ValueError("SQLite source does not match PostgreSQL migration proof")


def _database_error(operation: str, error: psycopg.Error) -> FoundationError:
    # libpq errors may embed a DSN, password, host, query or user-supplied data.
    # SQLSTATE is the only driver diagnostic allowed across this boundary.
    state = f" (SQLSTATE {error.sqlstate})" if error.sqlstate else ""
    return FoundationError(
        ErrorCode.DEPENDENCY_UNAVAILABLE, f"PostgreSQL {operation} failed{state}"
    )


class _PostgresRecordTransaction:
    def __init__(self, connection: Connection[Any]) -> None:
        self.connection = connection
        self.open = True

    def check(self) -> None:
        if not self.open:
            raise FoundationError(ErrorCode.CONTRACT_VIOLATION, "transaction is closed")

    def get(self, namespace: str, tenant: str, key: str) -> str | None:
        self.check()
        row = self.connection.execute(
            "SELECT value FROM capability_records WHERE namespace=%s AND tenant=%s AND key=%s",
            (namespace, tenant, key),
        ).fetchone()
        return None if row is None else str(row[0])

    def put(self, namespace: str, tenant: str, key: str, value: str) -> None:
        self.check()
        document = None
        if namespace in _PROJECTED_NAMESPACES:
            try:
                data = json.loads(value)
                state = (
                    data.get("record", {}).get("state")
                    if namespace == "p3_rf_tasks"
                    else data.get("state")
                )
                if isinstance(state, str):
                    document = Jsonb(
                        {"record": {"state": state}}
                        if namespace == "p3_rf_tasks"
                        else {"state": state}
                    )
            except (ValueError, TypeError, AttributeError):
                # Even projected namespaces preserve the raw string contract.
                # An invalid projection has no queue state and is not selected.
                pass
        self.connection.execute(
            "INSERT INTO capability_records(namespace,tenant,key,value,document) "
            "VALUES(%s,%s,%s,%s,%s) ON CONFLICT(namespace,tenant,key) "
            "DO UPDATE SET value=excluded.value,document=excluded.document",
            (namespace, tenant, key, value, document),
        )

    def delete(self, namespace: str, tenant: str, key: str) -> None:
        self.check()
        self.connection.execute(
            "DELETE FROM capability_records WHERE namespace=%s AND tenant=%s AND key=%s",
            (namespace, tenant, key),
        )

    def scan(self, namespace: str) -> list[tuple[str, str, str]]:
        self.check()
        rows = self.connection.execute(
            "SELECT tenant,key,value FROM capability_records WHERE namespace=%s "
            "ORDER BY tenant,key",
            (namespace,),
        ).fetchall()
        return [(str(tenant), str(key), str(value)) for tenant, key, value in rows]


class PostgresCapabilityStore:
    """One bounded, thread-safe PostgreSQL connection, never a SQLite fallback."""

    def __init__(self, dsn: str) -> None:
        self._lock = RLock()
        self._closed = False
        self._dsn = dsn
        self._connection = self._connect()
        try:
            with self.transaction() as raw:
                raw.connection.execute(
                    "CREATE TABLE IF NOT EXISTS capability_records ("
                    'namespace TEXT COLLATE "C" NOT NULL, tenant TEXT COLLATE "C" NOT NULL, '
                    'key TEXT COLLATE "C" NOT NULL, value TEXT NOT NULL, document JSONB, '
                    "PRIMARY KEY(namespace,tenant,key))"
                )
                raw.connection.execute(
                    "CREATE INDEX IF NOT EXISTS p3_active_task_states ON capability_records "
                    "((document #>> '{record,state}'),key) WHERE namespace='p3_rf_tasks'"
                )
                raw.connection.execute(
                    "CREATE INDEX IF NOT EXISTS p3_pending_delivery_states ON capability_records "
                    "((document ->> 'state'),key) WHERE namespace='p3_rf_deliveries'"
                )
                raw.connection.execute(
                    "CREATE INDEX IF NOT EXISTS p3_pending_temporal_intents ON capability_records "
                    "(namespace,(document ->> 'state'),key) WHERE namespace IN "
                    "('p3_rf_temporal_start_intents','p3_rf_temporal_control_intents')"
                )
        except BaseException as exc:
            self._connection.close()
            self._closed = True
            if isinstance(exc, psycopg.Error):
                raise _database_error("initialization", exc) from None
            raise

    def _connect(self) -> Connection[Any]:
        connection = None
        try:
            connection = psycopg.connect(
                self._dsn, connect_timeout=_CONNECT_TIMEOUT_SECONDS, autocommit=True
            )
            # Waiters must see the preceding transaction after obtaining the lock.
            connection.isolation_level = IsolationLevel.READ_COMMITTED
            connection.execute(
                "SELECT set_config('lock_timeout',%s,false),"
                "set_config('statement_timeout',%s,false),"
                "set_config('idle_in_transaction_session_timeout',%s,false)",
                (
                    str(_LOCK_TIMEOUT_MS),
                    str(_STATEMENT_TIMEOUT_MS),
                    str(_IDLE_TRANSACTION_TIMEOUT_MS),
                ),
            )
            return connection
        except (psycopg.Error, ValueError) as exc:
            if connection is not None:
                connection.close()
            if isinstance(exc, psycopg.Error):
                raise _database_error("connection", exc) from None
            raise FoundationError(
                ErrorCode.DEPENDENCY_UNAVAILABLE, "invalid PostgreSQL connection configuration"
            ) from None

    @contextmanager
    def transaction(self) -> Iterator[_PostgresRecordTransaction]:
        if not self._lock.acquire(timeout=_LOCAL_LOCK_TIMEOUT_SECONDS):
            raise FoundationError(
                ErrorCode.DEPENDENCY_UNAVAILABLE, "PostgreSQL local transaction wait timed out"
            )
        raw = None
        try:
            if self._closed:
                raise FoundationError(ErrorCode.CONTRACT_VIOLATION, "PostgreSQL store is closed")
            if self._connection.closed or self._connection.broken:
                # Only a NEW transaction may reconnect. Never replay a failed
                # transaction: its commit may already have reached PostgreSQL.
                self._connection = self._connect()
            with self._connection.transaction():
                self._connection.execute("SELECT pg_advisory_xact_lock(%s)", (_TRANSACTION_LOCK,))
                raw = _PostgresRecordTransaction(self._connection)
                yield raw
        except psycopg.Error as exc:
            raise _database_error("transaction", exc) from None
        finally:
            if raw is not None:
                raw.open = False
            self._lock.release()

    def close(self) -> None:
        if not self._lock.acquire(timeout=_LOCAL_LOCK_TIMEOUT_SECONDS):
            raise FoundationError(
                ErrorCode.DEPENDENCY_UNAVAILABLE, "PostgreSQL close wait timed out"
            )
        try:
            if not self._closed:
                self._connection.close()
                self._closed = True
        finally:
            self._lock.release()


class PostgresTransaction(SQLiteTransaction):
    """Shared revision/scope/guard semantics with PostgreSQL-native queue reads."""

    raw: _PostgresRecordTransaction

    def __init__(self, raw: _PostgresRecordTransaction, cursor_key: str) -> None:
        super().__init__(raw, cursor_key)

    def _query(self, sql: str, parameters: tuple[Any, ...]) -> list[tuple[str, Any]]:
        self.check()
        return [
            (str(key), json.loads(value))
            for key, value in self.raw.connection.execute(sql, parameters).fetchall()
        ]

    def rows_after(
        self, table: str, cursor: str = "", *, limit: int = 100
    ) -> list[tuple[str, Any]]:
        self.check()
        if not 1 <= limit <= 1000:
            raise ValueError("invalid page limit")
        return self._query(
            "SELECT key,value FROM capability_records WHERE namespace=%s AND tenant=%s "
            "AND key>%s ORDER BY key LIMIT %s",
            ("p3_rf_" + table, "system", cursor, limit),
        )

    def pending_intent_rows(self, kind: str, *, limit: int = 100) -> list[tuple[str, Any]]:
        self.check()
        if kind not in {"start", "control"} or not 1 <= limit <= 1000:
            raise ValueError("invalid intent kind or batch limit")
        return self._query(
            "SELECT key,value FROM capability_records WHERE namespace=%s "
            "AND namespace IN ('p3_rf_temporal_start_intents','p3_rf_temporal_control_intents') "
            "AND document ->> 'state'=%s ORDER BY key LIMIT %s",
            (f"p3_rf_temporal_{kind}_intents", "pending", limit),
        )

    def active_task_rows(self, *, include_attention: bool = False) -> list[tuple[str, Any]]:
        states = ["pending", "running", "retry_wait", "recovery_wait"]
        if include_attention:
            states.append("attention_required")
        return self._query(
            "SELECT key,value FROM capability_records WHERE namespace='p3_rf_tasks' "
            "AND document #>> '{record,state}'=ANY(%s) ORDER BY key",
            (states,),
        )

    def pending_delivery_rows(self, *, include_attention: bool = False) -> list[tuple[str, Any]]:
        states = ["pending", "sent", "retry_wait"]
        if include_attention:
            states.append("attention_required")
        return self._query(
            "SELECT key,value FROM capability_records WHERE namespace='p3_rf_deliveries' "
            "AND document ->> 'state'=ANY(%s) ORDER BY key",
            (states,),
        )


class PostgresUnitOfWork(SQLiteUnitOfWork):
    """Foundation-compatible UOW; the base SQLite constructor is never called."""

    backend = "postgresql"

    def __init__(self, dsn: str, local_path: str | Path) -> None:
        self.path = Path(local_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # The legacy superclass concretely annotates its store as SQLite. The
        # replacement obeys the same raw protocol and owns no SQLite connection.
        self.store = PostgresCapabilityStore(dsn)  # type: ignore[assignment]
        self.telemetry: Telemetry | None = None
        try:
            with self.store.transaction() as raw:
                config = raw.get("p3_rf_meta", "system", "schema")
                if config is None:
                    config = encode({"version": 1, "cursor_key": secrets.token_hex(32)})
                    raw.put("p3_rf_meta", "system", "schema", config)
                data = json.loads(config)
                if data["version"] != 1:
                    raise FoundationError(
                        ErrorCode.CONTRACT_VIOLATION, "unsupported foundation schema"
                    )
                self.cursor_key = data["cursor_key"]
        except BaseException:
            self.store.close()
            raise

    @contextmanager
    def transaction(self) -> Iterator[PostgresTransaction]:
        # Keep the shared ContextVar and commit-hook order: domain services and
        # execution fencing install guards on this exact transaction boundary.
        if _active.get():
            raise FoundationError(
                ErrorCode.CONTRACT_VIOLATION,
                "nested transactions are forbidden; pass the existing transaction",
            )
        token = _active.set(True)
        tx = None
        node = current_node.get()
        try:
            with self.store.transaction() as raw:
                tx = PostgresTransaction(raw, self.cursor_key)  # type: ignore[arg-type]
                yield tx
                tx.check()
                execution_guard = business_write_guard.get()
                if tx.writes and execution_guard is not None:
                    execution_guard(tx)
                for guard in tx.before_commit:
                    guard()
                if tx.writes:
                    watermark = raw.get("p3_rf_meta", "system", "watermark")
                    raw.put("p3_rf_meta", "system", "watermark", str(int(watermark or "0") + 1))
        except BaseException:
            if node:
                node.store.emit(node, "rolled_back", writes=tx.writes if tx else {})
            raise
        else:
            if node and tx and tx.writes:
                node.store.emit(node, "committed", writes=tx.writes)
        finally:
            if tx is not None:
                tx.open = False
            _active.reset(token)

    def probe(self, *, write: bool = False) -> dict[str, Any]:
        with self.store.transaction() as raw:
            if raw.get("p3_rf_meta", "system", "schema") is None:
                raise FoundationError(ErrorCode.CONTRACT_VIOLATION, "foundation schema is missing")
            if write:
                key = secrets.token_hex(16)
                raw.put("p3_rf_probe", "system", key, "probe")
                raw.delete("p3_rf_probe", "system", key)
        return {
            "state": "available",
            "reason": "write_ok" if write else "read_ok",
            "backend": "postgresql",
        }
