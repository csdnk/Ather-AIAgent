"""Real local filesystem cache copies; folders are not distinct physical storage tiers.

Independent SQLite action journal and per-action receipts preserve evidence across
P3 restarts. Authoritative text remains in Remember. No network or physical-tier SLA.
"""

from __future__ import annotations

import json
import os
import secrets
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from aether_agent_memory.operate.contracts.models import (
    ActionIntent,
    ExecutionFeedback,
    PlacementObservation,
    ReadProof,
    ResourceSnapshot,
    Tier,
)
from aether_agent_memory.remember.contracts.models import MemoryRef, MemorySnapshot
from aether_agent_memory.runtime.contracts.models import TrustedContext
from aether_agent_memory.runtime.foundation.common import encode, fingerprint, now
from aether_agent_memory.runtime.foundation.requests import text_hash


class LocalCacheExecutor:
    provider_id = "local_filesystem_cache"
    mode = "real"

    def __init__(self, root: str | Path, capacity_bytes: int = 8 * 1024 * 1024) -> None:
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.capacity = capacity_bytes
        self.drop_next_response = False
        for tier in Tier:
            (self.root / tier.value).mkdir(exist_ok=True)
        (self.root / "receipts").mkdir(exist_ok=True)
        with self.db() as db:
            db.executescript(
                "CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,value TEXT); "
                "CREATE TABLE IF NOT EXISTS copies(key TEXT PRIMARY KEY, memory TEXT, "
                "tier TEXT, content_hash TEXT); CREATE TABLE IF NOT EXISTS actions("
                "id TEXT PRIMARY KEY,intent TEXT,feedback TEXT); "
                "CREATE TABLE IF NOT EXISTS tombstones(id TEXT PRIMARY KEY,version INTEGER);"
            )
            db.execute(
                "INSERT OR IGNORE INTO meta VALUES ('instance', ?)", (secrets.token_hex(16),)
            )
            db.execute("INSERT OR IGNORE INTO meta VALUES ('epoch', '0')")
            self.instance_id = db.execute("SELECT value FROM meta WHERE key='instance'").fetchone()[
                0
            ]

    @contextmanager
    def db(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.root / "executor.db", timeout=5)
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA synchronous=FULL")
        try:
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def key(memory: MemoryRef) -> str:
        return fingerprint(memory.model_dump(mode="json"))

    def path(self, memory: MemoryRef, tier: Tier) -> Path:
        return self.root / tier.value / (self.key(memory) + ".blob")

    def bytes_used(self) -> int:
        return sum(
            path.stat().st_size for tier in Tier for path in (self.root / tier.value).glob("*.blob")
        )

    @staticmethod
    def write_atomic(path: Path, data: bytes) -> None:
        temporary = path.with_suffix("." + secrets.token_hex(8) + ".tmp")
        try:
            with temporary.open("xb") as file:
                file.write(data)
                file.flush()
                os.fsync(file.fileno())
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)

    def ensure(self, memory: MemorySnapshot) -> None:
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            tombstone = db.execute(
                "SELECT version FROM tombstones WHERE id=?", (memory.ref.memory_id,)
            ).fetchone()
            if tombstone and memory.ref.version <= tombstone[0]:
                raise ValueError("deleted cache version is fenced")
            existing = db.execute(
                "SELECT tier,content_hash FROM copies WHERE key=?", (self.key(memory.ref),)
            ).fetchone()
            if existing:
                path = self.path(memory.ref, Tier(existing[0]))
                if (
                    not path.exists()
                    or text_hash(path.read_bytes().decode("utf-8")) != memory.content_hash
                ):
                    raise ValueError("existing cache copy is not readable or hash differs")
                return
            content = memory.content.encode("utf-8")
            if self.bytes_used() + len(content) > self.capacity:
                raise ValueError("local cache capacity exhausted")
            self.write_atomic(self.path(memory.ref, Tier.COLD), content)
            db.execute(
                "INSERT INTO copies VALUES (?,?,?,?)",
                (
                    self.key(memory.ref),
                    memory.ref.model_dump_json(),
                    Tier.COLD,
                    memory.content_hash,
                ),
            )

    async def resources(self, ctx: TrustedContext) -> ResourceSnapshot:
        with self.db() as db:
            epoch = int(db.execute("SELECT value FROM meta WHERE key='epoch'").fetchone()[0])
        return ResourceSnapshot(
            provider_id=self.provider_id,
            provider_instance_id=self.instance_id,
            epoch=epoch,
            available_bytes=max(0, self.capacity - self.bytes_used()),
            observed_at=now(),
            supported_moves=("promote", "demote"),
        )

    async def observe(
        self, ctx: TrustedContext, memory: MemoryRef, representation_id: str
    ) -> PlacementObservation:
        with self.db() as db:
            row = db.execute(
                "SELECT tier,content_hash FROM copies WHERE key=?", (self.key(memory),)
            ).fetchone()
            epoch = int(db.execute("SELECT value FROM meta WHERE key='epoch'").fetchone()[0])
        if not row:
            raise ValueError("cache copy not present")
        path = self.path(memory, Tier(row[0]))
        readable = path.exists() and text_hash(path.read_bytes().decode("utf-8")) == row[1]
        return PlacementObservation(
            memory=memory,
            representation_id=representation_id,
            provider_instance_id=self.instance_id,
            tier=row[0],
            epoch=epoch,
            readable=readable,
            content_hash=row[1],
            observed_at=now(),
        )

    async def submit(self, ctx: TrustedContext, intent: ActionIntent) -> ExecutionFeedback:
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            previous = db.execute(
                "SELECT intent FROM actions WHERE id=?", (intent.action_id,)
            ).fetchone()
            if previous and json.loads(previous[0]) != intent.model_dump(mode="json"):
                raise ValueError("action ID cannot change its intent")
            if not previous:
                db.execute(
                    "INSERT INTO actions VALUES (?,?,NULL)",
                    (intent.action_id, intent.model_dump_json()),
                )
        if previous:
            return await self.query(ctx, intent.action_id)
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            epoch = int(db.execute("SELECT value FROM meta WHERE key='epoch'").fetchone()[0])
            tombstone = db.execute(
                "SELECT version FROM tombstones WHERE id=?", (intent.decision.memory.memory_id,)
            ).fetchone()
            if intent.provider_instance_id != self.instance_id or intent.expected_epoch != epoch:
                feedback = ExecutionFeedback(
                    action_id=intent.action_id,
                    provider_instance_id=self.instance_id,
                    state="failed",
                    observed_at=now(),
                    reason="instance or epoch mismatch",
                )
            elif tombstone and intent.decision.memory.version <= tombstone[0]:
                feedback = ExecutionFeedback(
                    action_id=intent.action_id,
                    provider_instance_id=self.instance_id,
                    state="failed",
                    observed_at=now(),
                    reason="deleted version fenced",
                )
            else:
                source = self.path(intent.decision.memory, intent.decision.current_tier)
                data = source.read_bytes()
                target = self.path(intent.decision.memory, intent.decision.target_tier)
                if (
                    text_hash(data.decode("utf-8")) != intent.content_hash
                    or self.bytes_used() + len(data) > self.capacity
                ):
                    feedback = ExecutionFeedback(
                        action_id=intent.action_id,
                        provider_instance_id=self.instance_id,
                        state="failed",
                        observed_at=now(),
                        reason="hash or capacity check failed",
                    )
                else:
                    self.write_atomic(target, data)
                    if target.read_bytes() != data:
                        raise ValueError("target read verification failed")
                    observation = PlacementObservation(
                        memory=intent.decision.memory,
                        representation_id=intent.representation_id,
                        provider_instance_id=self.instance_id,
                        tier=intent.decision.target_tier,
                        epoch=epoch + 1,
                        readable=True,
                        content_hash=intent.content_hash,
                        observed_at=now(),
                    )
                    proof = ReadProof(
                        action_id=intent.action_id,
                        memory=intent.decision.memory,
                        provider_instance_id=self.instance_id,
                        content_hash=intent.content_hash,
                        readable=True,
                        verified_at=now(),
                        provider_mode="real",
                    )
                    feedback = ExecutionFeedback(
                        action_id=intent.action_id,
                        provider_instance_id=self.instance_id,
                        provider_operation_id=intent.action_id,
                        state="succeeded",
                        observed_at=now(),
                        observation=observation,
                        read_proof=proof,
                    )
                    # Action-specific durable evidence before journal finalization.
                    self.write_atomic(
                        self.root / "receipts" / (intent.action_id + ".json"),
                        encode(
                            {
                                "intent": intent.model_dump(mode="json"),
                                "feedback": feedback.model_dump(mode="json"),
                            }
                        ).encode(),
                    )
                    db.execute(
                        "UPDATE copies SET tier=? WHERE key=?",
                        (intent.decision.target_tier, self.key(intent.decision.memory)),
                    )
                    db.execute("UPDATE meta SET value=? WHERE key='epoch'", (str(epoch + 1),))
                    source.unlink(missing_ok=True)
            db.execute(
                "UPDATE actions SET feedback=? WHERE id=?",
                (feedback.model_dump_json(), intent.action_id),
            )
        if self.drop_next_response:
            self.drop_next_response = False
            raise TimeoutError("injected response loss after durable execution")
        return feedback

    async def query(self, ctx: TrustedContext, action_id: str) -> ExecutionFeedback:
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT intent,feedback FROM actions WHERE id=?", (action_id,)
            ).fetchone()
            if row is None:
                return ExecutionFeedback(
                    action_id=action_id,
                    provider_instance_id=self.instance_id,
                    state="not_found",
                    observed_at=now(),
                    reason="no action evidence; not proof of no effect",
                )
            intent = ActionIntent.model_validate_json(row[0])
            if row[1]:
                return ExecutionFeedback.model_validate_json(row[1])
            receipt = self.root / "receipts" / (action_id + ".json")
            if receipt.exists():
                saved = json.loads(receipt.read_text(encoding="utf-8"))
                path = self.path(intent.decision.memory, intent.decision.target_tier)
                if (
                    saved["intent"] == intent.model_dump(mode="json")
                    and path.exists()
                    and text_hash(path.read_bytes().decode("utf-8")) == intent.content_hash
                ):
                    feedback = ExecutionFeedback.model_validate(saved["feedback"])
                    if feedback.observation is None:
                        raise ValueError("receipt is missing placement evidence")
                    db.execute(
                        "UPDATE actions SET feedback=? WHERE id=?",
                        (feedback.model_dump_json(), action_id),
                    )
                    db.execute(
                        "UPDATE copies SET tier=? WHERE key=?",
                        (intent.decision.target_tier, self.key(intent.decision.memory)),
                    )
                    epoch = int(
                        db.execute("SELECT value FROM meta WHERE key='epoch'").fetchone()[0]
                    )
                    db.execute(
                        "UPDATE meta SET value=? WHERE key='epoch'",
                        (str(max(epoch, feedback.observation.epoch)),),
                    )
                    self.path(intent.decision.memory, intent.decision.current_tier).unlink(
                        missing_ok=True
                    )
                    return feedback
        return ExecutionFeedback(
            action_id=action_id,
            provider_instance_id=self.instance_id,
            state="unknown",
            observed_at=now(),
            reason="action started but no verifiable receipt",
        )

    async def verify_read(self, ctx: TrustedContext, intent: ActionIntent) -> ReadProof:
        path = self.path(intent.decision.memory, intent.decision.target_tier)
        if not path.exists() or text_hash(path.read_bytes().decode("utf-8")) != intent.content_hash:
            raise ValueError("target cannot be verified")
        return ReadProof(
            action_id=intent.action_id,
            memory=intent.decision.memory,
            provider_instance_id=self.instance_id,
            content_hash=intent.content_hash,
            readable=True,
            verified_at=now(),
            provider_mode="real",
        )

    def purge(self, memory: MemoryRef, *, permanent: bool) -> None:
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            if permanent:
                db.execute(
                    "INSERT INTO tombstones VALUES (?,?) ON CONFLICT(id) "
                    "DO UPDATE SET version=MAX(version,excluded.version)",
                    (memory.memory_id, memory.version),
                )
            for key, payload in db.execute("SELECT key,memory FROM copies").fetchall():
                ref = MemoryRef.model_validate_json(payload)
                if (
                    ref.memory_id == memory.memory_id
                    and ref.scope == memory.scope
                    and ref.version <= memory.version
                ):
                    for tier in Tier:
                        self.path(ref, tier).unlink(missing_ok=True)
                    db.execute("DELETE FROM copies WHERE key=?", (key,))
