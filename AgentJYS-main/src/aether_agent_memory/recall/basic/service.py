"""Unified Recall: candidate discovery, exact eligibility, budget and durable context."""

from __future__ import annotations

import asyncio
import math
from datetime import datetime

from aether_agent_memory.recall.contracts.models import (
    AccessObserved,
    ContextPack,
    Coverage,
    RecallRecord,
    RecallRequest,
)
from aether_agent_memory.recall.contracts.ports import EmbeddingPort, VectorSearchPort
from aether_agent_memory.remember.contracts.models import MemoryRef
from aether_agent_memory.remember.contracts.ports import MemoryReadPort
from aether_agent_memory.runtime.contracts.models import (
    DiagnosticRecord,
    ErrorCode,
    EventEnvelope,
    Flow,
    Permission,
    RecordRef,
    TrustedContext,
)
from aether_agent_memory.runtime.foundation.common import FoundationError, fingerprint
from aether_agent_memory.runtime.foundation.events import Events
from aether_agent_memory.runtime.foundation.identity import Identity
from aether_agent_memory.runtime.foundation.requests import request_key, select_scope
from aether_agent_memory.runtime.foundation.storage import SQLiteTransaction, SQLiteUnitOfWork
from aether_agent_memory.runtime.foundation.telemetry import observed

from .components import RankedMemory, assemble, fuse
from .config import RecallSettings
from .reranking import Reranker
from .retrievers import Sources
from .tokenization import ModelTokenizer, TokenCounter


@observed("recall")
class Recall:
    def __init__(
        self,
        uow: SQLiteUnitOfWork,
        identity: Identity,
        events: Events,
        memories: MemoryReadPort,
        embedding: EmbeddingPort,
        vectors: VectorSearchPort,
        model_space: str,
        settings: RecallSettings | None = None,
        tokenizer: TokenCounter | None = None,
        reranker: Reranker | None = None,
    ) -> None:
        self.uow, self.identity, self.events, self.memories = uow, identity, events, memories
        self.embedding, self.vectors, self.model_space = embedding, vectors, model_space
        self.settings = settings or RecallSettings()
        self.tokenizer = tokenizer or ModelTokenizer(
            self.settings.tokenizer, self.settings.tokenizer_path
        )
        self.reranker = reranker
        self.sources = Sources(uow, memories, embedding, vectors, model_space, self.settings)
        self.policy_version = "recall_" + fingerprint(
            {
                "settings": self.settings.model_dump(),
                "tokenizer": self.tokenizer.identifier,
                "reranker": getattr(reranker, "identifier", None),
            }
        )
        events.register_type("recall.access", self.validate_event, permission=Permission.READ)

    @staticmethod
    def validate_event(event: EventEnvelope) -> None:
        value = AccessObserved.model_validate(event.payload)
        if event.producer != Flow.RECALL or event.subject != RecordRef(
            owner=Flow.REMEMBER,
            object_type="memory",
            object_id=value.memory.memory_id,
            scope=value.memory.scope,
            version=value.memory.version,
        ):
            raise ValueError("access event subject mismatch")

    @staticmethod
    def ref(record: RecallRecord) -> RecordRef:
        return RecordRef(
            owner=Flow.RECALL, object_type="recall", object_id=record.recall_id, scope=record.scope
        )

    def stage(
        self,
        ctx: TrustedContext,
        recall_id: str,
        stage: str,
        details: dict[str, object] | None = None,
    ) -> None:
        with self.uow.transaction() as tx:
            self.identity.revalidate(tx, ctx)
            row = tx.read("recall_requests", recall_id)
            record = RecallRecord.model_validate(row["record"])
            if record.state not in {"accepted", "running"}:
                raise FoundationError(ErrorCode.VERSION_CONFLICT, "recall already ended")
            updated = RecallRecord.model_validate(
                {
                    **record.model_dump(),
                    "state": "running",
                    "stage": stage,
                    "revision": record.revision + 1,
                }
            )
            tx.write(
                "recall_requests", recall_id, {**row, "record": updated.model_dump(mode="json")}
            )
            diagnostic = DiagnosticRecord(
                record_id=fingerprint([recall_id, stage]),
                subject=self.ref(record),
                request_id=ctx.request_id,
                trace_id=ctx.trace_id,
                stage="recall." + stage,
                occurred_at=self.identity.clock(),
                coverage="complete",
            )
            tx.write("diagnostics", diagnostic.record_id, diagnostic.model_dump(mode="json"))
            tx.write(
                "recall_stages",
                fingerprint([recall_id, stage]),
                {
                    "subject": self.ref(record).model_dump(mode="json"),
                    "recall_id": recall_id,
                    "stage": stage,
                    "trace_id": ctx.trace_id,
                    "recorded_at": self.identity.clock(),
                    "details": details or {},
                },
            )

    def access(
        self,
        tx: SQLiteTransaction,
        ctx: TrustedContext,
        recall_id: str,
        memory: MemoryRef,
        stage: str,
        elapsed_ms: float = 0,
    ) -> None:
        access_key = fingerprint([recall_id, memory.model_dump(mode="json")])
        value = AccessObserved(
            recall_id=recall_id,
            memory=memory,
            stage=stage,
            outcome="succeeded",
            access_key=access_key,
            representation="original",
            elapsed_ms=elapsed_ms,
        )
        self.events.append(
            tx,
            ctx,
            EventEnvelope(
                event_id=fingerprint([access_key, stage]),
                event_type="recall.access",
                producer=Flow.RECALL,
                subject=RecordRef(
                    owner=Flow.REMEMBER,
                    object_type="memory",
                    object_id=memory.memory_id,
                    scope=memory.scope,
                    version=memory.version,
                ),
                subject_revision=memory.version,
                occurred_at=self.identity.clock(),
                request_id=ctx.request_id,
                trace_id=ctx.trace_id,
                initiator_id=ctx.principal.principal_id,
                initiator_auth_epoch=ctx.principal.auth_epoch,
                payload=value.model_dump(mode="json"),
                payload_hash=fingerprint(value.model_dump(mode="json")),
            ),
        )

    async def recall(self, ctx: TrustedContext, request: RecallRequest) -> ContextPack:
        scope = select_scope(ctx, request.selection)
        recall_id = request_key(ctx, "recall")
        signature = fingerprint(request.model_dump(mode="json"))
        with self.uow.transaction() as tx:
            record = RecallRecord(
                recall_id=recall_id,
                scope=scope,
                state="accepted",
                stage="validate",
                revision=1,
                deadline_at=ctx.deadline_at,
                result_available=False,
            )
            self.identity.authorize(tx, ctx, Permission.READ, self.ref(record))
            previous = tx.read("recall_requests", recall_id)
            if previous:
                if previous["signature"] != signature:
                    tx.abort(
                        ErrorCode.IDEMPOTENCY_CONFLICT,
                        "same recall operation with different request",
                    )
                if previous["record"]["state"] != "completed":
                    raise FoundationError(
                        ErrorCode.REQUEST_IN_PROGRESS
                        if previous["record"]["state"] in {"accepted", "running"}
                        else ErrorCode.EXECUTION_INTERRUPTED,
                        "recall has no reusable result",
                    )
            else:
                tx.write(
                    "recall_requests",
                    recall_id,
                    {
                        "record": record.model_dump(mode="json"),
                        "signature": signature,
                        "context": ctx.model_dump(mode="json"),
                        "pack": None,
                    },
                )
        if previous:
            return self.result(ctx, recall_id)
        seconds = max(
            0.001,
            (
                datetime.fromisoformat(ctx.deadline_at.replace("Z", "+00:00"))
                - datetime.fromisoformat(self.identity.clock().replace("Z", "+00:00"))
            ).total_seconds(),
        )
        try:
            async with asyncio.timeout(seconds):
                return await self.retrieve(ctx, request, recall_id)
        except (Exception, asyncio.CancelledError) as exc:
            code = (
                ErrorCode.EXECUTION_INTERRUPTED
                if isinstance(exc, asyncio.CancelledError)
                else ErrorCode.DEADLINE_EXCEEDED
                if isinstance(exc, TimeoutError)
                else exc.code
                if isinstance(exc, FoundationError)
                else ErrorCode.DEPENDENCY_UNAVAILABLE
            )
            with self.uow.transaction() as tx:
                row = tx.read("recall_requests", recall_id)
                record = RecallRecord.model_validate(row["record"])
                tx.write(
                    "recall_requests",
                    recall_id,
                    {
                        **row,
                        "record": record.model_copy(
                            update={
                                "state": "failed",
                                "result_available": False,
                                "revision": record.revision + 1,
                                "reason": code.value,
                            }
                        ).model_dump(mode="json"),
                    },
                )
            if isinstance(exc, (FoundationError, asyncio.CancelledError)):
                raise
            raise FoundationError(code, "Recall dependency or deadline failure") from exc

    async def retrieve(
        self, ctx: TrustedContext, request: RecallRequest, recall_id: str
    ) -> ContextPack:
        self.stage(ctx, recall_id, "select")
        scope = select_scope(ctx, request.selection)
        selected = (
            ("working", "long_term")
            if request.sources == "both"
            or (request.sources == "auto" and (scope.session_id or scope.task_id))
            else ("long_term",)
            if request.sources == "auto"
            else (request.sources,)
        )
        self.stage(ctx, recall_id, "discover", {"sources": selected})
        tasks = [
            asyncio.create_task(self.sources.discover(ctx, request, recall_id, name))
            for name in selected
        ]
        try:
            results = await asyncio.gather(*tasks)
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
        coverage = {"working": "not_requested", "long_term": "not_requested"}
        for result in results:
            coverage[result.source] = result.coverage
        self.stage(
            ctx,
            recall_id,
            "load",
            {
                "sources": [
                    {
                        "source": r.source,
                        "coverage": r.coverage,
                        "reason": r.reason,
                        "candidate_count": len(r.candidates),
                        "rejected": r.rejected,
                        "candidates": [m.ref.model_dump(mode="json") for m in r.candidates],
                    }
                    for r in results
                ]
            },
        )
        ranked = fuse([r.candidates for r in results])
        # Qualify before handing any plaintext to a reranking provider.
        with self.uow.transaction() as tx:
            self.identity.revalidate(tx, ctx)
            eligibility = self.memories.final_guard(
                tx, ctx, tuple(c.memory.ref for c in ranked), "recall"
            )
            allowed = {
                item.ref.model_dump_json()
                for item in eligibility.items
                if item.decision == "allowed"
            }
            ranked = [c for c in ranked if c.memory.ref.model_dump_json() in allowed]
            reads = {key: item for r in results for key, item in r.reads.items()}
            for ref, elapsed in reads.values():
                self.access(tx, ctx, recall_id, ref, "read", elapsed)
        self.stage(
            ctx,
            recall_id,
            "rank",
            {
                "ranked": [
                    {
                        "memory_id": c.memory.ref.memory_id,
                        "version": c.memory.ref.version,
                        "score": c.score,
                    }
                    for c in ranked
                ]
            },
        )
        ranked, rerank_reason = await self.rerank(ctx, request.query, recall_id, ranked)
        groups, rendered = assemble(
            ranked, recall_id, request.token_budget, self.tokenizer, self.settings.max_items
        )
        self.stage(
            ctx,
            recall_id,
            "assemble",
            {
                "selected_count": len(groups),
                "tokens_used": self.tokenizer.count(rendered),
                "tokenizer_id": self.tokenizer.identifier,
            },
        )
        incomplete = any(coverage[source] != "complete" for source in selected)
        if not groups:
            if incomplete:
                raise FoundationError(
                    ErrorCode.DEPENDENCY_UNAVAILABLE, "no usable source after dependency failure"
                )
            if ranked:
                raise FoundationError(
                    ErrorCode.BUDGET_TOO_SMALL, "no complete memory fits the context budget"
                )
        reasons = tuple(
            f"{r.source}:{r.reason or r.coverage}" for r in results if r.coverage != "complete"
        ) + ((rerank_reason,) if rerank_reason else ())
        outcome = "degraded" if groups and reasons else "available" if groups else "empty"
        self.stage(
            ctx,
            recall_id,
            "finalize",
            {
                "selected_count": len(groups),
                "tokens_used": self.tokenizer.count(rendered),
                "outcome": outcome,
                "coverage": coverage,
            },
        )
        with self.uow.transaction() as tx:
            eligible = self.memories.final_guard(
                tx, ctx, tuple(i.memory for group in groups for i in group.items), "recall"
            )
            if any(item.decision != "allowed" for item in eligible.items):
                tx.abort(ErrorCode.RESULT_INVALIDATED, "memory changed before context commit")
            pack = ContextPack(
                recall_id=recall_id,
                scope=scope,
                outcome=outcome,
                selected_sources=selected,
                coverage=Coverage(**coverage),
                groups=tuple(groups),
                rendered_context=rendered,
                token_budget=request.token_budget,
                tokens_used=self.tokenizer.count(rendered),
                tokenizer_id=self.tokenizer.identifier,
                policy_version=self.policy_version,
                degradation_reasons=reasons,
                committed_at=self.identity.clock(),
            )
            row = tx.read("recall_requests", recall_id)
            record = RecallRecord.model_validate(row["record"])
            if record.state != "running":
                tx.abort(ErrorCode.VERSION_CONFLICT, "recall was interrupted")
            tx.write(
                "recall_requests",
                recall_id,
                {
                    **row,
                    "record": record.model_copy(
                        update={
                            "state": "completed",
                            "result_available": True,
                            "revision": record.revision + 1,
                        }
                    ).model_dump(mode="json"),
                    "pack": pack.model_dump(mode="json"),
                },
            )
            for group in groups:
                for context_item in group.items:
                    self.access(tx, ctx, recall_id, context_item.memory, "packed")
            tx.before_commit.append(lambda: self.identity.revalidate(tx, ctx))
            return pack

    async def rerank(
        self, ctx: TrustedContext, query: str, recall_id: str, ranked: list[RankedMemory]
    ) -> tuple[list[RankedMemory], str | None]:
        state = "disabled" if self.settings.rerank_policy == "disabled" else "skipped_empty"
        if ranked and self.settings.rerank_policy != "disabled":
            self.stage(ctx, recall_id, "rerank", {"state": "running"})
            try:
                if self.reranker is None:
                    raise FoundationError(ErrorCode.CONTRACT_VIOLATION, "missing reranker")
                async with asyncio.timeout(self.settings.rerank_timeout_seconds):
                    scores = await self.reranker.rerank(
                        ctx, query, tuple(c.memory.content for c in ranked)
                    )
                if len(scores) != len(ranked) or not all(math.isfinite(s) for s in scores):
                    raise FoundationError(ErrorCode.CONTRACT_VIOLATION, "invalid reranker output")
                ranked = sorted(
                    [RankedMemory(c.memory, s) for c, s in zip(ranked, scores, strict=True)],
                    key=lambda c: (-c.score, c.memory.ref.model_dump_json()),
                )
                state = "succeeded"
            except (TimeoutError, FoundationError) as exc:
                code = exc.code if isinstance(exc, FoundationError) else ErrorCode.DEADLINE_EXCEEDED
                self.stage(ctx, recall_id, "rerank", {"state": "failed", "reason_code": code.value})
                if self.settings.rerank_policy != "fallback" or code not in {
                    ErrorCode.DEPENDENCY_UNAVAILABLE,
                    ErrorCode.DEADLINE_EXCEEDED,
                }:
                    raise
                return ranked, "rerank:" + code.value
        self.stage(
            ctx,
            recall_id,
            "rerank",
            {
                "state": state,
                "model_id": getattr(self.reranker, "identifier", None),
                "ranked": [
                    {
                        "memory_id": c.memory.ref.memory_id,
                        "version": c.memory.ref.version,
                        "score": c.score,
                    }
                    for c in ranked
                ],
            },
        )
        return ranked, None

    def status(self, ctx: TrustedContext, recall_id: str) -> RecallRecord:
        with self.uow.transaction() as tx:
            row = tx.read("recall_requests", recall_id)
            if row is None:
                raise FoundationError(ErrorCode.NOT_FOUND, "recall not found")
            record = RecallRecord.model_validate(row["record"])
            self.identity.authorize(tx, ctx, Permission.READ, self.ref(record))
            return record

    def result(self, ctx: TrustedContext, recall_id: str) -> ContextPack:
        with self.uow.transaction() as tx:
            row = tx.read("recall_requests", recall_id)
            if row is None:
                raise FoundationError(ErrorCode.NOT_FOUND, "recall not found")
            record = RecallRecord.model_validate(row["record"])
            self.identity.authorize(tx, ctx, Permission.READ, self.ref(record))
            if not record.result_available or row["pack"] is None:
                raise FoundationError(
                    ErrorCode.REQUEST_IN_PROGRESS, "recall has no committed result"
                )
            pack = ContextPack.model_validate(row["pack"])
            eligible = self.memories.final_guard(
                tx, ctx, tuple(i.memory for group in pack.groups for i in group.items), "recall"
            )
            if any(item.decision != "allowed" for item in eligible.items):
                raise FoundationError(
                    ErrorCode.RESULT_INVALIDATED, "historical context is no longer valid"
                )
            return pack

    def recover_expired(self) -> int:
        count = 0
        with self.uow.transaction() as tx:
            for key, row in tx.rows("recall_requests"):
                record = RecallRecord.model_validate(row["record"])
                if (
                    record.state in {"accepted", "running"}
                    and record.deadline_at <= self.identity.clock()
                ):
                    tx.write(
                        "recall_requests",
                        key,
                        {
                            **row,
                            "record": record.model_copy(
                                update={
                                    "state": "failed",
                                    "reason": ErrorCode.EXECUTION_INTERRUPTED.value,
                                    "revision": record.revision + 1,
                                }
                            ).model_dump(mode="json"),
                        },
                    )
                    count += 1
        return count
