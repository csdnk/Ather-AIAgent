"""Remember implementations of the existing generation Recall contracts."""

import asyncio
from typing import Any, Literal

from aether_agent_memory.remember.contracts.foundation import (
    CandidateQualificationResult,
    CandidateQualificationTarget,
    ContextGuardRequest,
    FullBodyReadResult,
    GuardStamp,
    MemoryRelationSnapshot,
    ProjectionManifest,
    ProjectionReadiness,
)
from aether_agent_memory.remember.contracts.models import MemoryRef
from aether_agent_memory.runtime.contracts.models import (
    ErrorCode,
    RecordRef,
    ScopeSelector,
    TrustedContext,
)
from aether_agent_memory.runtime.contracts.ports import Transaction
from aether_agent_memory.runtime.foundation.common import FoundationError, fingerprint
from aether_agent_memory.runtime.foundation.content_diagnostics import authorize_content_read
from aether_agent_memory.runtime.foundation.requests import select_scope
from aether_agent_memory.runtime.foundation.transactions import native
from aether_agent_memory.runtime.storage.ports import MetadataTransaction

from .records import required_record
from .service import memory_ref


class RememberBoundary:
    def __init__(self, remember: Any) -> None:
        self.remember = remember

    async def projection_readiness(
        self,
        ctx: TrustedContext,
        selection: ScopeSelector,
        memory_source: Literal["working", "long_term"],
    ) -> ProjectionReadiness:
        return await asyncio.to_thread(self._projection_readiness, ctx, selection, memory_source)

    def _projection_readiness(
        self,
        ctx: TrustedContext,
        selection: ScopeSelector,
        memory_source: Literal["working", "long_term"],
    ) -> ProjectionReadiness:
        if memory_source not in {"working", "long_term"}:
            raise FoundationError(ErrorCode.INVALID_ARGUMENT, "invalid memory source")
        select_scope(ctx, selection)
        owner = self.remember
        ready = pending = failed = 0
        with owner.uow.transaction() as tx:
            owner.identity.revalidate(tx, ctx)
            for memory_id, pointer in tx.rows("remember_current"):
                record_ref = RecordRef.model_validate(pointer)
                if not owner.identity.discoverable(tx, ctx, record_ref, selection):
                    continue
                record = required_record(tx, record_ref)
                actual = "working" if record["kind"] == "working" else "long_term"
                if actual != memory_source:
                    continue
                ref = MemoryRef.model_validate(record["ref"])
                if owner.final_guard(tx, ctx, (ref,), "recall").items[0].decision != "allowed":
                    continue
                summary = tx.read("remember_working_summaries", memory_id)
                if summary and summary["memory"] == record["ref"] and summary["state"] != "ready":
                    if summary["state"] == "failed":
                        failed += 1
                    else:
                        pending += 1
                    continue
                raw = tx.read("remember_manifests", owner.refkey(ref))
                manifest = ProjectionManifest.model_validate(raw) if raw else None
                body_hash = (record.get("body_location") or {}).get(
                    "content_hash", record.get("content_hash")
                )
                if (
                    record["projection_state"] == "ready"
                    and manifest is not None
                    and manifest.state == "ready"
                    and manifest.memory == ref
                    and manifest.body_hash == body_hash
                    and manifest.model_space == owner.model_space
                ):
                    ready += 1
                    continue
                task_id = tx.read(
                    "remember_latest_task", fingerprint([memory_id, "remember.project"])
                )
                task = tx.read("tasks", task_id) if task_id else None
                if (
                    record["projection_state"] == "failed"
                    or task
                    and task["record"]["state"] in {"failed", "attention_required"}
                ):
                    failed += 1
                else:
                    pending += 1
            owner.identity.revalidate(tx, ctx)
        return ProjectionReadiness(
            source=memory_source,
            ready_count=ready,
            pending_count=pending,
            failed_count=failed,
            complete=pending == failed == 0,
        )

    @staticmethod
    def distinct(refs: tuple[MemoryRef, ...]) -> None:
        if len({r.model_dump_json() for r in refs}) != len(refs):
            raise FoundationError(ErrorCode.INVALID_ARGUMENT, "duplicate memory references")

    def guard_in(self, tx: MetadataTransaction, ctx: TrustedContext, ref: MemoryRef) -> GuardStamp:
        owner = self.remember
        authorize_content_read(owner.identity, tx, ctx, memory_ref(ref))
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
        return await asyncio.to_thread(self._qualify, ctx, targets, purpose)

    def _qualify(
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
                    actual_source = "working" if record["kind"] == "working" else "long_term"
                    if target.memory_source != actual_source:
                        results.append(
                            CandidateQualificationResult(
                                target=target,
                                decision="excluded",
                                reason_code="memory_source_mismatch",
                                manifest=None,
                                guard=None,
                            )
                        )
                        continue
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
