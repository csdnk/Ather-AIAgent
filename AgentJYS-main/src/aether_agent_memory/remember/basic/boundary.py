"""Remember implementations of the existing generation Recall contracts."""

from typing import Any, Literal

from aether_agent_memory.remember.contracts.foundation import (
    CandidateQualificationResult,
    CandidateQualificationTarget,
    ContextGuardRequest,
    FullBodyReadResult,
    GuardStamp,
    MemoryRelationSnapshot,
    ProjectionManifest,
)
from aether_agent_memory.remember.contracts.models import MemoryRef
from aether_agent_memory.runtime.contracts.models import ErrorCode, Permission, TrustedContext
from aether_agent_memory.runtime.contracts.ports import Transaction
from aether_agent_memory.runtime.foundation.common import FoundationError
from aether_agent_memory.runtime.foundation.storage import SQLiteTransaction, native

from .records import required_record
from .service import memory_ref


class RememberBoundary:
    def __init__(self, remember: Any) -> None:
        self.remember = remember

    @staticmethod
    def distinct(refs: tuple[MemoryRef, ...]) -> None:
        if len({r.model_dump_json() for r in refs}) != len(refs):
            raise FoundationError(ErrorCode.INVALID_ARGUMENT, "duplicate memory references")

    def guard_in(self, tx: SQLiteTransaction, ctx: TrustedContext, ref: MemoryRef) -> GuardStamp:
        owner = self.remember
        owner.identity.authorize(tx, ctx, Permission.READ, memory_ref(ref))
        if owner.final_guard(tx, ctx, (ref,), "recall").items[0].decision != "allowed":
            tx.abort(ErrorCode.RESULT_INVALIDATED, "memory no longer eligible")
        raw = required_record(tx, memory_ref(ref, versioned=True))
        return GuardStamp(
            memory=ref,
            object_revision=raw["object_revision"],
            relations_revision=raw.get("relations_revision", raw["revision"]),
            authorization_epoch=ctx.principal.auth_epoch,
            body_hash=raw["body_location"]["content_hash"]
            if "body_location" in raw
            else raw["content_hash"],
            checked_at=owner.identity.clock(),
        )

    async def qualify(
        self,
        ctx: TrustedContext,
        targets: tuple[CandidateQualificationTarget, ...],
        purpose: Literal["recall", "extraction"],
    ) -> tuple[CandidateQualificationResult, ...]:
        if (
            not targets
            or len({t.model_dump_json() for t in targets}) != len(targets)
            or purpose not in {"recall", "extraction"}
        ):
            raise FoundationError(ErrorCode.INVALID_ARGUMENT, "invalid qualification batch")
        owner = self.remember
        results = []
        with owner.uow.transaction() as tx:
            owner.identity.revalidate(tx, ctx)
            for target in targets:
                valid = owner.final_guard(tx, ctx, (target.memory,), "recall").items[0]
                manifest = guard = None
                reason = valid.reason
                if valid.decision == "allowed":
                    raw = tx.read("remember_manifests", owner.refkey(target.memory))
                    record = required_record(tx, memory_ref(target.memory, versioned=True))
                    candidate = ProjectionManifest.model_validate(raw) if raw else None
                    reason = "unpublished_or_stale_chunk"
                    if candidate is not None and (
                        candidate.state == "ready"
                        and record["projection_state"] == "ready"
                        and candidate.memory == target.memory
                        and candidate.generation == target.generation
                        and candidate.model_space == target.model_space
                        and candidate.body_hash == target.body_hash
                        and any(
                            c.verified
                            and c.chunk_index == target.chunk_index
                            and c.vector_id == target.vector_id
                            and c.input_hash == target.input_hash
                            for c in candidate.chunks
                        )
                    ):
                        current = self.guard_in(tx, ctx, target.memory)
                        if current.body_hash == candidate.body_hash:
                            manifest, guard, reason = candidate, current, "verified"
                results.append(
                    CandidateQualificationResult(
                        target=target,
                        decision="allowed" if guard else "excluded",
                        reason_code=reason,
                        manifest=manifest,
                        guard=guard,
                    )
                )
            owner.identity.revalidate(tx, ctx)
        return tuple(results)

    def relations(self, ctx: TrustedContext, refs: tuple[MemoryRef, ...]) -> MemoryRelationSnapshot:
        self.distinct(refs)
        with self.remember.uow.transaction() as tx:
            self.remember.identity.revalidate(tx, ctx)
            guards = tuple(self.guard_in(tx, ctx, ref) for ref in refs)
            conflicts = self.remember.conflict_groups_in(tx, ctx, refs)
            return MemoryRelationSnapshot(guards=guards, conflicts=conflicts)

    async def load_bodies(
        self, ctx: TrustedContext, refs: tuple[MemoryRef, ...]
    ) -> tuple[FullBodyReadResult, ...]:
        self.distinct(refs)
        result = []
        for ref in refs:
            try:
                result.append(await self.remember.read_body(ctx, ref))
            except FoundationError as exc:
                if exc.code not in {ErrorCode.DEPENDENCY_UNAVAILABLE, ErrorCode.NOT_FOUND}:
                    raise
                result.append(
                    FullBodyReadResult(
                        memory=ref,
                        outcome="unavailable",
                        path="none",
                        reason_code="body_dependency_unavailable",
                    )
                )
        return tuple(result)

    def revalidate_context(
        self, tx: Transaction, ctx: TrustedContext, request: ContextGuardRequest
    ) -> tuple[GuardStamp, ...]:
        sql = native(tx)
        self.remember.identity.revalidate(sql, ctx)
        current = []
        for expected in request.expected:
            guard = self.guard_in(sql, ctx, expected.memory)
            if (
                guard.model_dump(exclude={"checked_at"})
                != expected.model_dump(exclude={"checked_at"})
                or guard.checked_at < expected.checked_at
            ):
                sql.abort(ErrorCode.RESULT_INVALIDATED, "body or relation changed")
            current.append(guard)
        for expected_manifest in request.manifests:
            raw = sql.read("remember_manifests", self.remember.refkey(expected_manifest.memory))
            record = required_record(sql, memory_ref(expected_manifest.memory, versioned=True))
            if (
                raw is None
                or ProjectionManifest.model_validate(raw) != expected_manifest
                or record["projection_state"] != "ready"
            ):
                sql.abort(ErrorCode.RESULT_INVALIDATED, "published generation changed")
        return tuple(current)
