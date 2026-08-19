from __future__ import annotations

import asyncio
import builtins
import sqlite3
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from aether_agent_memory.core.memory import Memory
from aether_agent_memory.persistence.concurrency import AdaptiveConcurrency


class InMemoryMemoryStore:
    def __init__(self) -> None:
        self._items: dict[str, Memory] = {}

    async def upsert(self, memory: Memory) -> None:
        self._items[memory.id] = memory

    async def get(self, memory_id: str) -> Memory | None:
        return self._items.get(memory_id)

    async def list(self) -> builtins.list[Memory]:
        return builtins.list(self._items.values())

    async def list_scoped(self, **scope: str | None) -> builtins.list[Memory]:
        return [
            memory
            for memory in self._items.values()
            if all(
                value is None or getattr(memory, field) == value
                for field, value in scope.items()
            )
        ]

    async def delete(self, memory_id: str) -> bool:
        return self._items.pop(memory_id, None) is not None


class SQLiteMemoryStore:
    """Small-footprint durable B2 store for local development and MVP integration."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = asyncio.Lock()
        self._initialize()

    async def upsert(self, memory: Memory) -> None:
        async with self._lock:
            await asyncio.to_thread(self._upsert_sync, memory)

    async def get(self, memory_id: str) -> Memory | None:
        async with self._lock:
            return await asyncio.to_thread(self._get_sync, memory_id)

    async def list(self) -> builtins.list[Memory]:
        async with self._lock:
            return await asyncio.to_thread(self._list_sync)

    async def list_scoped(self, **scope: str | None) -> builtins.list[Memory]:
        async with self._lock:
            return await asyncio.to_thread(self._list_scoped_sync, scope)

    async def delete(self, memory_id: str) -> bool:
        async with self._lock:
            return await asyncio.to_thread(self._delete_sync, memory_id)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=5.0)
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA busy_timeout=5000")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS memories (
                    id TEXT PRIMARY KEY,
                    memory_type TEXT NOT NULL,
                    state TEXT NOT NULL,
                    tenant_id TEXT,
                    user_id TEXT,
                    agent_id TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    payload TEXT NOT NULL
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_memories_scope "
                "ON memories (tenant_id, user_id, agent_id, session_id, memory_type, state)"
            )

    def _upsert_sync(self, memory: Memory) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO memories (
                    id, memory_type, state, tenant_id, user_id, agent_id,
                    session_id, created_at, payload
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    memory_type=excluded.memory_type,
                    state=excluded.state,
                    tenant_id=excluded.tenant_id,
                    user_id=excluded.user_id,
                    agent_id=excluded.agent_id,
                    session_id=excluded.session_id,
                    created_at=excluded.created_at,
                    payload=excluded.payload
                """,
                (
                    memory.id,
                    memory.type.value,
                    memory.state.value,
                    memory.tenant_id,
                    memory.user_id,
                    memory.agent_id,
                    memory.session_id,
                    memory.created_at.isoformat(),
                    memory.model_dump_json(),
                ),
            )

    def _get_sync(self, memory_id: str) -> Memory | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload FROM memories WHERE id = ?",
                (memory_id,),
            ).fetchone()
        return Memory.model_validate_json(row[0]) if row is not None else None

    def _list_sync(self) -> builtins.list[Memory]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT payload FROM memories ORDER BY created_at, id"
            ).fetchall()
        return [Memory.model_validate_json(row[0]) for row in rows]

    def _list_scoped_sync(self, scope: dict[str, str | None]) -> builtins.list[Memory]:
        filters = [(field, value) for field, value in scope.items() if value is not None]
        where = " AND ".join(f"{field} = ?" for field, _ in filters) or "1 = 1"
        with self._connect() as connection:
            rows = connection.execute(
                f"SELECT payload FROM memories WHERE {where} ORDER BY created_at, id",
                [value for _, value in filters],
            ).fetchall()
        return [Memory.model_validate_json(row[0]) for row in rows]

    def _delete_sync(self, memory_id: str) -> bool:
        with self._connect() as connection:
            cursor = connection.execute("DELETE FROM memories WHERE id = ?", (memory_id,))
            return cursor.rowcount > 0


class RedisMemoryStore:
    """Redis-backed memory store for the runtime Working Memory path.

    Each memory is stored as JSON under an id key and tracked in a Redis set,
    which keeps the existing MemoryStore interface compatible with the mock
    managers while removing SQLite's process-wide serialization point.
    """

    def __init__(
        self,
        redis_url: str,
        namespace: str = "aether:b2:memory",
        max_connections: int = 1024,
        adaptive_initial: int = 8,
        adaptive_maximum: int = 128,
        adaptive_target_ms: float = 10.0,
        operation_timeout_seconds: float = 0.5,
        max_retries: int = 2,
    ) -> None:
        from redis.asyncio import Redis

        if operation_timeout_seconds <= 0:
            raise ValueError("Redis operation timeout must be positive")
        if max_retries < 0:
            raise ValueError("Redis max retries must not be negative")
        self._redis: Any = Redis.from_url(
            redis_url,
            decode_responses=True,
            max_connections=max_connections,
            socket_connect_timeout=operation_timeout_seconds,
            socket_timeout=operation_timeout_seconds,
        )
        self._namespace = namespace.rstrip(":")
        self._index_key = f"{self._namespace}:index"
        self._write_limiter = AdaptiveConcurrency(
            initial=adaptive_initial,
            maximum=adaptive_maximum,
            target_latency_ms=adaptive_target_ms,
        )
        self._operation_timeout_seconds = operation_timeout_seconds
        self._max_retries = max_retries

    def _key(self, memory_id: str) -> str:
        return f"{self._namespace}:{memory_id}"

    def _scope_index_key(self, field: str, value: str | None) -> str:
        # Values are part of a Redis key, not a query.  Keeping the field name
        # makes each index independently inspectable and avoids cross-scope reads.
        return f"{self._namespace}:scope:{field}:{value if value is not None else '_none'}"

    def _scope_index_keys(self, memory: Memory) -> list[str]:
        return [
            self._scope_index_key("tenant_id", memory.tenant_id),
            self._scope_index_key("user_id", memory.user_id),
            self._scope_index_key("agent_id", memory.agent_id),
            self._scope_index_key("session_id", memory.session_id),
        ]

    @staticmethod
    def _ttl_seconds(memory: Memory) -> int | None:
        if memory.expires_at is None:
            return None
        remaining = (memory.expires_at - datetime.now(UTC)).total_seconds()
        return max(1, int(remaining))

    async def upsert(self, memory: Memory) -> None:
        kwargs: dict[str, Any] = {}
        ttl = self._ttl_seconds(memory)
        if ttl is not None:
            kwargs["ex"] = ttl
        async with self._write_limiter.slot():
            async def write() -> None:
                pipeline = self._redis.pipeline(transaction=False)
                pipeline.set(self._key(memory.id), memory.model_dump_json(), **kwargs)
                pipeline.sadd(self._index_key, memory.id)
                for index_key in self._scope_index_keys(memory):
                    pipeline.sadd(index_key, memory.id)
                await pipeline.execute()

            await self._retry(write)

    @property
    def write_concurrency(self) -> int:
        return self._write_limiter.limit

    async def get(self, memory_id: str) -> Memory | None:
        payload = await self._retry(lambda: self._redis.get(self._key(memory_id)))
        if payload is None:
            await self._retry(lambda: self._redis.srem(self._index_key, memory_id))
            return None
        return Memory.model_validate_json(payload)

    async def list(self) -> builtins.list[Memory]:
        return await self._list_from_index(self._index_key)

    async def list_scoped(
        self,
        *,
        tenant_id: str | None = None,
        user_id: str | None = None,
        agent_id: str | None = None,
        session_id: str | None = None,
    ) -> builtins.list[Memory]:
        """Read a bounded scope index instead of the global memory index.

        Select the most specific scope supplied by the caller; remaining scope
        fields are checked after deserialisation to preserve exact semantics.
        """
        scope = {
            "session_id": session_id,
            "agent_id": agent_id,
            "user_id": user_id,
            "tenant_id": tenant_id,
        }
        selected = next(
            ((field, value) for field, value in scope.items() if value is not None),
            None,
        )
        if selected is None:
            return await self.list()
        index_key = self._scope_index_key(*selected)
        # Existing deployments may contain records written before scope indexes
        # were introduced.  Fall back only while that particular index is absent;
        # new scopes never pay a global-read cost.
        if not await self._retry(lambda: self._redis.exists(index_key)):
            memories = await self.list()
        else:
            memories = await self._list_from_index(index_key)
        return [
            memory for memory in memories
            if all(
                value is None or getattr(memory, field) == value
                for field, value in scope.items()
            )
        ]

    async def _list_from_index(self, index_key: str) -> builtins.list[Memory]:
        ids = builtins.list(await self._retry(lambda: self._redis.smembers(index_key)))
        if not ids:
            return []
        memories: list[Memory] = []
        stale: list[str] = []
        # A single MGET over an unbounded set was the BEAM replay timeout root
        # cause.  Small batches keep each Redis operation below its timeout.
        batch_size = 256

        def fetch_payloads(memory_ids: builtins.list[str]) -> Callable[[], Awaitable[Any]]:
            async def operation() -> Any:
                return await self._redis.mget(
                    [self._key(memory_id) for memory_id in memory_ids]
                )

            return operation

        for start in range(0, len(ids), batch_size):
            batch = ids[start : start + batch_size]
            payloads = await self._retry(fetch_payloads(batch))
            for memory_id, payload in zip(batch, payloads, strict=True):
                if payload is None:
                    stale.append(memory_id)
                    continue
                memories.append(Memory.model_validate_json(payload))
        if stale:
            await self._retry(lambda: self._redis.srem(index_key, *stale))
        memories.sort(key=lambda memory: (memory.created_at, memory.id))
        return memories

    async def delete(self, memory_id: str) -> bool:
        async def remove() -> int:
            payload = await self._redis.get(self._key(memory_id))
            pipeline = self._redis.pipeline(transaction=False)
            pipeline.delete(self._key(memory_id))
            pipeline.srem(self._index_key, memory_id)
            if payload is not None:
                memory = Memory.model_validate_json(payload)
                for index_key in self._scope_index_keys(memory):
                    pipeline.srem(index_key, memory_id)
            result = await pipeline.execute()
            return int(result[0])

        deleted = await self._retry(remove)
        return bool(deleted)

    async def _retry(self, operation: Callable[[], Awaitable[Any]]) -> Any:
        """Bound Redis failures so callers can degrade instead of hanging forever."""
        last_error: Exception | None = None
        for attempt in range(self._max_retries + 1):
            try:
                async with asyncio.timeout(self._operation_timeout_seconds):
                    return await operation()
            except Exception as exc:
                last_error = exc
                if attempt == self._max_retries:
                    raise
                await asyncio.sleep(0.01 * (2**attempt))
        raise RuntimeError("unreachable") from last_error

    async def close(self) -> None:
        await self._redis.aclose()
