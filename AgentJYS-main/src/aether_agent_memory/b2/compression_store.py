"""Durable stores for B2 compression Artifacts."""

from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from aether_agent_memory.b2.compression import CompressionArtifact


@runtime_checkable
class CompressionArtifactStore(Protocol):
    async def put(self, artifact: CompressionArtifact) -> None: ...

    async def get(self, artifact_id: str) -> CompressionArtifact | None: ...

    async def close(self) -> None: ...


class InMemoryCompressionArtifactStore:
    def __init__(self) -> None:
        self._items: dict[str, CompressionArtifact] = {}

    async def put(self, artifact: CompressionArtifact) -> None:
        self._items[artifact.artifact_id] = artifact

    async def get(self, artifact_id: str) -> CompressionArtifact | None:
        return self._items.get(artifact_id)

    async def close(self) -> None:
        return None


class SQLiteCompressionArtifactStore:
    """Durable local Artifact store used by the SQLite runtime profile."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = asyncio.Lock()
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=5.0)
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA busy_timeout=5000")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS compression_artifacts (
                    artifact_id TEXT PRIMARY KEY,
                    source_memory_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    payload TEXT NOT NULL
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_compression_source "
                "ON compression_artifacts (source_memory_id)"
            )

    async def put(self, artifact: CompressionArtifact) -> None:
        async with self._lock:
            await asyncio.to_thread(self._put_sync, artifact)

    def _put_sync(self, artifact: CompressionArtifact) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO compression_artifacts (
                    artifact_id, source_memory_id, created_at, payload
                )
                VALUES (?, ?, ?, ?)
                ON CONFLICT(artifact_id) DO UPDATE SET
                    source_memory_id=excluded.source_memory_id,
                    created_at=excluded.created_at,
                    payload=excluded.payload
                """,
                (
                    artifact.artifact_id,
                    artifact.source_memory_id,
                    artifact.created_at.isoformat(),
                    artifact.model_dump_json(),
                ),
            )

    async def get(self, artifact_id: str) -> CompressionArtifact | None:
        async with self._lock:
            return await asyncio.to_thread(self._get_sync, artifact_id)

    def _get_sync(self, artifact_id: str) -> CompressionArtifact | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload FROM compression_artifacts WHERE artifact_id = ?",
                (artifact_id,),
            ).fetchone()
        return CompressionArtifact.model_validate_json(row[0]) if row is not None else None

    async def close(self) -> None:
        return None


class RedisCompressionArtifactStore:
    """Redis-backed Artifact store with a separate namespace from Memory JSON."""

    def __init__(
        self,
        redis_url: str,
        *,
        namespace: str = "aether:b2:compression",
        operation_timeout_seconds: float = 0.5,
    ) -> None:
        from redis.asyncio import Redis

        if operation_timeout_seconds <= 0:
            raise ValueError("Redis operation timeout must be positive")
        self._redis: Any = Redis.from_url(
            redis_url,
            decode_responses=True,
            socket_connect_timeout=operation_timeout_seconds,
            socket_timeout=operation_timeout_seconds,
        )
        self._namespace = namespace.rstrip(":")

    def _key(self, artifact_id: str) -> str:
        return f"{self._namespace}:{artifact_id}"

    async def put(self, artifact: CompressionArtifact) -> None:
        await self._redis.set(self._key(artifact.artifact_id), artifact.model_dump_json())

    async def get(self, artifact_id: str) -> CompressionArtifact | None:
        payload = await self._redis.get(self._key(artifact_id))
        return CompressionArtifact.model_validate_json(payload) if payload is not None else None

    async def close(self) -> None:
        await self._redis.aclose()
