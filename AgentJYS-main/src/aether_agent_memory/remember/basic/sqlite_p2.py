"""Durable P2 substitute for local integration, not a production P2 implementation.

Immutable bytes live in a separate SQLite database. Metadata commits may leave
unreferenced objects; they never erase a successful upload on an ambiguous result.
Every call opens its own connection so process restart and worker handoff work.
"""

import asyncio
import hashlib
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path


class SQLiteP2:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.endpoint, self.bucket = str(self.path), "remember"
        self.available = True
        with self.connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("PRAGMA synchronous=FULL")
            db.execute(
                "CREATE TABLE IF NOT EXISTS objects ("
                "key TEXT PRIMARY KEY, body BLOB NOT NULL, digest TEXT NOT NULL)"
            )

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        if not self.available:
            raise OSError("simulated P2 unavailable")
        db = sqlite3.connect(self.path, timeout=30)
        try:
            with db:
                yield db
        finally:
            db.close()

    def get_object_sync(self, key: str) -> bytes | None:
        with self.connect() as db:
            row = db.execute("SELECT body,digest FROM objects WHERE key=?", (key,)).fetchone()
        if row is None:
            return None
        body = bytes(row[0])
        if hashlib.sha256(body).hexdigest() != row[1]:
            raise ValueError("P2 object checksum mismatch")
        return body

    def put_object_sync(self, key: str, body: bytes) -> None:
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT body FROM objects WHERE key=?", (key,)).fetchone()
            if row is not None and bytes(row[0]) != body:
                raise ValueError("immutable P2 key already contains different bytes")
            db.execute(
                "INSERT OR IGNORE INTO objects VALUES (?,?,?)",
                (key, body, hashlib.sha256(body).hexdigest()),
            )

    async def get_object(self, key: str) -> bytes | None:
        return await asyncio.to_thread(self.get_object_sync, key)

    async def put_object(self, key: str, body: bytes) -> None:
        await asyncio.to_thread(self.put_object_sync, key, body)

    async def delete_object(self, key: str) -> None:
        await asyncio.to_thread(self.delete_object_sync, key)

    def delete_object_sync(self, key: str) -> None:
        with self.connect() as db:
            db.execute("DELETE FROM objects WHERE key=?", (key,))

    async def read_range(self, key: str, start: int, end: int) -> bytes:
        return await asyncio.to_thread(self.read_range_sync, key, start, end)

    def read_range_sync(self, key: str, start: int, end: int) -> bytes:
        """Read only requested BLOB bytes; caller verifies the range manifest hash."""
        if start < 0 or end < start:
            raise ValueError("invalid byte range")
        with self.connect() as db:
            row = db.execute(
                "SELECT rowid,length(body) FROM objects WHERE key=?", (key,)
            ).fetchone()
            if row is None:
                raise FileNotFoundError(key)
            if end > row[1]:
                raise ValueError("range exceeds object")
            with db.blobopen("objects", "body", row[0], readonly=True) as blob:
                blob.seek(start)
                return blob.read(end - start)
