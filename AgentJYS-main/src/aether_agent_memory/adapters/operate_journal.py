"""Durable local control journal; intent is committed before remote submission."""

from __future__ import annotations

import asyncio
import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from aether_agent_memory.core.scope import Scope
from aether_agent_memory.operate.models import ActionState, ExecutionMode, TierAction


class SQLiteActionJournal:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS control_actions "
                "(id TEXT PRIMARY KEY, fence TEXT, state TEXT, payload TEXT, scope TEXT)"
            )
            db.execute("CREATE INDEX IF NOT EXISTS control_pending ON control_actions(scope,state)")
            db.execute(
                "CREATE TABLE IF NOT EXISTS control_active "
                "(fence TEXT PRIMARY KEY, action_id TEXT NOT NULL)"
            )

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.path, timeout=5)
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA synchronous=FULL")
        try:
            with db:
                yield db
        finally:
            db.close()

    async def get(self, action_id: str) -> TierAction | None:
        return await asyncio.to_thread(self._get, action_id)

    def _get(self, action_id: str) -> TierAction | None:
        with self._connect() as db:
            row = db.execute(
                "SELECT payload FROM control_actions WHERE id=?", (action_id,)
            ).fetchone()
            return TierAction.model_validate_json(row[0]) if row else None

    async def reserve(self, action: TierAction) -> TierAction:
        return await asyncio.to_thread(self._reserve, action)

    def _reserve(self, action: TierAction) -> TierAction:
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT payload FROM control_actions WHERE id=?", (action.action_id,)
            ).fetchone()
            if row:
                previous = TierAction.model_validate_json(row[0])
                if previous.intent() != action.intent():
                    raise ValueError("action_id already belongs to a different intent")
                return previous
            if action.execution_mode == ExecutionMode.LIVE:
                active = db.execute(
                    "SELECT a.payload FROM control_active x JOIN control_actions a "
                    "ON a.id=x.action_id WHERE x.fence=?",
                    (action.fence_key,),
                ).fetchone()
                if active:
                    return TierAction.model_validate_json(active[0])
                db.execute(
                    "INSERT INTO control_active VALUES (?,?)", (action.fence_key, action.action_id)
                )
            db.execute(
                "INSERT INTO control_actions VALUES (?,?,?,?,?)",
                (
                    action.action_id,
                    action.fence_key,
                    action.state.value,
                    action.model_dump_json(),
                    json.dumps(action.scope.as_dict(), sort_keys=True),
                ),
            )
            return action.model_copy(deep=True)

    async def transition(self, action: TierAction, expected: set[ActionState]) -> bool:
        return await asyncio.to_thread(self._transition, action, expected)

    def _transition(self, action: TierAction, expected: set[ActionState]) -> bool:
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT state,payload FROM control_actions WHERE id=?", (action.action_id,)
            ).fetchone()
            if not row or row[0] not in expected:
                return False
            previous = TierAction.model_validate_json(row[1])
            if previous.intent() != action.intent():
                raise ValueError("action_id intent is immutable")
            db.execute(
                "UPDATE control_actions SET state=?,payload=? WHERE id=?",
                (action.state.value, action.model_dump_json(), action.action_id),
            )
            if action.state in {ActionState.SUCCEEDED, ActionState.FAILED}:
                db.execute(
                    "DELETE FROM control_active WHERE fence=? AND action_id=?",
                    (action.fence_key, action.action_id),
                )
            return True

    async def close(self) -> None:
        pass

    async def pending(self, scope: Scope, *, limit: int = 100) -> list[TierAction]:
        if limit < 1:
            raise ValueError("journal limit must be positive")

        def read() -> list[TierAction]:
            with self._connect() as db:
                rows = db.execute(
                    "SELECT a.payload FROM control_actions a WHERE a.scope=? "
                    "AND a.state IN ('generated','submitted','unknown') "
                    "AND EXISTS (SELECT 1 FROM control_active x "
                    "WHERE x.fence=a.fence AND x.action_id=a.id) LIMIT ?",
                    (json.dumps(scope.as_dict(), sort_keys=True), limit),
                ).fetchall()
                return [TierAction.model_validate_json(row[0]) for row in rows]

        return await asyncio.to_thread(read)
