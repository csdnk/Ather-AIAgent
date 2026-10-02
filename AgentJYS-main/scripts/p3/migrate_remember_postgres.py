"""Offline, atomic migration of stopped P3 SQLite metadata to PostgreSQL.

Stop the P3 service and its workers and disable automatic restart first. Run in
the environment that owns the original data directory so DirectoryLock protects
the same mount. Credentials are read only from an environment variable.

Example: python scripts/p3/migrate_remember_postgres.py /data/p3.db \
    --confirm-source-stopped --report /backups/postgres-migration.json

The source database is opened read-only and retained. Ceph bodies are untouched.
Historical SQLite logs remain available separately; new logs use PostgreSQL.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import sys
from collections.abc import Iterable, Iterator
from contextlib import closing
from pathlib import Path
from typing import Any

from aether_agent_memory.runtime.foundation.common import now
from aether_agent_memory.runtime.foundation.postgres import PostgresCapabilityStore
from aether_agent_memory.runtime.temporal.locking import DirectoryLock

_MARKER = ("p3_rf_meta", "system", "postgres_migration")
_SCHEMA = ("p3_rf_meta", "system", "schema")
_SQLITE_ROWS = (
    "SELECT namespace,tenant,key,value FROM capability_records "
    "ORDER BY namespace COLLATE BINARY,tenant COLLATE BINARY,key COLLATE BINARY"
)


class MigrationError(ValueError):
    """Controlled migration refusal with a credential-free explanation."""


def _digest(rows: Iterable[tuple[str, ...]]) -> tuple[int, str]:
    """Unambiguous hash of ordered, exact UTF-8 strings, without JSON rewriting."""
    digest = hashlib.sha256()
    count = 0
    for row in rows:
        if len(row) != 4 or any(not isinstance(value, str) for value in row):
            raise MigrationError("source contains unsupported non-text capability records")
        for value in row:
            encoded = value.encode("utf-8")
            digest.update(len(encoded).to_bytes(8, "big"))
            digest.update(encoded)
        count += 1
    return count, digest.hexdigest()


def _source_rows(db: sqlite3.Connection) -> Iterator[tuple[str, ...]]:
    cursor = db.execute(_SQLITE_ROWS)
    while rows := cursor.fetchmany(1000):
        yield from rows


def _target_digest(connection: Any) -> tuple[int, str]:
    # Server-side cursor bounds process memory even for a large metadata store.
    with connection.cursor(name="p3_migration_verify") as cursor:
        cursor.execute(
            "SELECT namespace,tenant,key,value FROM capability_records "
            "WHERE NOT (namespace=%s AND tenant=%s AND key=%s) "
            'ORDER BY namespace COLLATE "C",tenant COLLATE "C",key COLLATE "C"',
            _MARKER,
        )
        return _digest(cursor)


def _file_state(path: Path) -> tuple[tuple[int, int] | None, ...]:
    result = []
    for candidate in (path, Path(str(path) + "-wal")):
        if candidate.exists():
            info = candidate.stat()
            result.append((info.st_size, info.st_mtime_ns))
        else:
            result.append(None)
    return tuple(result)


def _bootstrap_only(connection: Any) -> bool:
    rows = connection.execute(
        "SELECT namespace,tenant,key,value FROM capability_records LIMIT 3"
    ).fetchall()
    if not rows:
        return True
    for namespace, tenant, key, value in rows:
        if (namespace, tenant, key) == _SCHEMA:
            try:
                schema = json.loads(value)
                if schema.get("version") != 1 or not schema.get("cursor_key"):
                    return False
            except (ValueError, AttributeError):
                return False
        elif (namespace, tenant, key, value) != ("p3_rf_meta", "system", "watermark", "0"):
            return False
    return True


def migrate(source: str | Path, dsn: str, *, source_stopped: bool = False) -> dict[str, Any]:
    """Copy exact raw records once; refuse nonmatching targets and active owners."""
    if not source_stopped:
        raise MigrationError("source must be stopped and automatic restart disabled")
    path = Path(source).resolve(strict=True)
    if not path.is_file():
        raise MigrationError("source must be an existing SQLite database file")
    ownership = DirectoryLock()
    ownership.acquire(path.parent)
    try:
        with closing(
            sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=0.25)
        ) as source_db:
            source_db.execute("PRAGMA query_only=ON")
            source_db.execute("BEGIN")
            schema = source_db.execute(
                "SELECT value FROM capability_records WHERE namespace=? AND tenant=? AND key=?",
                _SCHEMA,
            ).fetchone()
            try:
                config = json.loads(schema[0]) if schema else {}
                valid = config.get("version") == 1 and bool(config.get("cursor_key"))
            except (ValueError, TypeError, AttributeError):
                valid = False
            if not valid:
                raise MigrationError("source foundation schema or cursor key is invalid")
            if source_db.execute(
                "SELECT 1 FROM capability_records WHERE namespace=? AND tenant=? AND key=?",
                _MARKER,
            ).fetchone():
                raise MigrationError("source already contains the reserved PostgreSQL marker")
            snapshot = _file_state(path)
            record_count, source_hash = _digest(_source_rows(source_db))
            store = PostgresCapabilityStore(dsn)
            try:
                with store.transaction() as raw:
                    # The regular UOW advisory lock protects cooperating workers;
                    # this table lock also excludes direct SQL during the copy.
                    raw.connection.execute("LOCK TABLE capability_records IN ACCESS EXCLUSIVE MODE")
                    existing_count, existing_hash = _target_digest(raw.connection)
                    marker = raw.get(*_MARKER)
                    if (existing_count, existing_hash) == (record_count, source_hash):
                        status = "already_migrated"
                        if marker is not None:
                            try:
                                prior = json.loads(marker)
                                matches = (
                                    prior.get("source_hash") == source_hash
                                    and prior.get("record_count") == record_count
                                )
                            except (ValueError, AttributeError):
                                matches = False
                            if not matches:
                                raise MigrationError(
                                    "target migration marker does not match source"
                                )
                    else:
                        if marker is not None or not _bootstrap_only(raw.connection):
                            raise MigrationError(
                                "target contains unmatched records; migration refused"
                            )
                        # Only an empty/pristine UOW bootstrap reaches this point.
                        raw.connection.execute("DELETE FROM capability_records")
                        for namespace, tenant, key, value in _source_rows(source_db):
                            raw.put(namespace, tenant, key, value)
                        status = "migrated"
                    if _target_digest(raw.connection) != (record_count, source_hash):
                        raise MigrationError(
                            "target record count or raw-value hash verification failed"
                        )
                    if _file_state(path) != snapshot:
                        raise MigrationError(
                            "source changed during migration; transaction rolled back"
                        )
                    if marker is None:
                        raw.put(
                            *_MARKER,
                            json.dumps(
                                {
                                    "version": 1,
                                    "source_hash": source_hash,
                                    "record_count": record_count,
                                    "completed_at": now(),
                                },
                                separators=(",", ":"),
                            ),
                        )
                    report = {
                        "status": status,
                        "record_count": record_count,
                        "source_hash": source_hash,
                        "verified": True,
                        "source_retained": True,
                        "body_storage": "unchanged",
                    }
                return report
            finally:
                store.close()
    finally:
        ownership.release()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--dsn-env", default="AETHER_POSTGRES_DSN")
    parser.add_argument("--confirm-source-stopped", action="store_true", required=True)
    parser.add_argument("--report", type=Path)
    arguments = parser.parse_args(argv)
    try:
        if arguments.report:
            source_path = arguments.source.resolve()
            protected = {
                source_path,
                Path(str(source_path) + "-wal"),
                Path(str(source_path) + "-shm"),
                Path(str(source_path) + "-journal"),
                source_path.parent / "p3.instance.lock",
            }
            if arguments.report.resolve() in protected:
                raise MigrationError("report path must not overwrite the source or its lock files")
        dsn = os.environ.get(arguments.dsn_env)
        if not dsn:
            raise MigrationError("PostgreSQL DSN environment variable is missing")
        report = migrate(arguments.source, dsn, source_stopped=arguments.confirm_source_stopped)
        encoded = json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2)
        if arguments.report:
            arguments.report.parent.mkdir(parents=True, exist_ok=True)
            arguments.report.write_text(encoded + "\n", encoding="utf-8")
        print(encoded)
        return 0
    except Exception as exc:
        # Never print a driver message, source row or connection string.
        failure = {"status": "failed", "error_type": type(exc).__name__}
        if isinstance(exc, MigrationError):
            failure["reason"] = str(exc)
        print(json.dumps(failure), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
