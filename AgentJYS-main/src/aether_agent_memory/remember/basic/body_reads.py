"""Verified address-first reads; ordinary reads never mutate cache placement."""

import asyncio
import math
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from pydantic import ValidationError

from aether_agent_memory.remember.contracts.foundation import (
    FullBodyReadResult,
    GuardStamp,
    MemoryRecord,
)
from aether_agent_memory.remember.contracts.models import (
    EligibilityBatch,
    EligibilityResult,
    MemoryReadBatch,
    MemoryRef,
    MemorySnapshot,
)
from aether_agent_memory.runtime.contracts.foundation import ResourceLocation
from aether_agent_memory.runtime.contracts.models import ErrorCode, Permission, TrustedContext
from aether_agent_memory.runtime.foundation.common import FoundationError
from aether_agent_memory.runtime.foundation.content_diagnostics import (
    authorize_content_read,
    permits_content_read,
)
from aether_agent_memory.runtime.foundation.requests import text_hash
from aether_agent_memory.runtime.foundation.timings import measure_stage

from .service import memory_ref


@dataclass(frozen=True)
class BodyView:
    item: MemoryRecord | MemorySnapshot
    authority: ResourceLocation
    cache: ResourceLocation | None

    def same_body(self, other: "BodyView") -> bool:
        # Placement publication deliberately does not change semantic revisions.
        return self.item == other.item and self.authority == other.authority


class BodyReads:
    def __init__(self, owner: Any) -> None:
        self.owner = owner

    def view(self, raw: dict[str, Any], ref: MemoryRef) -> BodyView:
        item: MemoryRecord | MemorySnapshot
        try:
            if "body_location" in raw:
                # Isolate only the optional field. Every authoritative field still
                # passes the complete MemoryRecord model and its binding validators.
                record = MemoryRecord.model_validate({**raw, "cache_location": None})
                cache = None
                if raw.get("cache_location") is not None:
                    with suppress(ValidationError):
                        cache = MemoryRecord.model_validate(raw).cache_location
                item, authority = record, record.body_location
            else:
                item = MemorySnapshot.model_validate(raw)
                if text_hash(item.content) != item.content_hash:
                    raise FoundationError(ErrorCode.CONTRACT_VIOLATION, "inline body hash differs")
                authority = self.owner.bodies.location(ref.scope, item.content)
                cache = None
        except ValidationError as exc:
            raise FoundationError(
                ErrorCode.CONTRACT_VIOLATION, "invalid body authority record"
            ) from exc
        if item.ref != ref:
            raise FoundationError(ErrorCode.CONTRACT_VIOLATION, "body record reference differs")
        self.owner.bodies.check_binding(authority)
        return BodyView(item, authority, cache)

    def inspect(
        self, ctx: TrustedContext, ref: MemoryRef, *, exclude_unauthorized: bool = False
    ) -> BodyView | FullBodyReadResult:
        owner = self.owner
        with owner.uow.transaction() as tx:
            owner.identity.revalidate(tx, ctx)
            if exclude_unauthorized and not permits_content_read(
                owner.identity, tx, ctx, Permission.READ, memory_ref(ref)
            ):
                return FullBodyReadResult(
                    memory=ref, outcome="excluded", path="none", reason_code="unauthorized"
                )
            authorize_content_read(owner.identity, tx, ctx, memory_ref(ref))
            raw = tx.get(memory_ref(ref, versioned=True))
            selected = self.view(raw, ref) if raw is not None else None
            eligible = owner.final_guard(tx, ctx, (ref,), "recall").items[0]
            if eligible.decision != "allowed":
                return FullBodyReadResult(
                    memory=ref, outcome="excluded", path="none", reason_code=eligible.reason
                )
            if selected is None:
                raise FoundationError(ErrorCode.CONTRACT_VIOLATION, "allowed body record missing")
            if eligible.checked_revision != selected.item.object_revision:
                return self.stale(ref)
            return selected

    @staticmethod
    def stale(ref: MemoryRef) -> FullBodyReadResult:
        return FullBodyReadResult(
            memory=ref, outcome="stale", path="none", reason_code="changed_during_read"
        )

    def recheck(
        self, ctx: TrustedContext, ref: MemoryRef, selected: BodyView
    ) -> GuardStamp | FullBodyReadResult:
        current = self.inspect(ctx, ref)
        if isinstance(current, FullBodyReadResult) or not selected.same_body(current):
            return self.stale(ref)
        item = current.item
        return GuardStamp(
            memory=ref,
            object_revision=item.object_revision,
            relations_revision=item.relations_revision
            if isinstance(item, MemoryRecord)
            else item.revision,
            authorization_epoch=ctx.principal.auth_epoch,
            body_hash=current.authority.content_hash,
            checked_at=self.owner.identity.clock(),
        )

    def deadline(self, ctx: TrustedContext) -> float:
        seconds = (
            datetime.fromisoformat(ctx.deadline_at)
            - datetime.fromisoformat(self.owner.identity.clock())
        ).total_seconds()
        if seconds <= 0:
            raise FoundationError(ErrorCode.DEADLINE_EXCEEDED, "request deadline expired")
        return asyncio.get_running_loop().time() + seconds

    async def fetch(
        self, ref: MemoryRef, selected: BodyView, end: float
    ) -> tuple[str, str, ResourceLocation]:
        if isinstance(selected.item, MemorySnapshot):
            return selected.item.content, "authority", selected.authority
        reader = self.owner.bodies.cache_reader
        if selected.cache is not None and reader is not None:
            # Reserve at least half the remaining request for cold fallback. The
            # Redis client also owns finite socket/connect limits; cancelling this
            # await cannot terminate a synchronous socket already running in a thread.
            budget = max(0, (end - asyncio.get_running_loop().time()) / 2)
            configured = getattr(reader, "read_timeout_seconds", None)
            if (
                isinstance(configured, (int, float))
                and not isinstance(configured, bool)
                and math.isfinite(configured)
                and configured > 0
            ):
                budget = min(budget, configured)
            try:
                async with asyncio.timeout(budget):
                    text = await reader.read_location(ref.scope, selected.cache, selected.authority)
                if text is not None and text_hash(text) == selected.authority.content_hash:
                    return text, "cache", selected.cache
            except FoundationError as exc:
                if exc.code not in {
                    ErrorCode.DEPENDENCY_UNAVAILABLE,
                    ErrorCode.NOT_FOUND,
                    ErrorCode.CONTRACT_VIOLATION,
                    ErrorCode.INVALID_ARGUMENT,
                    ErrorCode.VERSION_CONFLICT,
                }:
                    raise
            except (TimeoutError, OSError, UnicodeError, ValueError, TypeError):
                pass
        try:
            text = await self.owner.bodies.read_authority(selected.authority)
        except FoundationError as exc:
            if selected.authority.provider_id == "local" and exc.code == ErrorCode.NOT_FOUND:
                # The public local-body boundary historically reports a missing
                # retained file as a dependency failure. Remote absence remains 404.
                raise FoundationError(
                    ErrorCode.DEPENDENCY_UNAVAILABLE, "body authority unavailable"
                ) from exc
            raise
        path = "p2" if selected.authority.provider_id == "p2" else "authority"
        return text, path, selected.authority

    async def verified(
        self, ctx: TrustedContext, ref: MemoryRef, *, exclude_unauthorized: bool = False
    ) -> tuple[FullBodyReadResult, BodyView | None]:
        try:
            end = self.deadline(ctx)
            async with asyncio.timeout_at(end):
                with measure_stage("memory_prepare"):
                    selected = await asyncio.to_thread(
                        self.inspect, ctx, ref, exclude_unauthorized=exclude_unauthorized
                    )
                if isinstance(selected, FullBodyReadResult):
                    return selected, None
                with measure_stage("memory_fetch"):
                    content, path, location = await self.fetch(ref, selected, end)
                if text_hash(content) != selected.authority.content_hash or (
                    isinstance(selected.item, MemoryRecord)
                    and len(content) != selected.item.body_chars
                ):
                    raise FoundationError(
                        ErrorCode.CONTRACT_VIOLATION, "body content differs from authority"
                    )
                with measure_stage("memory_validate"):
                    guard = await asyncio.to_thread(self.recheck, ctx, ref, selected)
                if isinstance(guard, FullBodyReadResult):
                    return guard, None
                return FullBodyReadResult(
                    memory=ref,
                    outcome="read",
                    content=content,
                    sources=selected.item.sources,
                    location=location,
                    guard=guard,
                    path=path,
                    reason_code="verified_full_body",
                ), selected
        except TimeoutError as exc:
            raise FoundationError(
                ErrorCode.DEADLINE_EXCEEDED, "body read deadline expired"
            ) from exc

    async def read(self, ctx: TrustedContext, ref: MemoryRef) -> FullBodyReadResult:
        result, _ = await self.verified(ctx, ref)
        return result

    async def load(self, ctx: TrustedContext, refs: tuple[MemoryRef, ...]) -> MemoryReadBatch:
        try:
            async with asyncio.timeout_at(self.deadline(ctx)):
                return await self._load(ctx, refs)
        except TimeoutError as exc:
            raise FoundationError(
                ErrorCode.DEADLINE_EXCEEDED, "body batch deadline expired"
            ) from exc

    async def _load(self, ctx: TrustedContext, refs: tuple[MemoryRef, ...]) -> MemoryReadBatch:
        if len({ref.model_dump_json() for ref in refs}) != len(refs):
            raise FoundationError(ErrorCode.INVALID_ARGUMENT, "duplicate memory references")
        fetched: dict[str, tuple[FullBodyReadResult, BodyView | None]] = {}
        for ref in refs:
            try:
                fetched[ref.model_dump_json()] = await self.verified(
                    ctx, ref, exclude_unauthorized=True
                )
            except FoundationError as exc:
                if exc.code not in {ErrorCode.DEPENDENCY_UNAVAILABLE, ErrorCode.NOT_FOUND}:
                    raise
                fetched[ref.model_dump_json()] = (
                    FullBodyReadResult(
                        memory=ref,
                        outcome="unavailable",
                        path="none",
                        reason_code="body_dependency_unavailable",
                    ),
                    None,
                )

        def assemble() -> MemoryReadBatch:
            owner = self.owner
            with owner.uow.transaction() as tx:
                owner.identity.revalidate(tx, ctx)
                eligibility = owner.final_guard(tx, ctx, refs, "recall")
                items, decisions = [], []
                for eligible in eligibility.items:
                    result, selected = fetched[eligible.ref.model_dump_json()]
                    if eligible.decision != "allowed":
                        decisions.append(eligible)
                        continue
                    raw = tx.get(memory_ref(eligible.ref, versioned=True))
                    current = self.view(raw, eligible.ref) if raw is not None else None
                    if (
                        selected is not None
                        and current is not None
                        and selected.same_body(current)
                        and (eligible.checked_revision == selected.item.object_revision)
                    ):
                        assert result.content is not None
                        items.append(
                            owner.authority_snapshot(current.item, result.content)
                            if isinstance(current.item, MemoryRecord)
                            else current.item
                        )
                        decisions.append(eligible)
                    else:
                        decisions.append(
                            EligibilityResult(
                                ref=eligible.ref,
                                decision="unverifiable"
                                if result.outcome == "unavailable"
                                else "excluded",
                                reason=result.reason_code
                                if selected is None
                                else "changed_during_read",
                                checked_revision=eligible.checked_revision,
                            )
                        )
                conflicts = owner.conflict_groups_in(tx, ctx, refs)
                owner.identity.revalidate(tx, ctx)
                return MemoryReadBatch(
                    items=tuple(items),
                    eligibility=EligibilityBatch(
                        items=tuple(decisions), authorization_epoch=eligibility.authorization_epoch
                    ),
                    conflicts=conflicts,
                )

        return await asyncio.to_thread(assemble)
