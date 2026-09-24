"""Remember-owned retention statistics fed only by successful context packing."""

from datetime import datetime
from typing import Any

from aether_agent_memory.recall.contracts.models import AccessObserved
from aether_agent_memory.remember.contracts.models import (
    DeleteRequest,
    MemoryRef,
    MemorySnapshot,
    RetentionRequest,
)
from aether_agent_memory.runtime.contracts.models import (
    ErrorCode,
    EventEnvelope,
    Permission,
    RecordRef,
    TrustedContext,
)
from aether_agent_memory.runtime.contracts.ports import Transaction
from aether_agent_memory.runtime.foundation.common import FoundationError, fingerprint, later
from aether_agent_memory.runtime.foundation.requests import event_context, request_key
from aether_agent_memory.runtime.foundation.storage import SQLiteTransaction, native

from .decay import evaluate, initial, reinforce
from .service import memory_ref


def hours(timestamp: str) -> float:
    return datetime.fromisoformat(timestamp.replace("Z", "+00:00")).timestamp() / 3600


class Retention:
    def __init__(self, remember: Any) -> None:
        self.remember = remember

    def snapshot_in(self, tx: SQLiteTransaction, item: MemorySnapshot) -> dict[str, Any]:
        state = tx.read("remember_retention", self.remember.refkey(item.ref)) or initial(
            item.kind.value, hours(item.created_at)
        )
        return evaluate(state, hours(self.remember.identity.clock()), item.importance)

    def read(self, ctx: TrustedContext, memory_id: str) -> dict[str, Any]:
        with self.remember.uow.transaction() as tx:
            item = self.remember.current(tx, memory_id)
            self.remember.identity.authorize(tx, ctx, Permission.READ, memory_ref(item.ref))
            self.sync_packed_in(tx, item.ref)
            enrollment = tx.read("remember_retention_enrollment", memory_id) or {}
            return {
                "memory": item.ref.model_dump(mode="json"),
                "state": item.status,
                "retention": self.snapshot_in(tx, item),
                "policy": {k: v for k, v in enrollment.items() if k != "context"},
            }

    def configure(
        self, ctx: TrustedContext, memory_id: str, request: RetentionRequest
    ) -> dict[str, Any]:
        with self.remember.uow.transaction() as tx:
            item = self.remember.current(tx, memory_id)
            self.remember.identity.authorize(tx, ctx, Permission.WRITE, memory_ref(item.ref))
            key = request_key(ctx, "retention_" + memory_id)
            signature = fingerprint(
                {
                    **request.model_dump(mode="json"),
                    "expires_at_supplied": "expires_at" in request.model_fields_set,
                }
            )
            previous = tx.read("remember_operations", key)
            legacy_signature = fingerprint(
                request.model_dump(
                    mode="json",
                    include={
                        "expected_version",
                        "expected_object_revision",
                        "enabled",
                        "completed",
                        "archive_after_idle_hours",
                        "reason",
                    },
                )
            )
            legacy_replay = (
                previous is not None
                and previous["signature"] == legacy_signature
                and "expires_at" not in request.model_fields_set
                and request.delete_after_archive_hours is None
                and request.delete_below_strength == 0.05
                and not request.legal_hold
            )
            if previous and previous["signature"] != signature and not legacy_replay:
                tx.abort(ErrorCode.IDEMPOTENCY_CONFLICT, "retention request intent changed")
            if previous:
                return dict(previous["result"])
            if (
                item.ref.version != request.expected_version
                or item.object_revision != request.expected_object_revision
            ):
                raise FoundationError(
                    ErrorCode.VERSION_CONFLICT, "memory changed before retention enrollment"
                )
            if (
                item.status not in {"active", "archived", "expired"}
                or item.kind == "working"
                and request.enabled
                and not request.completed
            ):
                raise FoundationError(
                    ErrorCode.INVALID_ARGUMENT,
                    "retention needs a live record and completed Working",
                )
            if request.delete_after_archive_hours is not None:
                self.remember.identity.authorize(tx, ctx, Permission.DELETE, memory_ref(item.ref))
            updates: dict[str, Any] = {}
            if "expires_at" in request.model_fields_set:
                if item.status == "expired":
                    raise FoundationError(
                        ErrorCode.INVALID_ARGUMENT, "expired content needs a new version"
                    )
                if (
                    request.expires_at is not None
                    and request.expires_at <= self.remember.identity.clock()
                ):
                    raise FoundationError(
                        ErrorCode.INVALID_ARGUMENT, "expiry must be in the future"
                    )
                updates["expires_at"] = request.expires_at
            # Enrolling a policy is a CAS mutation too; concurrent policy updates
            # must not both succeed against the same object revision.
            updated = self.remember.change(tx, item, **updates)
            if updates:
                self.remember.emit(tx, ctx, updated, "processing")
            previous_policy = tx.read("remember_retention_enrollment", memory_id) or {}
            row = {
                "version": item.ref.version,
                "idle_hours": request.archive_after_idle_hours,
                "context": ctx.model_dump(mode="json"),
                "reason": request.reason,
                "enabled": request.enabled,
                "completed": request.completed,
                "delete_after_archive_hours": request.delete_after_archive_hours,
                "delete_below_strength": request.delete_below_strength,
                "legal_hold": request.legal_hold,
                "inactive_since": previous_policy.get("inactive_since")
                if previous_policy.get("version") == item.ref.version
                else None,
                "object_revision": updated.object_revision,
            }
            if item.status in {"archived", "expired"} and not row["inactive_since"]:
                row["inactive_since"] = self.remember.identity.clock()
            tx.write("remember_retention_enrollment", memory_id, row)
            result = {k: v for k, v in row.items() if k != "context"}
            tx.write(
                "remember_operations",
                key,
                {"signature": signature, "result": result},
            )
            return result

    def version_started_in(self, tx: SQLiteTransaction, item: MemorySnapshot) -> None:
        key = self.remember.refkey(item.ref)
        if tx.read("remember_retention", key) is None:
            tx.write(
                "remember_retention",
                key,
                initial(item.kind.value, hours(self.remember.identity.clock())),
            )

    def activated_in(self, tx: SQLiteTransaction, item: MemorySnapshot) -> None:
        state = initial(item.kind.value, hours(self.remember.identity.clock()))
        tx.write("remember_retention", self.remember.refkey(item.ref), state)
        prior = tx.read("remember_retention_enrollment", item.ref.memory_id)
        if prior:
            tx.write(
                "remember_retention_enrollment",
                item.ref.memory_id,
                {**prior, "enabled": False, "inactive_since": None},
            )

    def inactive_in(self, tx: SQLiteTransaction, item: MemorySnapshot) -> None:
        row = tx.read("remember_retention_enrollment", item.ref.memory_id)
        if row and row["version"] == item.ref.version and not row.get("inactive_since"):
            tx.write(
                "remember_retention_enrollment",
                item.ref.memory_id,
                {**row, "inactive_since": self.remember.identity.clock()},
            )

    def has_dependents(self, tx: SQLiteTransaction, item: MemorySnapshot) -> bool:
        """Automatic disposal must not withdraw evidence from a retained memory."""
        for memory_id, pointer in tx.rows("remember_current"):
            if memory_id == item.ref.memory_id:
                continue
            other: dict[str, Any] | None = tx.get(RecordRef.model_validate(pointer))
            if not other or other["status"] == "deleted":
                continue
            relation = tx.read("remember_relations", memory_id) or {}
            if any(
                r["memory_id"] == item.ref.memory_id
                for r in (*relation.get("derived_from", []), *relation.get("working_refs", []))
            ):
                return True
            if item.kind == "working" and (
                {s.source_id for s in item.sources} & {s["source_id"] for s in other["sources"]}
            ):
                return True
        for _, conflict in tx.rows("remember_conflicts"):
            ids = {r["memory_id"] for r in conflict["members"]}
            if item.ref.memory_id in ids and any(
                mid != item.ref.memory_id and self.remember.current(tx, mid).status != "deleted"
                for mid in ids
            ):
                return True
        return False

    def periodic(self) -> int:
        with self.remember.uow.transaction() as tx:
            rows = tx.rows("remember_retention_enrollment")
        count = 0
        for memory_id, _ in rows:
            try:
                with self.remember.uow.transaction() as tx:
                    # Re-read after acquiring the transaction: a concurrent hold,
                    # disabled policy or changed principal must take effect now.
                    row = tx.read("remember_retention_enrollment", memory_id)
                    ctx = TrustedContext.model_validate(row["context"]).model_copy(
                        update={
                            "deadline_at": later(
                                self.remember.identity.clock(), self.remember.processing_seconds
                            )
                        }
                    )
                    self.remember.identity.revalidate(tx, ctx)
                    item = self.remember.current(tx, memory_id)
                    if item.ref.version != row["version"] or item.status not in {
                        "active",
                        "archived",
                        "expired",
                    }:
                        continue
                    self.remember.identity.authorize(
                        tx, ctx, Permission.WRITE, memory_ref(item.ref)
                    )
                    self.sync_packed_in(tx, item.ref)
                    if (
                        item.status != "expired"
                        and item.expires_at
                        and item.expires_at <= self.remember.identity.clock()
                    ):
                        updated = self.remember.change(
                            tx, item, status="expired", projection_state="stale"
                        )
                        self.remember.enqueue(tx, ctx, updated, "remember.cleanup")
                        self.remember.emit(tx, ctx, updated, "expired")
                        count += 1
                        continue
                    if not row.get("enabled", False) or row.get("legal_hold", False):
                        continue
                    strength = self.snapshot_in(tx, item)
                    if strength["protected"]:
                        continue
                    if item.status in {"archived", "expired"}:
                        grace = row.get("delete_after_archive_hours")
                        since = row.get("inactive_since")
                        if (
                            grace is None
                            or since is None
                            or hours(self.remember.identity.clock()) - hours(since) < grace
                            or strength["strength"] >= row.get("delete_below_strength", 0.05)
                            or self.has_dependents(tx, item)
                        ):
                            continue
                        self.remember.identity.authorize(
                            tx, ctx, Permission.DELETE, memory_ref(item.ref)
                        )
                        ctx = ctx.model_copy(
                            update={
                                "operation_id": fingerprint(
                                    [
                                        "decay_delete",
                                        memory_id,
                                        item.ref.version,
                                        item.object_revision,
                                    ]
                                )
                            }
                        )
                        # delete_in() shares this UOW, preserving lifecycle preconditions,
                        # CAS, invalidation, cleanup task and Outbox atomically.
                        self.remember.delete_in(
                            tx,
                            ctx,
                            memory_id,
                            DeleteRequest(
                                expected_revision=item.object_revision, reason=row["reason"]
                            ),
                        )
                        tx.write(
                            "remember_retention_audit",
                            ctx.operation_id,
                            {
                                "memory": item.ref.model_dump(mode="json"),
                                "previous_state": item.status,
                                "state": "deleted",
                                "reason": row["reason"],
                                "retention": strength,
                                "physical_erasure": False,
                            },
                        )
                        count += 1
                        continue
                    if (
                        self.remember.final_guard(tx, ctx, (item.ref,), "recall").items[0].decision
                        != "allowed"
                    ):
                        continue
                    pending = tx.read("remember_pending", memory_id)
                    if (
                        strength["protected"]
                        or strength["band"] != "dormant"
                        or strength["elapsed_hours"] < row["idle_hours"]
                        or item.kind == "working"
                        and (not pending or pending["state"] != "processed")
                    ):
                        continue
                    ctx = ctx.model_copy(
                        update={
                            "operation_id": fingerprint(
                                ["decay_archive", memory_id, item.ref.version, item.object_revision]
                            )
                        }
                    )
                    # Keep the check and lifecycle CAS in the same serialized UOW
                    # so a packed-use event cannot race the archive preconditions.
                    self.remember.identity.authorize(
                        tx, ctx, Permission.WRITE, memory_ref(item.ref)
                    )
                    updated = self.remember.change(
                        tx,
                        item,
                        status="archived",
                        projection_state="not_required" if item.kind == "working" else "stale",
                    )
                    self.remember.emit(tx, ctx, updated, "archived")
                    tx.write(
                        "remember_retention_audit",
                        ctx.operation_id,
                        {
                            "memory": item.ref.model_dump(mode="json"),
                            "previous_state": "active",
                            "state": "archived",
                            "reason": row["reason"],
                            "retention": strength,
                        },
                    )
                    count += 1
            except FoundationError:
                # Revocation, deletion or conflicting changes cannot be bypassed
                # using privileged maintenance authority.
                continue
        return count

    def sync_packed_in(self, tx: SQLiteTransaction, ref: MemoryRef) -> None:
        """Reconcile committed feedback before a decay decision, even if delivery lags.

        SQLite simulation scans the durable outbox; a distributed provider should
        expose an indexed per-memory feedback watermark with the same atomicity.
        No feedback is inferred from internal reads or from a proposed context.
        """
        for _, row in sorted(
            tx.rows("outbox"), key=lambda pair: (pair[1]["event"]["occurred_at"], pair[0])
        ):
            raw = row["event"]
            if (
                raw["event_type"] != "recall.access"
                or raw["payload"].get("stage") != "packed"
                or raw["payload"].get("memory") != ref.model_dump(mode="json")
            ):
                continue
            try:
                self.consume(tx, EventEnvelope.model_validate(raw))
            except FoundationError as exc:
                if exc.code not in {ErrorCode.FORBIDDEN, ErrorCode.UNAUTHENTICATED}:
                    raise

    def consume(self, tx: Transaction, event: EventEnvelope) -> None:
        access = AccessObserved.model_validate(event.payload)
        if access.stage != "packed" or access.outcome != "succeeded":
            return
        sql = native(tx)
        ctx = event_context(sql, event, self.remember.identity.clock())
        if (
            self.remember.final_guard(sql, ctx, (access.memory,), "recall").items[0].decision
            != "allowed"
        ):
            return
        self.record_packed(sql, ctx, access.memory, access.access_key, event.occurred_at)

    def record_packed(
        self,
        sql: SQLiteTransaction,
        ctx: TrustedContext,
        memory: MemoryRef,
        access_key: str,
        occurred_at: str,
    ) -> None:
        if self.remember.final_guard(sql, ctx, (memory,), "recall").items[0].decision != "allowed":
            return
        key = fingerprint(["reinforce", access_key, memory.model_dump(mode="json")])
        if sql.read("remember_reinforcement_receipts", key):
            return
        item = self.remember.current(sql, memory.memory_id)
        state = self.snapshot_in(sql, item)
        changed = reinforce(state, hours(occurred_at))
        sql.write(
            "remember_reinforcement_receipts",
            key,
            {"reinforced": changed, "event_id": access_key},
        )
        if changed:
            sql.write("remember_retention", self.remember.refkey(item.ref), state)
