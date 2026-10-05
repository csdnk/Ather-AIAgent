"""Backend-independent record, scope, revision, cursor and abort semantics."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from collections.abc import Callable
from contextvars import ContextVar
from typing import Any, NoReturn

from pydantic import JsonValue

from aether_agent_memory.runtime.capability_store import RecordTransaction
from aether_agent_memory.runtime.contracts.models import (
    ErrorCode,
    PageRequest,
    RecordPage,
    RecordRef,
    Scope,
)
from aether_agent_memory.runtime.storage.ports import MetadataTransaction

from .common import FoundationError, encode, fingerprint

_active: ContextVar[bool] = ContextVar("p3_foundation_transaction", default=False)
business_write_guard: ContextVar[Callable[[MetadataTransaction], None] | None] = ContextVar(
    "p3_business_write_guard", default=None
)


class StorageTransaction:
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

    def rows_after(
        self, table: str, cursor: str = "", *, limit: int = 100
    ) -> list[tuple[str, Any]]:
        self.check()
        if not 1 <= limit <= 1000:
            raise ValueError("invalid page limit")
        return sorted((k, v) for k, v in self.rows(table) if k > cursor)[:limit]

    def pending_intent_rows(self, kind: str, *, limit: int = 100) -> list[tuple[str, Any]]:
        self.check()
        if kind not in {"start", "control"} or not 1 <= limit <= 1000:
            raise ValueError("invalid intent kind or batch limit")
        return sorted(
            (key, row)
            for key, row in self.rows(f"temporal_{kind}_intents")
            if row.get("state") == "pending"
        )[:limit]

    def active_task_rows(self, *, include_attention: bool = False) -> list[tuple[str, Any]]:
        states = {"pending", "running", "retry_wait", "recovery_wait"}
        if include_attention:
            states.add("attention_required")
        return sorted(
            (key, row)
            for key, row in self.rows("tasks")
            if row.get("record", {}).get("state") in states
        )

    def pending_delivery_rows(self, *, include_attention: bool = False) -> list[tuple[str, Any]]:
        states = {"pending", "sent", "retry_wait"}
        if include_attention:
            states.add("attention_required")
        return sorted(
            (key, row) for key, row in self.rows("deliveries") if row.get("state") in states
        )

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


def native(tx: Any) -> MetadataTransaction:
    if not isinstance(tx, MetadataTransaction):
        raise FoundationError(
            ErrorCode.CONTRACT_VIOLATION, "foundation requires its shared transaction"
        )
    tx.check()
    return tx
