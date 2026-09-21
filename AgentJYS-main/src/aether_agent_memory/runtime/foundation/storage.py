"""Scope/version/CAS adapter over the existing SQLite capability transaction.

The raw namespace methods are trusted infrastructure APIs, never request APIs.
Tombstone revisions prevent delete/recreate ABA. No domain rules live here.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Any, NoReturn

from pydantic import JsonValue

from aether_agent_memory.runtime.capability_store import RecordTransaction, SQLiteCapabilityStore
from aether_agent_memory.runtime.contracts.models import (
    ErrorCode,
    PageRequest,
    RecordPage,
    RecordRef,
    Scope,
)

from .common import FoundationError, encode, fingerprint
from .telemetry import Telemetry, current_node

_active: ContextVar[bool] = ContextVar("p3_foundation_transaction", default=False)


class SQLiteTransaction:
    def __init__(self, raw: RecordTransaction, cursor_key: str) -> None:
        self.raw = raw
        self.cursor_key = cursor_key
        self.open = True
        self.failed = False
        self.before_commit: list[Callable[[], None]] = []
        self.writes: dict[str, int] = {}

    def check(self) -> None:
        if not self.open or self.failed:
            raise FoundationError(ErrorCode.CONTRACT_VIOLATION, "transaction is closed or aborted")

    def abort(self, code: ErrorCode, message: str) -> NoReturn:
        self.failed = True
        raise FoundationError(code, message)

    def read(self, table: str, key: str) -> Any:
        self.check()
        raw = self.raw.get("p3_rf_" + table, "system", key)
        return None if raw is None else json.loads(raw)

    def write(self, table: str, key: str, value: Any) -> None:
        self.check()
        self.raw.put("p3_rf_" + table, "system", key, encode(value))
        self.writes[table] = self.writes.get(table, 0) + 1

    def rows(self, table: str) -> list[tuple[str, Any]]:
        self.check()
        return [(k, json.loads(v)) for _, k, v in self.raw.scan("p3_rf_" + table)]

    @staticmethod
    def key(ref: RecordRef) -> str:
        return fingerprint(ref.model_dump(mode="json"))

    def get(self, ref: RecordRef) -> dict[str, JsonValue] | None:
        row = self.read("records", self.key(ref))
        return None if row is None else row["value"]

    def revision(self, ref: RecordRef) -> int | None:
        row = self.read("records", self.key(ref))
        return None if row is None else int(row["revision"])

    def put_if_revision(
        self, ref: RecordRef, value: dict[str, JsonValue], expected_revision: int | None
    ) -> int:
        current = self.revision(ref)
        if current != expected_revision:
            self.abort(ErrorCode.VERSION_CONFLICT, "record revision changed")
        revision = (current or 0) + 1
        self.write(
            "records",
            self.key(ref),
            {"ref": ref.model_dump(mode="json"), "revision": revision, "value": value},
        )
        return revision

    def remove_if_revision(self, ref: RecordRef, expected_revision: int) -> None:
        if self.revision(ref) != expected_revision or self.get(ref) is None:
            self.abort(ErrorCode.VERSION_CONFLICT, "record revision changed or deleted")
        self.write(
            "records",
            self.key(ref),
            {"ref": ref.model_dump(mode="json"), "revision": expected_revision + 1, "value": None},
        )

    def page(
        self, items: list[tuple[str, Any]], binding: Any, page: PageRequest
    ) -> tuple[list[Any], str | None]:
        scope_hash = fingerprint(binding)
        last = ""
        if page.cursor:
            try:
                payload, signature = page.cursor.split(".")
                expected = hmac.new(
                    self.cursor_key.encode(), payload.encode(), hashlib.sha256
                ).hexdigest()
                data = json.loads(base64.urlsafe_b64decode(payload))
                if not hmac.compare_digest(signature, expected) or data["binding"] != scope_hash:
                    raise ValueError("cursor binding")
                last = data["last"]
            except (ValueError, KeyError, TypeError) as exc:
                raise FoundationError(
                    ErrorCode.INVALID_ARGUMENT, "invalid cursor for this query"
                ) from exc
        eligible = sorted((k, v) for k, v in items if k > last)
        selected = eligible[: page.limit]
        cursor = None
        if len(eligible) > page.limit:
            payload = base64.urlsafe_b64encode(
                encode({"binding": scope_hash, "last": selected[-1][0]}).encode()
            ).decode()
            signature = hmac.new(
                self.cursor_key.encode(), payload.encode(), hashlib.sha256
            ).hexdigest()
            cursor = payload + "." + signature
        return [v for _, v in selected], cursor

    def scan_page(self, owner: str, scope: Scope, page: PageRequest) -> RecordPage:
        scoped = [
            (key, row["ref"])
            for key, row in self.rows("records")
            if row["value"] is not None
            and row["ref"]["owner"] == owner
            and row["ref"]["scope"] == scope.model_dump(mode="json")
        ]
        selected, cursor = self.page(scoped, [owner, scope.model_dump(mode="json")], page)
        return RecordPage(
            records=tuple(RecordRef.model_validate(r) for r in selected), next_cursor=cursor
        )


class SQLiteUnitOfWork:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.store = SQLiteCapabilityStore(self.path)
        self.telemetry: Telemetry | None = None
        with self.store.transaction() as raw:
            config = raw.get("p3_rf_meta", "system", "schema")
            if config is None:
                config = encode({"version": 1, "cursor_key": secrets.token_hex(32)})
                raw.put("p3_rf_meta", "system", "schema", config)
            data = json.loads(config)
            if data["version"] != 1:
                raise FoundationError(ErrorCode.CONTRACT_VIOLATION, "unsupported foundation schema")
            self.cursor_key = data["cursor_key"]

    @contextmanager
    def transaction(self) -> Iterator[SQLiteTransaction]:
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
                tx = SQLiteTransaction(raw, self.cursor_key)
                yield tx
                tx.check()
                for guard in tx.before_commit:
                    guard()
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

    def close(self) -> None:
        self.store.close()


def native(tx: Any) -> SQLiteTransaction:
    if not isinstance(tx, SQLiteTransaction):
        raise FoundationError(
            ErrorCode.CONTRACT_VIOLATION, "foundation requires its shared transaction"
        )
    tx.check()
    return tx
