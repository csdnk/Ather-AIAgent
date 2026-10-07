"""PostgreSQL implementation of the shared foundation transaction boundary.

All cooperating workers acquire one database-wide transaction advisory lock.
This deliberately preserves SQLite BEGIN IMMEDIATE's serialized read/modify/write
semantics, including multi-record CAS and commit guards. It is a correctness-first
backend, not a claim of concurrent write throughput: transactions must stay short
and must not perform remote I/O while holding the lock. Lock and statement waits
are bounded. Each UOW owns one connection; independent workers own independent
connections and PostgreSQL coordinates them across processes.

Raw values remain TEXT so RecordTransaction retains exact strings.
A JSONB projection accelerates queue queries without reserializing stored values.
The inherited filesystem path is an anchor for existing local artifact APIs.
Business transactions use PostgreSQL with the shared atomic record contract.
"""

from __future__ import annotations

import json
import secrets
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from threading import RLock
from typing import Any

import psycopg
from psycopg import Connection, IsolationLevel
from psycopg.types.json import Jsonb

from aether_agent_memory.runtime.contracts.models import ErrorCode
from aether_agent_memory.runtime.foundation.transactions import _active, business_write_guard

from .common import FoundationError, encode
from .telemetry import Telemetry, current_node
from .timings import measure_stage
from .transactions import StorageTransaction

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
        "p3_rf_celery_dispatch_intents",
    }
)


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
        # Cooperating writers hold the same advisory lock for this whole
        # transaction. Keep exact immutable strings, never parsed domain
        # objects; each new transaction starts with an empty read set.
        self._reads: dict[tuple[str, str, str], str | None] = {}

    def check(self) -> None:
        if not self.open:
            raise FoundationError(ErrorCode.CONTRACT_VIOLATION, "transaction is closed")

    def get(self, namespace: str, tenant: str, key: str) -> str | None:
        self.check()
        address = (namespace, tenant, key)
        if address in self._reads:
            return self._reads[address]
        row = self.connection.execute(
            "SELECT value FROM capability_records WHERE namespace=%s AND tenant=%s AND key=%s",
            (namespace, tenant, key),
        ).fetchone()
        value = None if row is None else str(row[0])
        self._reads[address] = value
        return value

    def put(self, namespace: str, tenant: str, key: str, value: str) -> None:
        self.check()
        # Invalidate before enqueueing SQL. The next read receives the database
        # result, including a pending SQL error, instead of trusting the write.
        self._reads.pop((namespace, tenant, key), None)
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
                        else {"state": state, "due_at": data["due_at"]}
                        if namespace == "p3_rf_celery_dispatch_intents"
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
        self._reads.pop((namespace, tenant, key), None)
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
                raw.connection.execute(
                    "CREATE INDEX IF NOT EXISTS p3_due_celery_intents ON capability_records "
                    "((document->>'due_at'),key) WHERE namespace='p3_rf_celery_dispatch_intents' "
                    "AND document->>'state'='pending'"
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
        with measure_stage("postgres_local_wait"):
            acquired = self._lock.acquire(timeout=_LOCAL_LOCK_TIMEOUT_SECONDS)
        if not acquired:
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
            with measure_stage("postgres_transaction"), self._connection.transaction():
                with measure_stage("postgres_lock_wait"):
                    self._connection.execute(
                        "SELECT pg_advisory_xact_lock(%s)", (_TRANSACTION_LOCK,)
                    )
                raw = _PostgresRecordTransaction(self._connection)
                # Acquire the shared lock before entering pipeline mode. Reads
                # flush preceding commands; exit receives every result before
                # the enclosing transaction may commit. Consecutive writes do
                # not each incur a separate network round trip.
                with self._connection.pipeline():
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


class PostgresTransaction(StorageTransaction):
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

    def celery_due_rows(self, now: str, *, limit: int = 100) -> list[tuple[str, Any]]:
        if not 1 <= limit <= 1000:
            raise ValueError("invalid batch limit")
        return self._query(
            "SELECT key,value FROM capability_records "
            "WHERE namespace='p3_rf_celery_dispatch_intents' "
            "AND document->>'state'='pending' AND document->>'due_at'<=%s "
            "ORDER BY document->>'due_at',key LIMIT %s",
            (now, limit),
        )

    def pending_intent_rows(self, kind: str, *, limit: int = 100) -> list[tuple[str, Any]]:
        self.check()
        if kind not in {"start", "control"} or not 1 <= limit <= 1000:
            raise ValueError("invalid intent kind or batch limit")
        if kind == "start":
            # Hashes identify retries, not queue position. Existing task timestamps
            # also order intents admitted before this scheduling change.
            return self._query(
                "SELECT key,value FROM (SELECT i.key,i.value,"
                "CASE WHEN i.value::jsonb#>>'{intent,job,kind}'='recall.execute' "
                "THEN 0 ELSE 1 END AS lane,"
                "row_number() OVER (PARTITION BY "
                "CASE WHEN i.value::jsonb#>>'{intent,job,kind}'='recall.execute' "
                "THEN 0 ELSE 1 END ORDER BY "
                "(t.value::jsonb->>'created_at')::timestamptz NULLS FIRST,i.key) AS position "
                "FROM capability_records i "
                "LEFT JOIN capability_records t ON t.namespace='p3_rf_tasks' "
                "AND t.tenant=i.tenant AND t.key=i.value::jsonb#>>'{intent,job,job_id}' "
                "WHERE i.namespace='p3_rf_temporal_start_intents' "
                "AND i.document->>'state'='pending' "
                ") scheduled ORDER BY position,lane LIMIT %s",
                (limit,),
            )
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

    def processing_tasks_for_memories(self, memory_ids: tuple[str, ...]) -> list[tuple[str, Any]]:
        """Fetch one closure frontier, retaining terminal history and batch edges.

        Task envelopes remain in the system record partition; domain tenant
        authorization and result-record validation stay with the caller.
        No state or latest-attempt filter is valid while discovering descendants.
        """
        self.check()
        if len(memory_ids) > 1000 or any(
            not isinstance(memory_id, str) or not memory_id or len(memory_id) > 160
            for memory_id in memory_ids
        ):
            raise ValueError("invalid processing memory frontier")
        if not memory_ids:
            return []
        ids = list(memory_ids)
        return self._query(
            "SELECT t.key,t.value FROM capability_records t "
            "WHERE t.namespace='p3_rf_tasks' AND t.tenant='system' AND ("
            "t.value::jsonb#>>'{record,subject,object_id}'=ANY(%s) OR EXISTS ("
            "SELECT 1 FROM capability_records b "
            "CROSS JOIN LATERAL jsonb_array_elements(b.value::jsonb->'refs') AS ref "
            "WHERE b.namespace='p3_rf_remember_batches' AND b.tenant=t.tenant "
            "AND b.key=t.key AND ref->>'memory_id'=ANY(%s))) ORDER BY t.key",
            (ids, ids),
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


class PostgresUnitOfWork:
    """PostgreSQL metadata provider implementing the shared transaction contract."""

    backend = "postgresql"

    def __init__(self, dsn: str, local_path: str | Path) -> None:
        self.path = Path(local_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.store = PostgresCapabilityStore(dsn)
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
                tx = PostgresTransaction(raw, self.cursor_key)
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

    def close(self) -> None:
        self.store.close()
