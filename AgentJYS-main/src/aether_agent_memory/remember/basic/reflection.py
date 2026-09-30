"""Opt-in, scope-bound scheduling of evidenced episodic reflection.

Frequency/importance trigger a review, never certify a semantic fact. The existing
distill task validates evidence, deduplicates and commits candidates normally.
"""

from typing import Any

from aether_agent_memory.remember.contracts.models import ReflectionRequest
from aether_agent_memory.runtime.contracts.models import (
    ErrorCode,
    Flow,
    Permission,
    RecordRef,
    ScopeSelector,
    TrustedContext,
)
from aether_agent_memory.runtime.foundation.common import FoundationError, fingerprint, later
from aether_agent_memory.runtime.foundation.requests import select_scope
from aether_agent_memory.runtime.foundation.storage import SQLiteTransaction

from .service import memory_ref


class Reflection:
    def __init__(self, remember: Any) -> None:
        self.remember = remember

    def read(self, ctx: TrustedContext, selection: ScopeSelector) -> dict[str, Any]:
        scope = select_scope(ctx, selection)
        key = fingerprint(scope.model_dump(mode="json"))
        with self.remember.uow.transaction() as tx:
            self.remember.identity.authorize(
                tx,
                ctx,
                Permission.READ,
                RecordRef(
                    owner=Flow.REMEMBER, object_type="reflection", object_id=key, scope=scope
                ),
            )
            row = tx.read("remember_reflection_policies", key)
            return (
                {k: v for k, v in row.items() if k != "context"}
                if row
                else {"status": "not_enrolled", "revision": 0}
            )

    def configure(self, ctx: TrustedContext, request: ReflectionRequest) -> dict[str, Any]:
        scope = select_scope(ctx, request.selection)
        key = fingerprint(scope.model_dump(mode="json"))
        with self.remember.uow.transaction() as tx:
            self.remember.identity.authorize(
                tx,
                ctx,
                Permission.WRITE,
                RecordRef(
                    owner=Flow.REMEMBER, object_type="reflection", object_id=key, scope=scope
                ),
            )
            op, replay = self.remember.replay(tx, ctx, "reflection_" + key, request)
            if replay:
                return dict(replay["result"])
            prior = tx.read("remember_reflection_policies", key) or {}
            if prior.get("revision", 0) != request.expected_revision:
                raise FoundationError(ErrorCode.VERSION_CONFLICT, "reflection policy changed")
            row = {
                "revision": request.expected_revision + 1,
                "scope": scope.model_dump(mode="json"),
                "policy": request.model_dump(mode="json"),
                "context": ctx.model_dump(mode="json"),
                "next_check_at": self.remember.identity.clock(),
                "last_task_id": prior.get("last_task_id"),
                "last_inputs": prior.get("last_inputs", []),
                "reviewed": prior.get("reviewed", []),
                "status": "enrolled" if request.enabled else "disabled",
            }
            tx.write("remember_reflection_policies", key, row)
            result = {k: v for k, v in row.items() if k != "context"}
            tx.write(
                "remember_operations",
                op,
                {"signature": fingerprint(request.model_dump(mode="json")), "result": result},
            )
            return result

    def periodic(self) -> int:
        with self.remember.uow.transaction() as tx:
            keys = [key for key, _ in tx.rows("remember_reflection_policies")]
        count = 0
        for key in keys:
            try:
                with self.remember.uow.transaction() as tx:
                    count += self.periodic_item(tx, key)
            except FoundationError:
                continue
        return count

    def periodic_item(self, tx: SQLiteTransaction, key: str) -> int:
        count = 0
        row = tx.read("remember_reflection_policies", key)
        policy = ReflectionRequest.model_validate(row["policy"])
        now = self.remember.identity.clock()
        if not policy.enabled or row["next_check_at"] > now:
            return count
        ctx = TrustedContext.model_validate(row["context"]).model_copy(
            update={"deadline_at": later(now, self.remember.processing_seconds)}
        )
        self.remember.identity.revalidate(tx, ctx)
        row = {**row, "next_check_at": later(now, int(policy.period_hours * 3600))}
        task_row = tx.read("tasks", row["last_task_id"]) if row["last_task_id"] else None
        if task_row:
            state = task_row["record"]["state"]
            if state not in {"succeeded", "failed", "cancelled", "attention_required"}:
                tx.write("remember_reflection_policies", key, {**row, "status": "running"})
                return count
            # Failed evidence windows need explicit task recovery.
            # Never manufacture unlimited replacement jobs.
            if state != "succeeded":
                tx.write(
                    "remember_reflection_policies",
                    key,
                    {**row, "status": "needs_recovery"},
                )
                return count
            row["reviewed"] = sorted(set(row["reviewed"]) | set(row["last_inputs"]))
            row["last_task_id"] = None
        if not hasattr(self.remember.extraction, "review_episodes"):
            tx.write(
                "remember_reflection_policies",
                key,
                {**row, "status": "provider_unavailable"},
            )
            return count
        eligible = []
        reviewed = set(row["reviewed"])
        for memory_id, pointer in tx.rows("remember_current"):
            ref = RecordRef.model_validate(pointer)
            raw = tx.get(ref)
            if (
                raw is None
                or raw["kind"] != "episodic"
                or ref.scope.model_dump(mode="json") != row["scope"]
            ):
                continue
            stamp = fingerprint([raw["ref"], raw["sources"]])
            if stamp in reviewed:
                continue
            item = self.remember.current(tx, memory_id)
            if not self.remember.identity.permits(tx, ctx, Permission.WRITE, memory_ref(item.ref)):
                continue
            if (
                self.remember.final_guard(tx, ctx, (item.ref,), "recall").items[0].decision
                == "allowed"
            ):
                eligible.append((item, stamp))
        eligible.sort(key=lambda x: (-x[0].importance, x[0].created_at, x[0].ref.memory_id))
        # Choose distinct source sets before bounding the batch: a
        # backlog of copied events must not starve new evidence.
        distinct = []
        seen_hashes: set[str] = set()
        for item, stamp in eligible:
            hashes = {s.content_hash for s in item.sources}
            if hashes and not hashes & seen_hashes:
                distinct.append((item, stamp))
                seen_hashes.update(hashes)
        eligible = distinct[: min(policy.max_episodes, self.remember.policy.consolidation_messages)]
        # Repeated copies of one source do not count as independent observations.
        evidence_sets = {tuple(sorted(s.content_hash for s in i.sources)) for i, _ in eligible}
        emphasized = any(i.importance >= policy.importance_threshold for i, _ in eligible)
        reused = any(
            self.remember.retention.snapshot_in(tx, i)["reinforcements"]
            >= policy.min_reinforcements
            for i, _ in eligible
        )
        if not eligible or not (len(evidence_sets) >= policy.min_episodes or emphasized or reused):
            tx.write(
                "remember_reflection_policies",
                key,
                {**row, "status": "waiting_evidence"},
            )
            return count
        stamps = [stamp for _, stamp in eligible]
        ctx = ctx.model_copy(
            update={"operation_id": fingerprint(["reflection", key, row["revision"], stamps])}
        )
        task_id = self.remember.distill_in(tx, ctx, tuple(i.ref for i, _ in eligible))
        tx.write(
            "remember_reflection_policies",
            key,
            {
                **row,
                "last_task_id": task_id,
                "last_inputs": stamps,
                "status": "scheduled",
            },
        )
        count += 1
        return count
