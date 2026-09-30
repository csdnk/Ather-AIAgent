"""Single-host ownership and bounded Activity concurrency, without a task queue."""

import asyncio
import os
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import BinaryIO


class DirectoryLock:
    def __init__(self) -> None:
        self._file: BinaryIO | None = None

    def acquire(self, path: Path) -> None:
        if self._file is not None:
            raise RuntimeError("directory lock already owned")
        path = path.resolve()
        path.mkdir(parents=True, exist_ok=True)
        stream = (path / "p3.instance.lock").open("a+b")
        stream.seek(0, os.SEEK_END)
        if stream.tell() == 0:
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        try:
            if sys.platform == "win32":
                import msvcrt

                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            stream.close()
            raise RuntimeError("P3 business directory is already owned") from None
        self._file = stream

    def release(self) -> None:
        if self._file is not None:
            self._file.close()
            self._file = None


class ExecutionLimits:
    def __init__(self, classes: dict[str, int], *, per_tenant: int = 1, per_scope: int = 1) -> None:
        if min(*classes.values(), per_tenant, per_scope) <= 0:
            raise ValueError("execution capacity must be positive")
        self.classes, self.per_tenant, self.per_scope = classes, per_tenant, per_scope
        self._entries: dict[tuple[str, ...], tuple[asyncio.Semaphore, int]] = {}

    @property
    def waiter_count(self) -> int:
        return sum(count for _, count in self._entries.values())

    @asynccontextmanager
    async def slot(
        self, tenant_id: str, scope_key: str, execution_class: str
    ) -> AsyncIterator[None]:
        keys = [
            (("class", execution_class), self.classes[execution_class]),
            (("tenant", execution_class, tenant_id), self.per_tenant),
            (("scope", execution_class, tenant_id, scope_key), self.per_scope),
        ]
        acquired: list[asyncio.Semaphore] = []
        referenced: list[tuple[str, ...]] = []
        try:
            for key, capacity in keys:
                semaphore, count = self._entries.get(key, (asyncio.Semaphore(capacity), 0))
                self._entries[key] = (semaphore, count + 1)
                referenced.append(key)
                await semaphore.acquire()
                acquired.append(semaphore)
            yield
        finally:
            for semaphore in reversed(acquired):
                semaphore.release()
            for key in reversed(referenced):
                semaphore, count = self._entries[key]
                if count == 1:
                    del self._entries[key]
                else:
                    self._entries[key] = (semaphore, count - 1)
