"""Durable Remember pipeline composed with RF task leases and transactional outbox.

MemorySnapshot is a hydrated API DTO. New authority rows contain MemoryRecord
references. Local body replicas are durable runtime data, separate from caches.
"""

from __future__ import annotations

from collections.abc import Callable
from contextvars import ContextVar
from typing import Any, cast

from aether_agent_memory.recall.basic.tokenization import TokenCounter
from aether_agent_memory.recall.contracts.models import EmbeddingRequest, VectorSearchRequest
from aether_agent_memory.remember.contracts.foundation import (
    ChunkDescriptor,
    FullBodyReadResult,
    GuardStamp,
    MemoryRecord,
    ProjectionManifest,
)
from aether_agent_memory.remember.contracts.models import (
    CandidateFact,
    ConflictGroup,
    CorrectionRequest,
    EligibilityBatch,
    ExtractionRequest,
    ExtractionResult,
    FactEvidence,
    MemoryKind,
    MemoryReadBatch,
    MemoryRef,
    MemorySnapshot,
    MemoryStatus,
    ProjectionRequest,
    ProjectionState,
    ProjectionTarget,
    RememberReceipt,
    RememberRequest,
    SourceInput,
    SourceRef,
)
from aether_agent_memory.runtime.contracts.foundation import ResourceLocation
from aether_agent_memory.runtime.contracts.models import (
    EffectStatus,
    ErrorCode,
    PageRequest,
    Permission,
    RecordRef,
    RecoveryAction,
    RecoveryDecision,
    RunResult,
    Scope,
    ScopeSelector,
    TaskRecord,
    TrustedContext,
)
from aether_agent_memory.runtime.contracts.ports import Transaction
from aether_agent_memory.runtime.foundation.common import FoundationError, fingerprint, later
from aether_agent_memory.runtime.foundation.requests import select_scope, text_hash
from aether_agent_memory.runtime.foundation.storage import SQLiteTransaction, native
from aether_agent_memory.runtime.foundation.telemetry import observed

from .comparison import (
    ComparisonDecision,
    ComparisonPort,
    ConservativeComparison,
    EquivalencePort,
    EquivalenceVerdict,
)
from .compression import (
    CompressionPort,
    ExactParagraphCompression,
    ExactParagraphQuality,
    QualityPort,
)
from .content import Bodies
from .dedup import canonical_text, source_identity, source_signature
from .eligibility import qualify
from .extraction import LiteralExtraction
from .policy import RememberPolicy, chunks, importance
from .projection import projection_target
from .records import required_record
from .reflection import Reflection
from .retention import Retention
from .revalidation import Revalidation
from .service import memory_ref
from .sources import PreparedDocument, SourceAccess
from .summaries import SummaryPort, WorkingSummaries

_task_policy: ContextVar[RememberPolicy | None] = ContextVar("remember_task_policy", default=None)


@observed("remember")
class RememberPipeline(Revalidation):
    independent_working_sources = True
    retention: Retention
    reflection: Reflection
    embedding_count: Callable[[str], int]
    embedding_tokenizer_id: str

    @property
    def policy(self) -> RememberPolicy:
        return _task_policy.get() or self._policy

    @policy.setter
    def policy(self, value: RememberPolicy) -> None:
        self._policy = value

    def __init__(
        self,
        *args: Any,
        bodies: Bodies,
        tokenizer: TokenCounter,
        policy: RememberPolicy | None = None,
        comparison: ComparisonPort | None = None,
        equivalence_verifier: EquivalencePort | None = None,
        vector_search: Any = None,
        compressor: CompressionPort | None = None,
        quality: QualityPort | None = None,
        documents: Any = None,
        support_verifier: Any = None,
        summarizer: SummaryPort | None = None,
        **kwargs: Any,
    ) -> None:
        self.bodies, self.tokenizer = bodies, tokenizer
        self.policy = policy or RememberPolicy()
        self.max_input_bytes = self.policy.max_input_bytes
        self.processing_seconds = self.policy.processing_seconds
        self.comparison = comparison or ConservativeComparison()
        self.equivalence_verifier = equivalence_verifier
        self.vector_search, self.compressor, self.quality = vector_search, compressor, quality
        if compressor is None and quality is None:
            self.compressor, self.quality = ExactParagraphCompression(), ExactParagraphQuality()
        self.documents = documents or {}
        self.support_verifier = support_verifier
        self.source_access = SourceAccess(self)
        self.summaries = WorkingSummaries(self, summarizer)
        super().__init__(*args, **kwargs)
        self.tasks.register("remember.compress", "remember", self, permission=Permission.READ)
        self.tasks.register("remember.distill", "remember", self, permission=Permission.READ)
        self.tasks.register("remember.revalidate", "remember", self, permission=Permission.READ)
        self.tasks.register("remember.summarize", "remember", self, permission=Permission.WRITE)

    @staticmethod
    def refkey(ref: MemoryRef) -> str:
        return fingerprint(ref.model_dump(mode="json"))

    def final_guard(
        self, tx: Transaction, ctx: TrustedContext, refs: tuple[MemoryRef, ...], purpose: str
    ) -> EligibilityBatch:
        return qualify(self, native(tx), ctx, refs, purpose)

    def enqueue(
        self, tx: SQLiteTransaction, ctx: TrustedContext, memory: MemorySnapshot, kind: str
    ) -> str:
        task_id = super().enqueue(tx, ctx, memory, kind)
        if tx.read("remember_task_policy", task_id) is None:
            tx.write("remember_task_policy", task_id, self.policy.model_dump(mode="json"))
        if not tx.read("remember_outbox", task_id):
            tx.write(
                "remember_outbox",
                task_id,
                {
                    "task_id": task_id,
                    "state": "pending",
                    "attempts": 0,
                    "next_attempt_at": self.identity.clock(),
                },
            )
        tx.write("remember_latest_task", fingerprint([memory.ref.memory_id, kind]), task_id)
        if kind == "remember.extract" and memory.kind == MemoryKind.WORKING:
            pending = tx.read("remember_pending", memory.ref.memory_id)
            if pending and pending["ref"] == memory.ref.model_dump(mode="json"):
                tx.write(
                    "remember_pending",
                    memory.ref.memory_id,
                    {**pending, "state": "scheduled", "task_id": task_id},
                )
        return task_id

    def decode(self, tx: SQLiteTransaction, raw: dict[str, Any]) -> MemorySnapshot:
        if "body_location" not in raw:
            return super().decode(tx, raw)
        record = MemoryRecord.model_validate(raw)
        text = self.bodies.read_local(record.body_location)
        self.bodies.prepared[record.body_location.object_key] = record.body_location
        return MemorySnapshot(
            ref=record.ref,
            revision=record.revision,
            object_revision=record.object_revision,
            kind=record.kind,
            status=record.status,
            content=text,
            content_hash=record.body_location.content_hash,
            sources=record.sources,
            projection_state=record.projection_state,
            model_space=record.projection.model_space if record.projection else None,
            expires_at=record.expires_at,
            supersedes=record.supersedes,
            created_at=record.created_at,
            importance=record.importance,
            importance_reason=record.importance_reason,
            importance_policy_version=record.importance_policy_version,
        )

    def put(self, tx: SQLiteTransaction, snapshot: MemorySnapshot) -> None:
        if snapshot.kind != MemoryKind.WORKING:
            index_key = self.duplicate_key(
                snapshot.ref.scope, snapshot.kind.value, snapshot.content
            )
            ids = tx.read("remember_duplicate_index", index_key) or []
            if snapshot.ref.memory_id not in ids:
                tx.write("remember_duplicate_index", index_key, [*ids, snapshot.ref.memory_id])
        semantic = [
            snapshot.ref.version,
            snapshot.status.value,
            snapshot.content_hash,
            [s.model_dump(mode="json") for s in snapshot.sources],
            snapshot.expires_at,
        ]
        semantic_key = self.space_key(snapshot.ref.scope)
        previous_semantic = tx.read("remember_semantic_fingerprints", snapshot.ref.memory_id)
        if previous_semantic != fingerprint(semantic):
            sequence = tx.read("remember_space_seq", semantic_key) or 0
            tx.write("remember_space_seq", semantic_key, sequence + 1)
            tx.write(
                "remember_semantic_fingerprints", snapshot.ref.memory_id, fingerprint(semantic)
            )
        location = self.bodies.stage(snapshot.ref.scope, snapshot.content)
        manifest = tx.read("remember_manifests", self.refkey(snapshot.ref))
        if manifest is None and snapshot.projection_state == ProjectionState.READY:
            # Existing inline ready rows retain their legacy vector binding until reindex.
            return super().put(tx, snapshot)
        if manifest and snapshot.projection_state == ProjectionState.STALE:
            manifest = {**manifest, "state": "stale"}
        if snapshot.projection_state not in {ProjectionState.READY, ProjectionState.STALE}:
            manifest = None
        record = MemoryRecord(
            ref=snapshot.ref,
            revision=snapshot.revision,
            relations_revision=snapshot.revision,
            object_revision=snapshot.object_revision,
            kind=snapshot.kind,
            status=snapshot.status,
            body_location=location,
            body_chars=len(snapshot.content),
            sources=snapshot.sources,
            projection_state=snapshot.projection_state,
            projection=manifest,
            expires_at=snapshot.expires_at,
            supersedes=snapshot.supersedes,
            created_at=snapshot.created_at,
            importance=snapshot.importance,
            importance_reason=snapshot.importance_reason,
            importance_policy_version=snapshot.importance_policy_version,
        )
        ref = memory_ref(snapshot.ref, versioned=True)
        tx.put_if_revision(ref, record.model_dump(mode="json"), tx.revision(ref))
        tx.write("remember_current", snapshot.ref.memory_id, ref.model_dump(mode="json"))

    @staticmethod
    def space_key(scope: Scope) -> str:
        return fingerprint(scope.model_dump(mode="json"))

    def new_source(
        self,
        tx: SQLiteTransaction,
        source_id: str,
        scope: Scope,
        text: str,
        working_id: str | None = None,
    ) -> SourceRef:
        location = self.bodies.stage(scope, text).model_copy(update={"kind": "source"})
        source = SourceRef(
            source_id=source_id,
            source_version=1,
            content_hash=text_hash(text),
            locator=location.object_key,
        )
        tx.write(
            "remember_sources",
            source_id,
            {
                "ref": source.model_dump(mode="json"),
                "scope": scope.model_dump(mode="json"),
                "original_location": location.model_dump(mode="json"),
                "valid": True,
                "revision": 1,
                "working_id": working_id,
                "saved_at": self.identity.clock(),
            },
        )
        self.source_access.register(tx, source, text)
        return source

    def source_replay(
        self,
        tx: SQLiteTransaction,
        ctx: TrustedContext,
        request: RememberRequest,
        scope: Scope,
        text: str,
        operation_key: str,
    ) -> RememberReceipt | None:
        # One-time metadata-only backfill; later new inputs use indexed lookups.
        if not tx.read("remember_migrations", "source_identity_v2"):
            for sid, raw_input in sorted(tx.rows("remember_source_input")):
                raw_source = tx.read("remember_sources", sid) or {}
                working_id = raw_source.get("working_id")
                if not working_id:
                    continue
                operation = tx.read("remember_operations", working_id)
                if not operation or not operation.get("result", {}).get("saved"):
                    continue
                old_scope = Scope.model_validate(raw_source["scope"])
                old_request = request.model_copy(
                    update={"source": SourceInput.model_validate(raw_input)}
                )
                identity = source_identity(old_scope, old_request)
                policy = tx.read("remember_source_policy", sid) or {}
                if tx.read("remember_source_receipts", identity) is None:
                    tx.write(
                        "remember_source_receipts",
                        identity,
                        {
                            "result": operation["result"],
                            "signature": fingerprint(
                                [
                                    raw_source["ref"]["content_hash"],
                                    policy.get("trigger", "remember"),
                                    policy.get("importance_category", "observation"),
                                ]
                                + ([policy["task_context"]] if policy.get("task_context") else [])
                            ),
                        },
                    )
            tx.write("remember_migrations", "source_identity_v2", True)
        previous = tx.read("remember_source_receipts", source_identity(scope, request))
        if previous is None:
            return None
        if previous["signature"] != source_signature(request, text):
            tx.abort(
                ErrorCode.IDEMPOTENCY_CONFLICT,
                "source occurrence/version reused with different content or policy",
            )
        result = RememberReceipt.model_validate(previous["result"]).model_copy(
            update={"operation_id": ctx.operation_id}
        )
        self.remember_result(tx, operation_key, request, result)
        return result

    @staticmethod
    def duplicate_key(scope: Scope, kind: str, text: str) -> str:
        return fingerprint(
            ["normalized_exact_v1", scope.model_dump(mode="json"), kind, canonical_text(text)]
        )

    def ensure_duplicate_index(self, tx: SQLiteTransaction) -> None:
        if tx.read("remember_migrations", "normalized_exact_v1"):
            return
        for key, pointer in tx.rows("remember_current"):
            raw = required_record(tx, RecordRef.model_validate(pointer))
            if raw["kind"] == "working":
                continue
            item = self.decode(tx, raw)
            index_key = self.duplicate_key(item.ref.scope, item.kind.value, item.content)
            ids = tx.read("remember_duplicate_index", index_key) or []
            tx.write("remember_duplicate_index", index_key, list(dict.fromkeys([*ids, key])))
        tx.write("remember_migrations", "normalized_exact_v1", True)

    def duplicate_in(
        self,
        tx: SQLiteTransaction,
        ctx: TrustedContext,
        candidate: CandidateFact,
        scope: Scope,
    ) -> MemorySnapshot | None:
        self.ensure_duplicate_index(tx)
        index_key = self.duplicate_key(scope, candidate.kind, candidate.text)
        for key in sorted(tx.read("remember_duplicate_index", index_key) or []):
            pointer = tx.read("remember_current", key)
            if pointer is None:
                continue
            ref = RecordRef.model_validate(pointer)
            if ref.scope != scope:
                continue
            raw = required_record(tx, ref)
            if raw["kind"] != candidate.kind:
                continue
            memory = MemoryRef.model_validate(raw["ref"])
            if self.final_guard(tx, ctx, (memory,), "recall").items[0].decision != "allowed":
                continue
            item = self.decode(tx, raw)
            if canonical_text(item.content) != canonical_text(candidate.text):
                continue
            if candidate.kind == "semantic":
                return item
            relation = tx.read("remember_relations", key) or {}
            same_event = candidate.event_key and relation.get("event_key") == candidate.event_key
            same_evidence = {fingerprint(e) for e in relation.get("evidence", [])} & {
                fingerprint(e.model_dump(mode="json")) for e in candidate.evidence
            }
            if same_event or same_evidence:
                return item
        return None

    async def save(self, ctx: TrustedContext, request: RememberRequest) -> RememberReceipt:
        scope = select_scope(ctx, request.selection)
        with self.uow.transaction() as tx:
            key, previous = self.replay(tx, ctx, "remember.save", request)
            ref = MemoryRef(scope=scope, memory_id=key, version=1)
            self.identity.authorize(tx, ctx, Permission.WRITE, memory_ref(ref))
            if previous:
                return RememberReceipt.model_validate(previous["result"])
        document = None
        if request.content.kind == "document":
            reader = self.documents.get(request.content.provider_id)
            if reader is None:
                raise FoundationError(ErrorCode.INVALID_ARGUMENT, "document provider not allowed")
            if hasattr(reader, "acquire_text"):
                try:
                    prepared = PreparedDocument.model_validate(
                        await reader.acquire_text(ctx, request.content)
                    )
                except ValueError as exc:
                    raise FoundationError(
                        ErrorCode.CONTRACT_VIOLATION, "invalid P2 prepared document"
                    ) from exc
                except (OSError, TimeoutError) as exc:
                    raise FoundationError(
                        ErrorCode.DEPENDENCY_UNAVAILABLE, "P2 file service unavailable"
                    ) from exc
                if prepared.document != request.content:
                    raise FoundationError(ErrorCode.CONTRACT_VIOLATION, "document binding mismatch")
                document = prepared.model_dump(mode="json", exclude={"parsed_text"})
                raw = prepared.parsed_text.encode("utf-8")
            else:
                try:
                    raw = await reader.read(ctx, request.content)
                except (OSError, TimeoutError) as exc:
                    raise FoundationError(
                        ErrorCode.DEPENDENCY_UNAVAILABLE, "P2 file service unavailable"
                    ) from exc
            if not isinstance(raw, bytes) or len(raw) > self.max_input_bytes:
                raise FoundationError(ErrorCode.INVALID_ARGUMENT, "invalid text document size")
            try:
                text = raw.decode("utf-8")
            except UnicodeError as exc:
                raise FoundationError(
                    ErrorCode.INVALID_ARGUMENT, "document must be UTF-8 text"
                ) from exc
            if document is None and text_hash(text) != request.content.expected_hash:
                raise FoundationError(ErrorCode.CONTRACT_VIOLATION, "text document hash mismatch")
        else:
            text = request.content.text
        if not text.strip() or len(text.encode("utf-8")) > self.max_input_bytes:
            raise FoundationError(ErrorCode.INVALID_ARGUMENT, "text exceeds input policy")
        with self.uow.transaction() as tx:
            self.identity.authorize(tx, ctx, Permission.WRITE, memory_ref(ref))
            duplicate = self.source_replay(tx, ctx, request, scope, text, key)
            if duplicate is not None:
                return duplicate
        await self.bodies.persist(ctx, scope, text)
        summarized = self.summaries.needed(text, request.content.kind == "document")
        source_ref = SourceRef(
            source_id=fingerprint([key, "source"]),
            source_version=1,
            content_hash=text_hash(text),
            locator=self.bodies.location(scope, text).object_key,
        )
        working_text = (
            self.summaries.descriptor(source_ref, text, request.task_context)
            if summarized
            else text
        )
        if summarized:
            await self.bodies.persist(ctx, scope, working_text)
        with self.uow.transaction() as tx:
            key, previous = self.replay(tx, ctx, "remember.save", request)
            self.identity.authorize(tx, ctx, Permission.WRITE, memory_ref(ref))
            if previous:
                return RememberReceipt.model_validate(previous["result"])
            duplicate = self.source_replay(tx, ctx, request, scope, text, key)
            if duplicate is not None:
                return duplicate
            source = self.new_source(tx, fingerprint([key, "source"]), scope, text, key)
            tx.write(
                "remember_source_input", source.source_id, request.source.model_dump(mode="json")
            )
            tx.write(
                "remember_source_policy",
                source.source_id,
                {
                    "importance_category": request.importance_category,
                    "trigger": request.trigger,
                    "principal_id": ctx.principal.principal_id,
                    "task_context": request.task_context,
                },
            )
            if document is not None:
                tx.write("remember_source_documents", source.source_id, document)
            item = self.new_memory(tx, key, scope, working_text, (source,), MemoryKind.WORKING)
            value, reason = importance(request.importance_category)
            item = item.model_copy(
                update={
                    "importance": value,
                    "importance_reason": reason,
                    "importance_policy_version": self.policy.version,
                }
            )
            self.put(tx, item)
            tx.write(
                "remember_pending",
                key,
                {
                    "ref": item.ref.model_dump(mode="json"),
                    "tokens": self.tokenizer.count(text),
                    "created_at": item.created_at,
                    "state": "waiting_summary" if summarized else "pending",
                    "context": ctx.model_dump(mode="json"),
                },
            )
            if summarized:
                self.summaries.register(tx, item, request.task_context)
                task_ids = [self.enqueue(tx, ctx, item, "remember.summarize")]
            else:
                task_ids = list(self.schedule(tx, ctx, scope, force=request.trigger != "observe"))
            if not summarized and len(text.encode("utf-8")) >= self.policy.compression_min_bytes:
                task_ids.append(self.enqueue(tx, ctx, item, "remember.compress"))
            self.emit(tx, ctx, item, "saved")
            result = RememberReceipt(
                operation_id=ctx.operation_id,
                saved=True,
                source=source,
                memories=(item.ref,),
                task_ids=tuple(task_ids),
                phase="saved",
            )
            self.remember_result(tx, key, request, result)
            tx.write(
                "remember_source_receipts",
                source_identity(scope, request),
                {
                    "signature": source_signature(request, text),
                    "result": result.model_dump(mode="json"),
                },
            )
        with self.uow.transaction() as tx:
            allowed = (
                self.final_guard(tx, ctx, (item.ref,), "recall").items[0].decision == "allowed"
            )
        cache = await self.bodies.admit(scope, working_text) if allowed else "ineligible"
        with self.uow.transaction() as tx:
            still_allowed = (
                self.final_guard(tx, ctx, (item.ref,), "recall").items[0].decision == "allowed"
            )
        if not still_allowed and self.bodies.cache:
            # Reads always guard metadata, including while this compensating delete retries.
            try:
                await self.bodies.cache.delete(scope, item.content_hash)
                cache = "invalidated"
            except Exception:
                cache = "cleanup_pending"
                with self.uow.transaction() as tx:
                    self.enqueue(tx, ctx, self.current(tx, key), "remember.cleanup")
        with self.uow.transaction() as tx:
            tx.write(
                "remember_cache_admission",
                key,
                {"state": cache, "bytes": len(working_text.encode("utf-8"))},
            )
        return result

    def schedule(
        self, tx: SQLiteTransaction, ctx: TrustedContext, scope: Scope, *, force: bool = False
    ) -> tuple[str, ...]:
        pending = [
            (key, row)
            for key, row in tx.rows("remember_pending")
            if row["state"] == "pending" and row["ref"]["scope"] == scope.model_dump(mode="json")
        ]
        pending.sort(key=lambda x: (x[1]["created_at"], x[0]))
        if not pending:
            return ()
        due = (
            later(pending[0][1]["created_at"], self.policy.consolidation_seconds)
            <= self.identity.clock()
        )
        if not (
            force
            or due
            or len(pending) >= self.policy.consolidation_messages
            or sum(r["tokens"] for _, r in pending) >= self.policy.consolidation_tokens
        ):
            return ()
        # Bound one task's input count; subsequent ticks pick up the remainder.
        pending = pending[: self.policy.consolidation_messages]
        items = []
        for key, row in pending:
            item = self.current(tx, key)
            if self.final_guard(tx, ctx, (item.ref,), "recall").items[0].decision == "allowed":
                items.append(item)
            else:
                tx.write("remember_pending", key, {**row, "state": "obsolete"})
        if not items:
            return ()
        task_id = self.enqueue(tx, ctx, items[0], "remember.extract")
        tx.write(
            "remember_batches", task_id, {"refs": [x.ref.model_dump(mode="json") for x in items]}
        )
        for item in items:
            row = tx.read("remember_pending", item.ref.memory_id)
            tx.write(
                "remember_pending",
                item.ref.memory_id,
                {**row, "state": "scheduled", "task_id": task_id},
            )
        return (task_id,)

    def consolidate(self, ctx: TrustedContext, selection: ScopeSelector) -> tuple[str, ...]:
        scope = select_scope(ctx, selection)
        with self.uow.transaction() as tx:
            self.identity.revalidate(tx, ctx)
            return self.schedule(tx, ctx, scope, force=True)

    def reprocess(self, ctx: TrustedContext, memory_id: str) -> str:
        with self.uow.transaction() as tx:
            item = self.current(tx, memory_id)
            self.identity.authorize(tx, ctx, Permission.WRITE, memory_ref(item.ref))
            if self.final_guard(tx, ctx, (item.ref,), "recall").items[0].decision != "allowed":
                raise FoundationError(
                    ErrorCode.MEMORY_GONE, "inactive memory cannot be reprocessed"
                )
            kind = "remember.extract" if item.kind == MemoryKind.WORKING else "remember.project"
            summary = tx.read("remember_working_summaries", memory_id)
            if (
                item.kind == MemoryKind.WORKING
                and summary
                and summary["state"] != "ready"
                and summary["memory"] == item.ref.model_dump(mode="json")
            ):
                kind = "remember.summarize"
            return self.enqueue(tx, ctx, item, kind)

    def distill(self, ctx: TrustedContext, refs: tuple[MemoryRef, ...]) -> str:
        with self.uow.transaction() as tx:
            return self.distill_in(tx, ctx, refs)

    def distill_in(
        self, tx: SQLiteTransaction, ctx: TrustedContext, refs: tuple[MemoryRef, ...]
    ) -> str:
        if not refs or len(refs) > self.policy.consolidation_messages:
            raise FoundationError(ErrorCode.INVALID_ARGUMENT, "invalid review input count")
        if (
            len({r.model_dump_json() for r in refs}) != len(refs)
            or len({r.scope.model_dump_json() for r in refs}) != 1
        ):
            raise FoundationError(
                ErrorCode.INVALID_ARGUMENT, "review needs distinct same-scope memories"
            )
        items = []
        for ref in refs:
            self.identity.authorize(tx, ctx, Permission.WRITE, memory_ref(ref))
            item = self.current(tx, ref.memory_id)
            if (
                item.ref != ref
                or item.kind != MemoryKind.EPISODIC
                or self.final_guard(tx, ctx, (ref,), "recall").items[0].decision != "allowed"
            ):
                raise FoundationError(
                    ErrorCode.INVALID_ARGUMENT,
                    "review requires active current episodic memories",
                )
            items.append(item)
        task_id = self.enqueue(tx, ctx, items[0], "remember.distill")
        batch = {"refs": [r.model_dump(mode="json") for r in refs]}
        prior = tx.read("remember_batches", task_id)
        if prior and prior != batch:
            raise FoundationError(ErrorCode.IDEMPOTENCY_CONFLICT, "review inputs changed")
        tx.write("remember_batches", task_id, batch)
        return task_id

    def processing(self, ctx: TrustedContext, memory_id: str) -> dict[str, Any]:
        with self.uow.transaction() as tx:
            item = self.current(tx, memory_id)
            self.identity.authorize(tx, ctx, Permission.READ, memory_ref(item.ref))
            all_rows = [row["record"] for _, row in tx.rows("tasks")]
            memory_ids = {memory_id}
            selected_tasks: set[str] = set()
            while True:
                before = (len(memory_ids), len(selected_tasks))
                for row in all_rows:
                    batch = tx.read("remember_batches", row["task_id"]) or {"refs": []}
                    if row["subject"]["object_id"] not in memory_ids and not any(
                        r["memory_id"] in memory_ids for r in batch["refs"]
                    ):
                        continue
                    selected_tasks.add(row["task_id"])
                    if row.get("result_ref"):
                        result = required_record(tx, RecordRef.model_validate(row["result_ref"]))
                        memory_ids.update(r["memory_id"] for r in result.get("memories", []))
                if before == (len(memory_ids), len(selected_tasks)):
                    break
            rows = [row for row in all_rows if row["task_id"] in selected_tasks]
            current_rows = [
                r
                for r in rows
                if tx.read(
                    "remember_latest_task", fingerprint([r["subject"]["object_id"], r["kind"]])
                )
                in {None, r["task_id"]}
            ]
            state = (
                "failed"
                if any(r["state"] in {"failed", "attention_required"} for r in current_rows)
                else (
                    "processing"
                    if any(r["state"] not in {"succeeded", "cancelled"} for r in current_rows)
                    else "completed"
                )
            )
            pending = tx.read("remember_pending", memory_id)
            if pending and pending["state"] == "pending" and item.status == MemoryStatus.ACTIVE:
                state = "awaiting_consolidation"
            if (
                pending
                and pending["state"] == "awaiting_extractor"
                and item.status == MemoryStatus.ACTIVE
            ):
                state = "awaiting_extraction_provider"
            summary = tx.read("remember_working_summaries", memory_id)
            if summary and summary["state"] == "failed" and state == "completed":
                state = "completed_with_summary_failure"
            return {
                "memory": item.ref.model_dump(mode="json"),
                "state": state,
                "state_basis": "latest_task_per_memory_and_kind",
                "historical_failed_tasks": sum(
                    r["state"] in {"failed", "attention_required"} for r in rows
                ),
                "memory_status": item.status.value,
                "projection_state": item.projection_state.value,
                "derived_memory_ids": sorted(memory_ids - {memory_id}),
                "tasks": [
                    {k: r.get(k) for k in ("task_id", "kind", "state", "error_code", "result_ref")}
                    for r in rows
                ],
                "artifact": tx.read("remember_artifacts", self.refkey(item.ref)),
                "working_summary": summary,
                "sources": [s.model_dump(mode="json") for s in item.sources],
                "physical_erasure": False,
                "source_retention": "retained",
            }

    def periodic(self) -> int:
        count = self.retention.periodic() if hasattr(self, "retention") else 0
        if hasattr(self, "reflection"):
            count += self.reflection.periodic()
        with self.uow.transaction() as tx:
            pending = [
                (key, row) for key, row in tx.rows("remember_pending") if row["state"] == "pending"
            ]
        seen = set()
        for _, row in pending:
            scope = MemoryRef.model_validate(row["ref"]).scope
            scope_key = fingerprint(scope.model_dump(mode="json"))
            if scope_key in seen:
                continue
            seen.add(scope_key)
            ctx = TrustedContext.model_validate(row["context"])
            ctx = ctx.model_copy(
                update={
                    "deadline_at": later(self.identity.clock(), self.processing_seconds),
                    "operation_id": fingerprint(["consolidate", row["ref"]]),
                }
            )
            try:
                with self.uow.transaction() as tx:
                    self.identity.revalidate(tx, ctx)
                    count += len(self.schedule(tx, ctx, scope))
            except FoundationError:
                # Revoked principals must never run using maintenance authority.
                continue
        return count

    async def correct_async(
        self, ctx: TrustedContext, memory_id: str, request: CorrectionRequest
    ) -> RememberReceipt:
        with self.uow.transaction() as tx:
            item = self.current(tx, memory_id)
            self.identity.authorize(tx, ctx, Permission.CORRECT, memory_ref(item.ref))
        if (
            not request.content.strip()
            or len(request.content.encode("utf-8")) > self.max_input_bytes
        ):
            raise FoundationError(ErrorCode.INVALID_ARGUMENT, "correction exceeds input policy")
        await self.bodies.persist(ctx, item.ref.scope, request.content)
        if item.kind == MemoryKind.WORKING:
            with self.uow.transaction() as tx:
                key, _ = self.replay(tx, ctx, "correct_" + memory_id, request)
                prior = tx.read("remember_working_summaries", memory_id)
            if prior or self.summaries.needed(request.content, request.source.kind == "document"):
                source = SourceRef(
                    source_id=fingerprint([key, "correction_source"]),
                    source_version=1,
                    content_hash=text_hash(request.content),
                    locator=self.bodies.location(item.ref.scope, request.content).object_key,
                )
                body = self.summaries.descriptor(
                    source, request.content, (prior or {}).get("task_context", "")
                )
                await self.bodies.persist(ctx, item.ref.scope, body)
        return self.correct(ctx, memory_id, request)

    def working_correction_body(
        self,
        tx: SQLiteTransaction,
        ctx: TrustedContext,
        item: MemorySnapshot,
        source: SourceRef,
        request: CorrectionRequest,
    ) -> str:
        prior = tx.read("remember_working_summaries", item.ref.memory_id)
        summarize = bool(prior) or self.summaries.needed(
            request.content, request.source.kind == "document"
        )
        new_ref = item.ref.model_copy(update={"version": item.ref.version + 1})
        if summarize:
            task_context = (prior or {}).get("task_context", "")
            body = self.summaries.descriptor(source, request.content, task_context)
            self.summaries.register(
                tx, item.model_copy(update={"ref": new_ref, "sources": (source,)}), task_context
            )
        else:
            body = request.content
        tx.write(
            "remember_pending",
            item.ref.memory_id,
            {
                "ref": new_ref.model_dump(mode="json"),
                "tokens": self.tokenizer.count(request.content),
                "created_at": self.identity.clock(),
                "context": ctx.model_dump(mode="json"),
                "state": "waiting_summary" if summarize else "pending",
            },
        )
        return body

    def working_task_kind(self, tx: SQLiteTransaction, item: MemorySnapshot) -> str:
        row = tx.read("remember_working_summaries", item.ref.memory_id)
        if row and row["memory"] == item.ref.model_dump(mode="json") and row["state"] == "pending":
            return "remember.summarize"
        return "remember.extract"

    async def hydrate(
        self, ctx: TrustedContext, refs: tuple[MemoryRef, ...], purpose: str = "recall"
    ) -> None:
        # Authorize before object-store reads; provider keys come only from authority rows.
        locations = []
        with self.uow.transaction() as tx:
            for ref in refs:
                self.identity.authorize(tx, ctx, Permission.READ, memory_ref(ref))
                if self.final_guard(tx, ctx, (ref,), purpose).items[0].decision != "allowed":
                    continue
                raw = tx.get(memory_ref(ref, versioned=True))
                if raw and "body_location" in raw:
                    locations.append(
                        (ref.scope, ResourceLocation.model_validate(raw["body_location"]))
                    )
        for scope, location in locations:
            await self.bodies.read(scope, location)

    async def load_async(self, ctx: TrustedContext, refs: tuple[MemoryRef, ...]) -> MemoryReadBatch:
        await self.hydrate(ctx, refs)
        return self.load(ctx, refs)

    async def working_async(
        self, ctx: TrustedContext, selection: ScopeSelector, page: PageRequest
    ) -> MemoryReadBatch:
        scope = select_scope(ctx, selection)
        with self.uow.transaction() as tx:
            self.identity.revalidate(tx, ctx)
            selected = []
            for key, pointer in tx.rows("remember_current"):
                from aether_agent_memory.runtime.contracts.models import RecordRef

                record_ref = RecordRef.model_validate(pointer)
                if not self.identity.discoverable(tx, ctx, record_ref, selection):
                    continue
                raw = required_record(tx, record_ref)
                if raw["kind"] == "working":
                    selected.append((key, MemoryRef.model_validate(raw["ref"])))
            refs, cursor = tx.page(selected, ["working", scope.model_dump(mode="json")], page)
        batch = await self.load_async(ctx, tuple(refs))
        return batch.model_copy(update={"next_cursor": cursor})

    async def read_body(self, ctx: TrustedContext, ref: MemoryRef) -> FullBodyReadResult:
        await self.hydrate(ctx, (ref,))
        with self.uow.transaction() as tx:
            eligibility = self.final_guard(tx, ctx, (ref,), "recall")
            if eligibility.items[0].decision != "allowed":
                return FullBodyReadResult(
                    memory=ref,
                    outcome="excluded",
                    path="none",
                    reason_code=eligibility.items[0].reason,
                )
            item = self.current(tx, ref.memory_id)
            location = self.bodies.location(ref.scope, item.content)
        content, path = await self.bodies.read(ref.scope, location)
        with self.uow.transaction() as tx:
            eligible = self.final_guard(tx, ctx, (ref,), "recall").items[0]
            if eligible.decision != "allowed" or eligible.checked_revision != item.object_revision:
                return FullBodyReadResult(
                    memory=ref, outcome="stale", path="none", reason_code="changed_during_read"
                )
            guard = GuardStamp(
                memory=ref,
                object_revision=item.object_revision,
                relations_revision=item.revision,
                authorization_epoch=ctx.principal.auth_epoch,
                body_hash=item.content_hash,
                checked_at=self.identity.clock(),
            )
        return FullBodyReadResult(
            memory=ref,
            outcome="read",
            content=content,
            sources=item.sources,
            location=location.model_copy(update={"kind": "cache"}) if path == "cache" else location,
            guard=guard,
            path=path,
            reason_code="verified_full_body",
        )

    def accepts_projection(self, target: ProjectionTarget) -> bool:
        with self.uow.transaction() as tx:
            raw = tx.read("remember_manifests", self.refkey(target.memory))
            if raw is None:
                return target.generation is None
            manifest = ProjectionManifest.model_validate(raw)
            return (
                manifest.state == "ready"
                and manifest.generation == target.generation
                and manifest.body_hash == target.body_hash
                and manifest.model_space == target.model_space
                and any(
                    c.vector_id == target.vector_id
                    and c.input_hash == target.input_hash
                    and c.chunk_index == target.chunk_index
                    for c in manifest.chunks
                )
            )

    async def read_range(
        self, ctx: TrustedContext, ref: MemoryRef, start: int = 0, end: int | None = None
    ) -> dict[str, Any]:
        result = await self.read_body(ctx, ref)
        if result.outcome != "read":
            return {
                "memory": ref.model_dump(mode="json"),
                "outcome": result.outcome,
                "reason_code": result.reason_code,
                "content": None,
            }
        text = result.content
        if text is None or result.guard is None:
            raise FoundationError(ErrorCode.CONTRACT_VIOLATION, "read lacks body or guard")
        end = len(text) if end is None else end
        if not 0 <= start <= end <= len(text):
            raise FoundationError(ErrorCode.INVALID_ARGUMENT, "invalid Unicode character range")
        return {
            "memory": ref.model_dump(mode="json"),
            "outcome": "read",
            "representation": "full_text" if start == 0 and end == len(text) else "text_range",
            "start_char": start,
            "end_char": end,
            "total_chars": len(text),
            "is_complete": start == 0 and end == len(text),
            "content": text[start:end],
            "body_hash": result.guard.body_hash,
            "range_hash": text_hash(text[start:end]),
            "sources": [s.model_dump(mode="json") for s in result.sources],
        }

    async def read_source(
        self, ctx: TrustedContext, source: SourceRef, start: int = 0, end: int | None = None
    ) -> dict[str, Any]:
        """Agent/file-tool entry point. Source reads do not count as packed memory use."""
        return await self.source_access.read(ctx, source, start, end)

    async def run(self, ctx: TrustedContext, task: TaskRecord) -> RunResult:
        with self.uow.transaction() as tx:
            saved_policy = tx.read("remember_task_policy", task.task_id)
        token = _task_policy.set(
            RememberPolicy.model_validate(saved_policy) if saved_policy else self._policy
        )
        try:
            return await self.run_bound(ctx, task)
        finally:
            _task_policy.reset(token)

    async def run_bound(self, ctx: TrustedContext, task: TaskRecord) -> RunResult:
        with self.uow.transaction() as tx:
            self.tasks.guard(tx, task)
            raw = required_record(tx, task.input_ref)
            ref = MemoryRef.model_validate(raw["ref"])
            batch = tx.read("remember_batches", task.task_id)
            refs = tuple(MemoryRef.model_validate(x) for x in batch["refs"]) if batch else (ref,)
        maintenance = task.kind in {"remember.cleanup", "remember.revalidate"}
        await self.hydrate(ctx, refs, "cleanup" if maintenance else "recall")
        with self.uow.transaction() as tx:
            self.tasks.guard(tx, task)
            valid = self.final_guard(tx, ctx, refs, "cleanup" if maintenance else "recall")
            if any(x.decision != "allowed" for x in valid.items):
                if task.kind == "remember.extract" and batch:
                    retry_ctx = ctx.model_copy(
                        update={"operation_id": fingerprint([task.task_id, "remaining_inputs"])}
                    )
                    for eligibility in valid.items:
                        key = eligibility.ref.memory_id
                        row = tx.read("remember_pending", key)
                        if row and row.get("task_id") == task.task_id:
                            tx.write(
                                "remember_pending",
                                key,
                                {
                                    **row,
                                    "state": "pending"
                                    if eligibility.decision == "allowed"
                                    else "obsolete",
                                    "context": retry_ctx.model_dump(mode="json"),
                                },
                            )
                    self.schedule(tx, retry_ctx, refs[0].scope, force=True)
                return RunResult(
                    outcome="obsolete",
                    effect_status=EffectStatus.NO_EFFECT,
                    reason="input no longer eligible",
                )
            items = tuple(
                self.decode(tx, required_record(tx, memory_ref(r, versioned=True))) for r in refs
            )
        if task.kind == "remember.summarize":
            return await self.summaries.process(ctx, task, items[0])
        if task.kind == "remember.extract":
            return await self.extract_batch(
                ctx, task, await self.source_access.originals(ctx, items)
            )
        if task.kind == "remember.revalidate":
            return await self.revalidate_sources(ctx, task, items[0])
        if task.kind == "remember.distill":
            if not hasattr(self.extraction, "review_episodes"):
                return RunResult(
                    outcome="failed",
                    effect_status=EffectStatus.NO_EFFECT,
                    reason="episodic review provider not configured",
                )
            originals = []
            seen = set()
            for item in items:
                for source in item.sources:
                    if source.source_id in seen:
                        continue
                    seen.add(source.source_id)
                    with self.uow.transaction() as tx:
                        row = tx.read("remember_sources", source.source_id)
                    if "original_location" in row:
                        text, _ = await self.bodies.read(
                            item.ref.scope,
                            ResourceLocation.model_validate(row["original_location"]),
                        )
                    else:
                        text = row["text"]
                    originals.append(
                        item.model_copy(
                            update={
                                "content": text,
                                "content_hash": source.content_hash,
                                "sources": (source,),
                            }
                        )
                    )
            # Do not deduplicate episodes that share an original: each version/result
            # remains part of the review. Source originals are only the evidence pool.
            if (
                sum(self.tokenizer.count(i.content) for i in (*items, *originals))
                > self.policy.extraction_chunk_tokens
            ):
                return RunResult(
                    outcome="failed",
                    effect_status=EffectStatus.NO_EFFECT,
                    reason="review input budget exceeded; select a smaller evidence set",
                )
            with self.uow.transaction() as tx:
                self.tasks.guard(tx, task)
                tx.write(
                    "remember_review_context",
                    task.task_id,
                    {"episodes": [i.model_dump(mode="json") for i in items]},
                )
            return await self.extract_batch(ctx, task, tuple(originals))
        if task.kind == "remember.compress":
            source_items = await self.source_access.originals(ctx, items)
            return await self.compress(ctx, task, source_items[0])
        if task.kind == "remember.cleanup":
            return await self.cleanup(ctx, task, items[0])
        return await self.project(ctx, task, items[0])

    def consume_call(self, task: TaskRecord) -> None:
        with self.uow.transaction() as tx:
            self.tasks.guard(tx, task)
            count = tx.read("remember_model_calls", task.task_id) or 0
            if count >= self.policy.max_model_calls:
                raise FoundationError(ErrorCode.CONTRACT_VIOLATION, "model call budget exhausted")
            tx.write("remember_model_calls", task.task_id, count + 1)

    async def compress(
        self, ctx: TrustedContext, task: TaskRecord, item: MemorySnapshot
    ) -> RunResult:
        result: dict[str, Any] = {
            "quality": "failed",
            "published": False,
            "reason": "compression_or_quality_provider_not_configured",
            "target_ratio": self.policy.compression_target_ratio,
            "original_bytes": len(item.content.encode("utf-8")),
        }
        if self.compressor is not None and self.quality is not None:
            parts = chunks(item.content, self.tokenizer.count, self.policy.extraction_chunk_tokens)
            compressed, reports = [], []
            for index, (start, end, text) in enumerate(parts):
                checkpoint_key = fingerprint([task.task_id, index, text_hash(text)])
                with self.uow.transaction() as tx:
                    checkpoint = tx.read("remember_compression_parts", checkpoint_key)
                    if checkpoint is not None:
                        self.tasks.progress.part(
                            tx,
                            ctx,
                            task,
                            "compression",
                            "remember_compression_parts",
                            checkpoint_key,
                            config_version=self.policy.version,
                        )
                if checkpoint is None:
                    self.consume_call(task)
                    output = await self.compressor.compress(ctx, text)
                    self.consume_call(task)
                    quality = await self.quality.verify(ctx, text, output.text)
                    checkpoint = {
                        "text": output.text,
                        "strategy": output.strategy,
                        "quality": quality.model_dump(mode="json"),
                        "start_char": start,
                        "end_char": end,
                    }
                    with self.uow.transaction() as tx:
                        self.tasks.guard(tx, task)
                        tx.write("remember_compression_parts", checkpoint_key, checkpoint)
                        self.tasks.progress.part(
                            tx,
                            ctx,
                            task,
                            "compression",
                            "remember_compression_parts",
                            checkpoint_key,
                            config_version=self.policy.version,
                        )
                compressed.append(checkpoint["text"])
                reports.append(checkpoint)
            output_text = "\n".join(compressed)
            size = len(output_text.encode("utf-8"))
            ratio = result["original_bytes"] / size if size else 0
            quality_ok = bool(size) and all(
                r["quality"]["passed"]
                and not r["quality"].get("critical_failures")
                and not r["quality"].get("critical_unknowns")
                for r in reports
            )
            ratio_met = ratio >= self.policy.compression_target_ratio
            result.update(
                ratio=ratio,
                ratio_met=ratio_met,
                stored_bytes=size,
                token_ratio=self.tokenizer.count(item.content)
                / max(1, self.tokenizer.count(output_text)),
                quality_evidence=[r["quality"] for r in reports],
                covered_ranges=[[r["start_char"], r["end_char"]] for r in reports],
                strategy="chunked_quality_v3",
                reason="quality_rejected" if not quality_ok else "ratio_unmet",
                declared_use="locator_only"
                if any(r["quality"].get("declared_use") == "locator_only" for r in reports)
                else "supported_summary",
            )
            if quality_ok and (ratio_met or not self.policy.compression_require_ratio):
                location = await self.bodies.persist(ctx, item.ref.scope, output_text)
                result.update(
                    quality="passed",
                    published=True,
                    reason="quality_and_ratio_passed" if ratio_met else "usable_ratio_unmet",
                    location=location.model_copy(update={"kind": "artifact"}).model_dump(
                        mode="json"
                    ),
                )
        with self.uow.transaction() as tx:
            self.tasks.guard(tx, task)
            if self.final_guard(tx, ctx, (item.ref,), "recall").items[0].decision != "allowed":
                return RunResult(
                    outcome="obsolete",
                    effect_status=EffectStatus.NO_EFFECT,
                    reason="source changed during compression",
                )
            tx.write(
                "remember_artifacts",
                self.refkey(item.ref),
                {
                    **result,
                    "task_id": task.task_id,
                    "memory": item.ref.model_dump(mode="json"),
                    "source_hash": item.content_hash,
                    "policy_version": self.policy.version,
                },
            )
            if result["published"]:
                updated = self.change(tx, self.current(tx, item.ref.memory_id))
                self.emit(tx, ctx, updated, "artifact_ready")
            return self.finish(tx, ctx, task, result)

    def validate_candidate(
        self, candidate: CandidateFact, items: tuple[MemorySnapshot, ...]
    ) -> CandidateFact:
        originals = {s.source_id: (s, item.content) for item in items for s in item.sources}
        if candidate.evidence_status != "supported" or not candidate.text.strip():
            raise FoundationError(ErrorCode.CONTRACT_VIOLATION, "unsupported candidate")
        if any(
            s.source_id not in originals or originals[s.source_id][0] != s
            for s in candidate.sources
        ):
            raise FoundationError(ErrorCode.CONTRACT_VIOLATION, "candidate names unrelated sources")
        evidence = list(candidate.evidence)
        if not evidence:
            # Legacy adapters can only promote exact excerpts without explicit evidence.
            for source in candidate.sources:
                text = originals[source.source_id][1]
                start = text.find(candidate.text)
                if start >= 0:
                    evidence.append(
                        FactEvidence(
                            source=source,
                            start_char=start,
                            end_char=start + len(candidate.text),
                            quote=candidate.text,
                        )
                    )
        for entry in evidence:
            bound_source, text = originals.get(entry.source.source_id, (None, ""))
            if (
                bound_source != entry.source
                or text[entry.start_char : entry.end_char] != entry.quote
            ):
                raise FoundationError(
                    ErrorCode.CONTRACT_VIOLATION, "candidate evidence slice mismatch"
                )
        if {s.source_id for s in candidate.sources} != {e.source.source_id for e in evidence}:
            raise FoundationError(ErrorCode.CONTRACT_VIOLATION, "every source needs exact evidence")
        return candidate.model_copy(update={"evidence": tuple(evidence)})

    async def candidates(
        self, ctx: TrustedContext, task: TaskRecord, items: tuple[MemorySnapshot, ...]
    ) -> tuple[CandidateFact, ...]:
        with self.uow.transaction() as tx:
            stored = tx.read("remember_candidates", task.task_id)
        if stored:
            return tuple(
                self.validate_candidate(CandidateFact.model_validate(c), items)
                for c in stored["candidates"]
            )
        found: list[CandidateFact] = []
        if task.kind == "remember.distill":
            with self.uow.transaction() as tx:
                review = tx.read("remember_review_context", task.task_id)
            episodes = tuple(MemorySnapshot.model_validate(i) for i in review["episodes"])
            self.consume_call(task)
            output = ExtractionResult.model_validate(
                await cast(Any, self.extraction).review_episodes(
                    ctx, episodes, items, self.policy.version
                )
            )
            found.extend(c for c in output.candidates if c.kind == "semantic")
        elif hasattr(self.extraction, "extract_batch"):
            found.extend(await self.extract_inputs(ctx, items, task=task))
        else:
            for item in items:
                if type(self.extraction) is LiteralExtraction:
                    with self.uow.transaction() as tx:
                        summary = tx.read("remember_working_summaries", item.ref.memory_id)
                        if summary:
                            # A file is not automatically one giant event. The literal
                            # short-text demo adapter cannot judge a document's value.
                            deferred = tx.read("remember_deferred_extraction", task.task_id) or []
                            tx.write(
                                "remember_deferred_extraction",
                                task.task_id,
                                list(dict.fromkeys([*deferred, item.ref.memory_id])),
                            )
                    if summary:
                        continue
                pieces = (
                    [(0, len(item.content), item.content)]
                    if type(self.extraction) is LiteralExtraction
                    else chunks(
                        item.content, self.tokenizer.count, self.policy.extraction_chunk_tokens
                    )
                )
                for offset, _, piece in pieces:
                    checkpoint_key = fingerprint(
                        [task.task_id, item.ref.model_dump(mode="json"), offset, text_hash(piece)]
                    )
                    with self.uow.transaction() as tx:
                        checkpoint = tx.read("remember_extraction_parts", checkpoint_key)
                        if checkpoint is not None:
                            self.tasks.progress.part(
                                tx,
                                ctx,
                                task,
                                "extraction",
                                "remember_extraction_parts",
                                checkpoint_key,
                                config_version=self.policy.version,
                            )
                    if checkpoint is None:
                        for repair in range(3):
                            self.consume_call(task)
                            try:
                                output = ExtractionResult.model_validate(
                                    await self.extraction.extract(
                                        ctx,
                                        ExtractionRequest(
                                            source=item.sources[0],
                                            text=piece,
                                            existing=(),
                                            policy_version=self.policy.version,
                                        ),
                                    )
                                )
                                break
                            except ValueError:
                                if repair == 2:
                                    raise
                        with self.uow.transaction() as tx:
                            self.tasks.guard(tx, task)
                            tx.write(
                                "remember_extraction_parts",
                                checkpoint_key,
                                output.model_dump(mode="json"),
                            )
                            self.tasks.progress.part(
                                tx,
                                ctx,
                                task,
                                "extraction",
                                "remember_extraction_parts",
                                checkpoint_key,
                                config_version=self.policy.version,
                            )
                    else:
                        output = ExtractionResult.model_validate(checkpoint)
                    for candidate in output.candidates:
                        evidence = tuple(
                            e.model_copy(
                                update={
                                    "start_char": e.start_char + offset,
                                    "end_char": e.end_char + offset,
                                }
                            )
                            for e in candidate.evidence
                        )
                        found.append(candidate.model_copy(update={"evidence": evidence}))
        merged: dict[Any, CandidateFact] = {}
        for candidate in found:
            candidate = self.validate_candidate(candidate, items)
            quotes = "\n".join(e.quote for e in candidate.evidence)
            if candidate.text not in quotes:
                if self.support_verifier is None:
                    raise FoundationError(
                        ErrorCode.CONTRACT_VIOLATION,
                        "paraphrased candidate requires an independent support verifier",
                    )
                self.consume_call(task)
                if not await self.support_verifier.verify(ctx, candidate, quotes):
                    raise FoundationError(
                        ErrorCode.CONTRACT_VIOLATION, "evidence does not support claim"
                    )
            # Identical words in distinct events or source ranges are not one occurrence.
            identity = (
                candidate.event_key
                or tuple((e.source.source_id, e.start_char, e.end_char) for e in candidate.evidence)
                if candidate.kind == "episodic"
                else candidate.fact_key
            )
            key = (
                (candidate.kind, identity)
                if candidate.event_key
                else (
                    candidate.kind,
                    canonical_text(candidate.text),
                    identity if candidate.kind == "episodic" else None,
                )
            )
            if key in merged:
                old = merged[key]
                candidate = candidate.model_copy(
                    update={
                        "sources": tuple(
                            {
                                s.model_dump_json(): s for s in (*old.sources, *candidate.sources)
                            }.values()
                        ),
                        "text": old.text
                        if canonical_text(old.text) == canonical_text(candidate.text)
                        or candidate.text in old.text
                        else candidate.text
                        if old.text in candidate.text
                        else old.text + "\n" + candidate.text,
                        "evidence": tuple(
                            {
                                e.model_dump_json(): e for e in (*old.evidence, *candidate.evidence)
                            }.values()
                        ),
                    }
                )
            merged[key] = candidate
        if len(merged) > self.policy.max_candidates:
            raise FoundationError(
                ErrorCode.CONTRACT_VIOLATION,
                "candidate budget exceeded; input requires smaller batches",
            )
        with self.uow.transaction() as tx:
            self.tasks.guard(tx, task)
            tx.write(
                "remember_candidates",
                task.task_id,
                {"candidates": [c.model_dump(mode="json") for c in merged.values()]},
            )
        return tuple(merged.values())

    async def extract_inputs(
        self,
        ctx: TrustedContext,
        items: tuple[MemorySnapshot, ...],
        allow_artifacts: bool = True,
        task: TaskRecord | None = None,
    ) -> list[CandidateFact]:
        """Bound model inputs, preserving original source-relative evidence offsets."""
        units: list[tuple[MemorySnapshot, int, int, str | None]] = []
        used_artifact = False
        budget = self.policy.extraction_chunk_tokens
        for item in items:
            summary = None
            if allow_artifacts and getattr(self.extraction, "supports_representations", False):
                with self.uow.transaction() as tx:
                    artifact = tx.read("remember_artifacts", self.refkey(item.ref))
                if (
                    artifact
                    and artifact["published"]
                    and artifact.get("declared_use", "supported_summary") == "supported_summary"
                    and artifact["source_hash"] == item.content_hash
                ):
                    summary, _ = await self.bodies.read(
                        item.ref.scope, ResourceLocation.model_validate(artifact["location"])
                    )
                    if self.tokenizer.count(summary) > budget:
                        summary = None
            if summary:
                units.append((item, 0, self.tokenizer.count(summary), summary))
                used_artifact = True
            else:
                for start, _, piece in chunks(item.content, self.tokenizer.count, budget):
                    units.append(
                        (
                            item.model_copy(update={"content": piece}),
                            start,
                            self.tokenizer.count(piece),
                            None,
                        )
                    )
        groups: list[list[tuple[MemorySnapshot, int, int, str | None]]] = []
        group: list[tuple[MemorySnapshot, int, int, str | None]] = []
        total = 0
        source_ids: set[str] = set()
        for unit in units:
            ids = {s.source_id for s in unit[0].sources}
            if group and (total + unit[2] > budget or ids & source_ids):
                groups.append(group)
                group, total, source_ids = [], 0, set()
            group.append(unit)
            total += unit[2]
            source_ids.update(ids)
        if group:
            groups.append(group)
        found: list[CandidateFact] = []
        try:
            for group in groups:
                kwargs = {}
                if getattr(self.extraction, "supports_representations", False):
                    kwargs["representations"] = [
                        {"source_id": s.source_id, "text": summary}
                        for item, _, _, summary in group
                        if summary
                        for s in item.sources
                    ]
                checkpoint_key = fingerprint(
                    [
                        task.task_id if task else ctx.operation_id,
                        [
                            (u[0].ref.model_dump(mode="json"), u[1], text_hash(u[0].content), u[3])
                            for u in group
                        ],
                    ]
                )
                with self.uow.transaction() as tx:
                    checkpoint = tx.read("remember_extraction_parts", checkpoint_key)
                    if checkpoint is not None and task is not None:
                        self.tasks.progress.part(
                            tx,
                            ctx,
                            task,
                            "extraction",
                            "remember_extraction_parts",
                            checkpoint_key,
                            config_version=self.policy.version,
                        )
                if checkpoint is None:
                    for repair in range(3):
                        if task is not None:
                            self.consume_call(task)
                        try:
                            output = ExtractionResult.model_validate(
                                await cast(Any, self.extraction).extract_batch(
                                    ctx, tuple(u[0] for u in group), self.policy.version, **kwargs
                                )
                            )
                            break
                        except ValueError:
                            if repair == 2:
                                raise
                    with self.uow.transaction() as tx:
                        if task is not None:
                            self.tasks.guard(tx, task)
                        tx.write(
                            "remember_extraction_parts",
                            checkpoint_key,
                            output.model_dump(mode="json"),
                        )
                        if task is not None:
                            self.tasks.progress.part(
                                tx,
                                ctx,
                                task,
                                "extraction",
                                "remember_extraction_parts",
                                checkpoint_key,
                                config_version=self.policy.version,
                            )
                else:
                    output = ExtractionResult.model_validate(checkpoint)
                offsets = {
                    s.source_id: offset for item, offset, _, _ in group for s in item.sources
                }
                for candidate in output.candidates:
                    evidence = tuple(
                        e.model_copy(
                            update={
                                "start_char": e.start_char + offsets.get(e.source.source_id, 0),
                                "end_char": e.end_char + offsets.get(e.source.source_id, 0),
                            }
                        )
                        for e in candidate.evidence
                    )
                    found.append(
                        self.validate_candidate(
                            candidate.model_copy(update={"evidence": evidence}), items
                        )
                    )
        except (ValueError, FoundationError) as exc:
            if (
                not used_artifact
                or isinstance(exc, FoundationError)
                and exc.code != ErrorCode.CONTRACT_VIOLATION
            ):
                raise
            return await self.extract_inputs(ctx, items, allow_artifacts=False, task=task)
        return found

    async def related(
        self, ctx: TrustedContext, candidate: CandidateFact, scope: Scope
    ) -> tuple[MemorySnapshot, ...]:
        discovered = []
        if self.vector_search is not None:
            # Query is bounded separately; the full candidate remains available to comparison.
            query = chunks(
                candidate.text, self.tokenizer.count, self.policy.projection_chunk_tokens
            )[0][2]
            op = fingerprint([ctx.operation_id, candidate.model_dump(mode="json"), "related"])
            embedded = await self.embedding.embed(
                ctx,
                EmbeddingRequest(
                    operation_id=op,
                    usage="query",
                    texts=(query,),
                    model_space=self.model_space,
                    deadline_at=ctx.deadline_at,
                ),
            )
            if (
                embedded.operation_id != op
                or embedded.usage != "query"
                or embedded.model_space != self.model_space
                or len(embedded.items) != 1
                or embedded.items[0].input_hash != text_hash(query)
            ):
                raise FoundationError(
                    ErrorCode.CONTRACT_VIOLATION, "comparison query embedding mismatch"
                )
            result = await self.vector_search.search(
                ctx,
                VectorSearchRequest(
                    selection=ScopeSelector(
                        **scope.model_dump(
                            exclude={"tenant_id", "application_id", "user_id", "agent_id"}
                        )
                    ),
                    vector=embedded.items[0].vector,
                    model_space=self.model_space,
                    limit=self.policy.comparison_candidates,
                    deadline_at=ctx.deadline_at,
                ),
            )
            if result.coverage == "unavailable":
                raise FoundationError(
                    ErrorCode.DEPENDENCY_UNAVAILABLE, "comparison discovery unavailable"
                )
            discovered = [x.target.memory.memory_id for x in result.candidates]
        with self.uow.transaction() as tx:
            eligible = []
            for key, pointer in tx.rows("remember_current"):
                record_ref = RecordRef.model_validate(pointer)
                if record_ref.scope != scope:
                    continue
                raw = required_record(tx, record_ref)
                if raw["kind"] == "working":
                    continue
                ref = MemoryRef.model_validate(raw["ref"])
                if self.final_guard(tx, ctx, (ref,), "recall").items[0].decision != "allowed":
                    continue
                item = self.decode(tx, raw)
                if self.final_guard(tx, ctx, (item.ref,), "recall").items[0].decision != "allowed":
                    continue
                relation = tx.read("remember_relations", key) or {}
                exact = (
                    canonical_text(item.content) == canonical_text(candidate.text)
                    and item.kind.value == candidate.kind
                )
                stable = bool(
                    candidate.fact_key
                    and relation.get("fact_key") == candidate.fact_key
                    or candidate.event_key
                    and relation.get("event_key") == candidate.event_key
                )
                if (
                    exact
                    or stable
                    or key in discovered
                    or item.projection_state != ProjectionState.READY
                ):
                    eligible.append((0 if exact or stable else 1 if key in discovered else 2, item))
            rank = {key: index for index, key in reversed(list(enumerate(discovered)))}
            eligible.sort(
                key=lambda x: (x[0], rank.get(x[1].ref.memory_id, len(rank)), x[1].ref.memory_id)
            )
            return tuple(x[1] for x in eligible[: self.policy.comparison_candidates])

    async def extract_batch(
        self, ctx: TrustedContext, task: TaskRecord, items: tuple[MemorySnapshot, ...]
    ) -> RunResult:
        candidates = await self.candidates(ctx, task, items)
        if task.kind == "remember.distill":
            candidates = tuple(c for c in candidates if c.kind == "semantic")
        with self.uow.transaction() as tx:
            batch = tx.read("remember_batches", task.task_id)
        refs = (
            tuple(MemoryRef.model_validate(r) for r in batch["refs"])
            if batch
            else tuple(x.ref for x in items)
        )
        for attempt in range(self.policy.max_commit_retries + 1):
            with self.uow.transaction() as tx:
                space_key = self.space_key(items[0].ref.scope)
                expected_space_seq = tx.read("remember_space_seq", space_key) or 0
            proposals: list[tuple[CandidateFact, ComparisonDecision, MemorySnapshot | None]] = []
            virtual: dict[str, MemorySnapshot] = {}
            virtual_relations: dict[str, Any] = {}
            for candidate in candidates:
                existing: tuple[MemorySnapshot, ...]
                with self.uow.transaction() as tx:
                    duplicate = self.duplicate_in(tx, ctx, candidate, items[0].ref.scope)
                if duplicate is not None:
                    existing = (duplicate,)
                    decision = ComparisonDecision(
                        outcome="no_change",
                        target_id=duplicate.ref.memory_id,
                        reason="deterministic_duplicate_v1",
                    )
                else:
                    existing = (
                        *await self.related(ctx, candidate, items[0].ref.scope),
                        *virtual.values(),
                    )
                    context_tokens = self.tokenizer.count(candidate.text) + sum(
                        self.tokenizer.count(x.content) for x in existing
                    )
                    if context_tokens > self.policy.comparison_context_tokens:
                        raise FoundationError(
                            ErrorCode.CONTRACT_VIOLATION,
                            "comparison context budget exceeded; split extraction input",
                        )
                    self.consume_call(task)
                    with self.uow.transaction() as tx:
                        relations = {
                            x.ref.memory_id: tx.read("remember_relations", x.ref.memory_id)
                            or virtual_relations.get(x.ref.memory_id, {})
                            for x in existing
                        }
                    if hasattr(self.comparison, "compare_with_relations"):
                        raw_decision = await self.comparison.compare_with_relations(
                            ctx, candidate, existing, relations
                        )
                    else:
                        raw_decision = await self.comparison.compare(ctx, candidate, existing)
                    decision = ComparisonDecision.model_validate(raw_decision)
                target = next((x for x in existing if x.ref.memory_id == decision.target_id), None)
                if decision.outcome == "create" and candidate.event_key:
                    with self.uow.transaction() as tx:
                        same_events = [
                            x
                            for x in existing
                            if (tx.read("remember_relations", x.ref.memory_id) or {}).get(
                                "event_key"
                            )
                            == candidate.event_key
                            and x.kind == MemoryKind.EPISODIC
                        ]
                    if len(same_events) == 1:
                        target = same_events[0]
                        decision = ComparisonDecision(
                            outcome="no_change"
                            if target.content == candidate.text
                            else "amend"
                            if target.content in candidate.text
                            else "conflict",
                            target_id=target.ref.memory_id,
                            reason="stable_event_identity",
                        )
                if (
                    decision.outcome in {"no_change", "equivalent", "amend", "correct", "conflict"}
                    and target is None
                ):
                    raise FoundationError(
                        ErrorCode.CONTRACT_VIOLATION, "comparison target was not a candidate"
                    )
                if decision.outcome in {"create", "reject"} and decision.target_id is not None:
                    raise FoundationError(
                        ErrorCode.CONTRACT_VIOLATION, "unexpected comparison target"
                    )
                if (
                    decision.outcome == "no_change"
                    and target is not None
                    and (
                        canonical_text(target.content) != canonical_text(candidate.text)
                        or target.kind.value != candidate.kind
                    )
                ):
                    raise FoundationError(
                        ErrorCode.CONTRACT_VIOLATION,
                        "semantic similarity is not proof of duplicate",
                    )
                if decision.outcome == "no_change" and candidate.kind == "episodic":
                    assert target is not None
                    with self.uow.transaction() as tx:
                        relation = tx.read("remember_relations", target.ref.memory_id) or {}
                    same_origin = bool(
                        {fingerprint(e) for e in relation.get("evidence", [])}
                        & {fingerprint(e.model_dump(mode="json")) for e in candidate.evidence}
                    )
                    same_event = bool(
                        candidate.event_key and relation.get("event_key") == candidate.event_key
                    )
                    if not same_origin and not same_event:
                        decision = ComparisonDecision(
                            outcome="create", reason="same_text_does_not_prove_same_event"
                        )
                        target = None
                if decision.outcome == "equivalent":
                    if target is None or target.kind.value != candidate.kind:
                        raise FoundationError(
                            ErrorCode.CONTRACT_VIOLATION, "equivalence target kind mismatch"
                        )
                    if self.equivalence_verifier is None:
                        raise FoundationError(
                            ErrorCode.DEPENDENCY_UNAVAILABLE,
                            "semantic equivalence verifier is not configured",
                        )
                    self.consume_call(task)
                    verdict = EquivalenceVerdict.model_validate(
                        await self.equivalence_verifier.verify(ctx, candidate, target)
                    )
                    with self.uow.transaction() as tx:
                        self.tasks.guard(tx, task)
                        tx.write(
                            "remember_equivalence_checks",
                            fingerprint([task.task_id, attempt, len(proposals)]),
                            verdict.model_dump(mode="json"),
                        )
                        relation = tx.read(
                            "remember_relations", target.ref.memory_id
                        ) or virtual_relations.get(target.ref.memory_id, {})
                    same_event = candidate.kind == "semantic" or bool(
                        candidate.event_key and candidate.event_key == relation.get("event_key")
                    )
                    if not (
                        verdict.equivalent
                        and verdict.same_identity
                        and verdict.preserves_conditions
                        and verdict.candidate_quote == candidate.text
                        and verdict.existing_quote == target.content
                        and same_event
                    ):
                        decision = ComparisonDecision(
                            outcome="create", reason="equivalence_not_verified_preserve_candidate"
                        )
                        target = None
                if decision.outcome == "correct":
                    # Model proposals preserve both facts until an explicit correction.
                    decision = decision.model_copy(
                        update={
                            "outcome": "conflict",
                            "reason": "proposed_correction_requires_explicit_confirmation: "
                            + decision.reason,
                        }
                    )
                if decision.outcome == "amend":
                    assert target is not None
                    with self.uow.transaction() as tx:
                        relation = tx.read("remember_relations", target.ref.memory_id) or {}
                        self.identity.authorize(tx, ctx, Permission.CORRECT, memory_ref(target.ref))
                    if not (
                        candidate.kind == "episodic"
                        and candidate.event_key
                        and relation.get("event_key") == candidate.event_key
                        and target.content in candidate.text
                    ):
                        raise FoundationError(
                            ErrorCode.CONTRACT_VIOLATION,
                            "amend requires same event and supported additive content",
                        )
                proposals.append((candidate, decision, target))
                if decision.outcome in {"create", "conflict"}:
                    virtual_id = fingerprint([task.task_id, len(proposals) - 1])
                    virtual[virtual_id] = items[0].model_copy(
                        update={
                            "ref": items[0].ref.model_copy(
                                update={"memory_id": virtual_id, "version": 1}
                            ),
                            "kind": MemoryKind(candidate.kind),
                            "content": candidate.text,
                            "content_hash": text_hash(candidate.text),
                            "sources": candidate.sources,
                            "revision": 1,
                            "object_revision": 1,
                        }
                    )
                    virtual_relations[virtual_id] = {
                        "event_key": candidate.event_key,
                        "fact_key": candidate.fact_key,
                    }
                if decision.outcome not in {"no_change", "equivalent", "reject"}:
                    await self.bodies.persist(ctx, items[0].ref.scope, candidate.text)
            with self.uow.transaction() as tx:
                self.tasks.guard(tx, task)
                if (tx.read("remember_space_seq", space_key) or 0) != expected_space_seq:
                    tx.write("remember_comparison_retries", task.task_id, {"attempts": attempt + 1})
                    continue
                if any(
                    x.decision != "allowed" for x in self.final_guard(tx, ctx, refs, "recall").items
                ):
                    return RunResult(
                        outcome="obsolete",
                        effect_status=EffectStatus.NO_EFFECT,
                        reason="sources changed during extraction",
                    )
                if any(
                    target
                    and target.ref.memory_id not in virtual
                    and (
                        self.current(tx, target.ref.memory_id).object_revision
                        != target.object_revision
                        or self.final_guard(tx, ctx, (target.ref,), "recall").items[0].decision
                        != "allowed"
                    )
                    for _, _, target in proposals
                ):
                    tx.write("remember_comparison_retries", task.task_id, {"attempts": attempt + 1})
                    continue
                outputs, decisions = [], []
                for index, (candidate, decision, target) in enumerate(proposals):
                    # Recheck within the write transaction: earlier candidates in
                    # this batch may already have created the canonical memory.
                    if decision.outcome == "create":
                        duplicate = self.duplicate_in(tx, ctx, candidate, items[0].ref.scope)
                        if duplicate is not None:
                            target = duplicate
                            decision = ComparisonDecision(
                                outcome="no_change",
                                target_id=target.ref.memory_id,
                                reason="commit_duplicate_v1",
                            )
                    decisions.append(
                        {
                            "candidate": candidate.model_dump(mode="json"),
                            "decision": decision.model_dump(mode="json"),
                            "expected_object_revision": target.object_revision if target else None,
                        }
                    )
                    if decision.outcome == "reject":
                        continue
                    if decision.outcome in {"no_change", "equivalent"}:
                        assert target is not None
                        # Preserve every new provenance edge even when content is unchanged.
                        current = self.current(tx, target.ref.memory_id)
                        sources = tuple(
                            {
                                s.model_dump_json(): s
                                for s in (*current.sources, *candidate.sources)
                            }.values()
                        )
                        explicit_protection = (
                            candidate.importance_category == "explicit_constraint"
                            and any(
                                (tx.read("remember_source_policy", source.source_id) or {}).get(
                                    "importance_category"
                                )
                                == "explicit_constraint"
                                for source in candidate.sources
                            )
                        )
                        updates: dict[str, Any] = {"sources": sources}
                        if explicit_protection and current.importance < 1.0:
                            updates.update(
                                importance=1.0,
                                importance_reason="explicit_user_constraint",
                                importance_policy_version=self.policy.version,
                            )
                        if sources == current.sources and len(updates) == 1:
                            fact = current
                        else:
                            fact = self.change(tx, current, **updates)
                            self.emit(tx, ctx, fact, "processing")
                    elif decision.outcome == "amend":
                        assert target is not None
                        old = self.change(
                            tx,
                            self.current(tx, target.ref.memory_id),
                            status=MemoryStatus.SUPERSEDED,
                            projection_state=ProjectionState.STALE,
                        )
                        self.emit(tx, ctx, old, "projection_stale")
                        fact = MemorySnapshot.model_validate(
                            {
                                **old.model_dump(),
                                "ref": old.ref.model_copy(update={"version": old.ref.version + 1}),
                                "revision": 1,
                                "object_revision": old.object_revision + 1,
                                "content": candidate.text,
                                "content_hash": text_hash(candidate.text),
                                "sources": candidate.sources,
                                "status": MemoryStatus.ACTIVE,
                                "projection_state": ProjectionState.PENDING,
                                "model_space": None,
                                "supersedes": old.ref,
                            }
                        )
                        self.put(tx, fact)
                        self.enqueue(tx, ctx, fact, "remember.project")
                        self.emit(tx, ctx, fact, "corrected")
                    else:
                        fact = self.new_memory(
                            tx,
                            fingerprint([task.task_id, index]),
                            items[0].ref.scope,
                            candidate.text,
                            candidate.sources,
                            MemoryKind(candidate.kind),
                        )
                        category = candidate.importance_category
                        if category == "explicit_constraint":
                            trusted = any(
                                (tx.read("remember_source_policy", s.source_id) or {}).get(
                                    "importance_category"
                                )
                                == "explicit_constraint"
                                for s in candidate.sources
                            )
                            if not trusted:
                                import re

                                category = (
                                    "decision"
                                    if re.search(
                                        r"\b(must|never|required)\b|必须|不得|禁止",
                                        candidate.text,
                                        re.IGNORECASE,
                                    )
                                    else "fact"
                                )
                        value, reason = importance(category)
                        fact = fact.model_copy(
                            update={
                                "importance": value,
                                "importance_reason": reason,
                                "importance_policy_version": self.policy.version,
                            }
                        )
                        self.put(tx, fact)
                        self.enqueue(tx, ctx, fact, "remember.project")
                        self.emit(tx, ctx, fact, "saved")
                    relation = tx.read("remember_relations", fact.ref.memory_id) or {}
                    tx.write(
                        "remember_relations",
                        fact.ref.memory_id,
                        {
                            "event_key": relation.get("event_key") or candidate.event_key,
                            "fact_key": relation.get("fact_key") or candidate.fact_key,
                            "evidence": list(
                                {
                                    fingerprint(e): e
                                    for e in [
                                        *relation.get("evidence", []),
                                        *[e.model_dump(mode="json") for e in candidate.evidence],
                                    ]
                                }.values()
                            ),
                            "working_refs": list(
                                {
                                    fingerprint(r): r
                                    for r in [
                                        *relation.get("working_refs", []),
                                        *[
                                            i.ref.model_dump(mode="json")
                                            for i in items
                                            if {s.source_id for s in i.sources}
                                            & {s.source_id for s in candidate.sources}
                                        ],
                                    ]
                                }.values()
                            ),
                            "derived_from": [r.model_dump(mode="json") for r in refs]
                            if task.kind == "remember.distill"
                            else relation.get("derived_from", []),
                            "verification_level": "derived_tentative"
                            if task.kind == "remember.distill"
                            else "grounded",
                        },
                    )
                    if decision.outcome == "equivalent":
                        tx.write(
                            "remember_equivalent_forms",
                            fingerprint([fact.ref.memory_id, candidate.model_dump(mode="json")]),
                            {
                                "memory_id": fact.ref.memory_id,
                                "canonical_version": fact.ref.version,
                                "text": candidate.text,
                                "sources": [r.model_dump(mode="json") for r in candidate.sources],
                                "task_id": task.task_id,
                                "reason": decision.reason,
                            },
                        )
                    if decision.outcome == "conflict":
                        assert target is not None
                        changed_target = self.change(tx, self.current(tx, target.ref.memory_id))
                        self.emit(tx, ctx, changed_target, "processing")
                        group = ConflictGroup(
                            group_id=fingerprint(
                                [
                                    target.ref.model_dump(mode="json"),
                                    fact.ref.model_dump(mode="json"),
                                ]
                            ),
                            members=(target.ref, fact.ref),
                            explanation=decision.reason,
                        )
                        tx.write(
                            "remember_conflicts", group.group_id, group.model_dump(mode="json")
                        )
                    outputs.append(fact.ref.model_dump(mode="json"))
                tx.write(
                    "remember_decisions",
                    task.task_id,
                    {"decisions": decisions, "policy_version": self.policy.version},
                )
                deferred = tx.read("remember_deferred_extraction", task.task_id) or []
                for item in items:
                    row = tx.read("remember_pending", item.ref.memory_id)
                    if row and row.get("task_id") == task.task_id:
                        tx.write(
                            "remember_pending",
                            item.ref.memory_id,
                            {
                                **row,
                                "state": "awaiting_extractor"
                                if item.ref.memory_id in deferred
                                else "processed",
                            },
                        )
                return self.finish(
                    tx,
                    ctx,
                    task,
                    {
                        "memories": list({fingerprint(r): r for r in outputs}.values()),
                        "candidate_count": len(candidates),
                        "zero_output": not outputs,
                        "awaiting_extraction_provider": deferred,
                    },
                )
        return RunResult(
            outcome="failed",
            effect_status=EffectStatus.NO_EFFECT,
            reason="comparison commit conflict retry budget exhausted",
        )

    def load(self, ctx: TrustedContext, refs: tuple[MemoryRef, ...]) -> MemoryReadBatch:
        batch = super().load(ctx, refs)
        with self.uow.transaction() as tx:
            groups = self.conflict_groups_in(tx, ctx, refs)
        return batch.model_copy(update={"conflicts": groups})

    def conflict_groups(
        self, ctx: TrustedContext, refs: tuple[MemoryRef, ...]
    ) -> tuple[ConflictGroup, ...]:
        with self.uow.transaction() as tx:
            return self.conflict_groups_in(tx, ctx, refs)

    def conflict_groups_in(
        self, tx: SQLiteTransaction, ctx: TrustedContext, refs: tuple[MemoryRef, ...]
    ) -> tuple[ConflictGroup, ...]:
        groups = [ConflictGroup.model_validate(raw) for _, raw in tx.rows("remember_conflicts")]
        components: list[dict[str, MemoryRef]] = []
        for group in groups:
            if any(
                x.decision != "allowed"
                for x in self.final_guard(tx, ctx, group.members, "recall").items
            ):
                continue
            members = {r.model_dump_json(): r for r in group.members}
            kept = []
            for old in components:
                if members.keys() & old.keys():
                    members.update(old)
                else:
                    kept.append(old)
            components = [*kept, members]
        requested = {r.model_dump_json() for r in refs}
        return tuple(
            ConflictGroup(
                group_id=fingerprint(sorted(members)),
                members=tuple(members.values()),
                explanation="Unresolved conflicting evidence; do not choose truth by recency.",
            )
            for members in components
            if requested & members.keys()
        )

    def project_chunks(self, item: MemorySnapshot) -> list[tuple[int, int, str]]:
        counter = getattr(self, "embedding_count", self.tokenizer.count)
        return chunks(item.content, counter, self.policy.projection_chunk_tokens)

    async def project(
        self, ctx: TrustedContext, task: TaskRecord, item: MemorySnapshot
    ) -> RunResult:
        pieces = self.project_chunks(item)
        generation = task.task_id
        targets = tuple(
            projection_target(
                item.ref,
                text_hash(text),
                self.model_space,
                chunk_index=i,
                generation=generation,
                body_hash=item.content_hash,
            )
            for i, (_, _, text) in enumerate(pieces)
        )
        with self.uow.transaction() as tx:
            self.tasks.guard(tx, task)
            for target in targets:
                tx.write(
                    "remember_projection_targets",
                    target.vector_id,
                    {
                        "target": target.model_dump(mode="json"),
                        "task_id": task.task_id,
                        "cleanup": "pending",
                    },
                )
        descriptors, dimensions = [], None
        for target, (start, end, text) in zip(targets, pieces, strict=True):
            with self.uow.transaction() as tx:
                self.tasks.guard(tx, task)
                valid = (
                    self.final_guard(tx, ctx, (item.ref,), "recall").items[0].decision == "allowed"
                )
            if not valid:
                return await self.discard_projection(ctx, task, targets)
            observed = await self.projections.inspect(ctx, target, task.task_id)
            with self.uow.transaction() as tx:
                checkpoint = tx.read("remember_chunk_vectors", target.vector_id)
                if checkpoint is not None:
                    self.tasks.progress.part(
                        tx,
                        ctx,
                        task,
                        "embedding",
                        "remember_chunk_vectors",
                        target.vector_id,
                        config_version=self.policy.version,
                    )
            if checkpoint is None:
                embedded = await self.embedding.embed(
                    ctx,
                    EmbeddingRequest(
                        operation_id=target.vector_id,
                        usage="passage",
                        texts=(text,),
                        model_space=self.model_space,
                        deadline_at=ctx.deadline_at,
                    ),
                )
                if (
                    embedded.model_space != self.model_space
                    or embedded.operation_id != target.vector_id
                    or embedded.usage != "passage"
                    or len(embedded.items) != 1
                    or embedded.items[0].index != 0
                    or embedded.items[0].input_hash != target.input_hash
                ):
                    raise FoundationError(
                        ErrorCode.CONTRACT_VIOLATION, "chunk embedding binding mismatch"
                    )
                checkpoint = {"vector": list(embedded.items[0].vector)}
                with self.uow.transaction() as tx:
                    self.tasks.guard(tx, task)
                    tx.write("remember_chunk_vectors", target.vector_id, checkpoint)
                    self.tasks.progress.part(
                        tx,
                        ctx,
                        task,
                        "embedding",
                        "remember_chunk_vectors",
                        target.vector_id,
                        config_version=self.policy.version,
                    )
            if dimensions is not None and dimensions != len(checkpoint["vector"]):
                raise FoundationError(ErrorCode.CONTRACT_VIOLATION, "mixed embedding dimensions")
            dimensions = len(checkpoint["vector"])
            if observed.state != "verified":
                observed = await self.projections.project(
                    ctx,
                    ProjectionRequest(
                        operation_id=task.task_id,
                        target=target,
                        vector=tuple(checkpoint["vector"]),
                        deadline_at=ctx.deadline_at,
                    ),
                )
            if (
                observed.target != target
                or observed.operation_id != task.task_id
                or observed.state != "verified"
            ):
                return RunResult(
                    outcome="uncertain",
                    effect_status=EffectStatus.UNKNOWN,
                    operation_id=task.task_id,
                    reason="chunk projection not verified",
                )
            descriptors.append(
                ChunkDescriptor(
                    chunk_index=target.chunk_index,
                    vector_id=target.vector_id,
                    start_char=start,
                    end_char=end,
                    input_hash=target.input_hash,
                    verified=True,
                )
            )
        with self.uow.transaction() as tx:
            self.tasks.guard(tx, task)
            valid = self.final_guard(tx, ctx, (item.ref,), "recall").items[0].decision == "allowed"
            if valid:
                manifest = ProjectionManifest(
                    memory=item.ref,
                    generation=generation,
                    body_hash=item.content_hash,
                    model_space=self.model_space,
                    dimensions=dimensions,
                    chunker_version="unicode_token_budget_v1",
                    embedding_tokenizer=getattr(
                        self, "embedding_tokenizer_id", self.tokenizer.identifier
                    ),
                    expected_chunk_count=len(targets),
                    chunks=tuple(descriptors),
                    state="ready",
                    task_id=task.task_id,
                    published_at=self.identity.clock(),
                    vector_location=ResourceLocation(
                        kind="vector",
                        provider_id="projection",
                        provider_instance_id=self.model_space,
                        namespace="remember_vectors",
                        object_key=self.refkey(item.ref),
                        generation=generation,
                        content_hash=item.content_hash,
                    ),
                )
                tx.write(
                    "remember_manifests", self.refkey(item.ref), manifest.model_dump(mode="json")
                )
                updated = self.change(
                    tx,
                    self.current(tx, item.ref.memory_id),
                    projection_state=ProjectionState.READY,
                    model_space=self.model_space,
                )
                self.emit(tx, ctx, updated, "projection_ready")
                return self.finish(
                    tx,
                    ctx,
                    task,
                    {
                        "memory": item.ref.model_dump(mode="json"),
                        "projection": "ready",
                        "chunks": len(targets),
                    },
                )
        return await self.discard_projection(ctx, task, targets)

    async def discard_projection(
        self, ctx: TrustedContext, task: TaskRecord, targets: tuple[ProjectionTarget, ...]
    ) -> RunResult:
        for target in targets:
            result = await self.projections.delete(ctx, target, task.task_id)
            if result.state != "absent" or result.target != target:
                return RunResult(
                    outcome="uncertain",
                    effect_status=EffectStatus.UNKNOWN,
                    operation_id=task.task_id,
                    reason="late projection cleanup not confirmed",
                )
        return RunResult(
            outcome="obsolete",
            effect_status=EffectStatus.CONFIRMED,
            reason="invalidated generation cleaned",
        )

    async def cleanup(
        self, ctx: TrustedContext, task: TaskRecord, item: MemorySnapshot
    ) -> RunResult:
        with self.uow.transaction() as tx:
            in_flight = [
                row["record"]
                for _, row in tx.rows("tasks")
                if row["record"]["kind"] == "remember.project"
                and row["record"]["subject"]["object_id"] == item.ref.memory_id
                and (
                    row["record"]["state"] == "running"
                    or row["record"]["effect_status"] == "unknown"
                )
            ]
            if in_flight:
                return RunResult(
                    outcome="uncertain",
                    effect_status=EffectStatus.UNKNOWN,
                    operation_id=task.task_id,
                    reason="in-flight projection must reconcile before cleanup confirmation",
                )
            targets = [
                ProjectionTarget.model_validate(row["target"])
                for _, row in tx.rows("remember_projection_targets")
                if row["target"]["memory"]["memory_id"] == item.ref.memory_id
            ]
            legacy = []
            for version in range(1, item.ref.version + 1):
                ref = item.ref.model_copy(update={"version": version})
                raw = tx.get(memory_ref(ref, versioned=True))
                if raw:
                    old = self.decode(tx, raw)
                    legacy.append(old)
                    if "body_location" not in raw:
                        targets.append(projection_target(ref, old.content_hash, self.model_space))
        for target in targets:
            result = await self.projections.delete(ctx, target, task.task_id)
            if result.state != "absent" or result.target != target:
                return RunResult(
                    outcome="uncertain",
                    effect_status=EffectStatus.UNKNOWN,
                    operation_id=task.task_id,
                    reason="vector cleanup unconfirmed",
                )
            with self.uow.transaction() as tx:
                self.tasks.guard(tx, task)
                row = tx.read("remember_projection_targets", target.vector_id)
                if row:
                    tx.write(
                        "remember_projection_targets",
                        target.vector_id,
                        {**row, "cleanup": "confirmed", "evidence": result.model_dump(mode="json")},
                    )
        if self.bodies.cache:
            for old in legacy:
                await self.bodies.cache.delete(old.ref.scope, old.content_hash)
                if await self.bodies.cache.get(old.ref.scope, old.content_hash) is not None:
                    return RunResult(
                        outcome="uncertain",
                        effect_status=EffectStatus.UNKNOWN,
                        operation_id=task.task_id,
                        reason="body cache cleanup unconfirmed",
                    )
        with self.uow.transaction() as tx:
            self.tasks.guard(tx, task)
            return self.finish(
                tx,
                ctx,
                task,
                {
                    "vector_cleanup": "completed",
                    "body_cache_cleanup": "completed",
                    "source_retention": "retained",
                    "physical_erasure": False,
                },
            )

    async def recover(self, ctx: TrustedContext, task: TaskRecord) -> RecoveryDecision:
        # Every external write is registered before execution and uses a stable generation.
        # Resume inspects each target before writing, including partially finished manifests.
        with self.uow.transaction() as tx:
            self.tasks.guard(tx, task)
            raw = required_record(tx, task.input_ref)
            ref = MemoryRef.model_validate(raw["ref"])
        purpose = (
            "cleanup" if task.kind in {"remember.cleanup", "remember.revalidate"} else "recall"
        )
        await self.hydrate(ctx, (ref,), purpose)
        with self.uow.transaction() as tx:
            valid = self.final_guard(tx, ctx, (ref,), purpose).items[0].decision == "allowed"
            targets = tuple(
                ProjectionTarget.model_validate(row["target"])
                for _, row in tx.rows("remember_projection_targets")
                if row["task_id"] == task.task_id
            )
        if not valid:
            if targets:
                result = await self.discard_projection(ctx, task, targets)
                if result.outcome == "uncertain":
                    return RecoveryDecision(
                        action=RecoveryAction.QUERY_ONLY,
                        effect_status=EffectStatus.UNKNOWN,
                        original_operation_id=task.task_id,
                        reason=result.reason,
                        evidence=(task.input_ref,),
                    )
            return RecoveryDecision(
                action=RecoveryAction.CANCEL,
                effect_status=EffectStatus.CONFIRMED if targets else EffectStatus.NO_EFFECT,
                reason="source invalidated; registered projections reconciled",
                evidence=(task.input_ref,),
            )
        return RecoveryDecision(
            action=RecoveryAction.RESUME,
            effect_status=EffectStatus.NO_EFFECT,
            reason="resume original task with per-chunk inspect and idempotent writes",
            evidence=(task.input_ref,),
        )
