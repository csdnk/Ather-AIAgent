"""Durable Remember pipeline composed with RF task leases and transactional outbox.

MemorySnapshot is a hydrated API DTO. New authority rows contain MemoryRecord
references. Local body replicas are durable runtime data, separate from caches.
"""

from __future__ import annotations

import asyncio
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
from aether_agent_memory.runtime.contracts.http_evidence import HttpRequestEvidence
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
from aether_agent_memory.runtime.foundation.content_diagnostics import authorize_content_read
from aether_agent_memory.runtime.foundation.requests import select_scope, text_hash
from aether_agent_memory.runtime.foundation.telemetry import observed
from aether_agent_memory.runtime.foundation.timings import measure_stage
from aether_agent_memory.runtime.foundation.transactions import native
from aether_agent_memory.runtime.storage.ports import MetadataTransaction

from .comparison import (
    ComparisonDecision,
    ComparisonPort,
    ConservativeComparison,
    EquivalencePort,
    EquivalenceVerdict,
    OccurrenceContext,
    OccurrenceVerdict,
    discovery_text,
    occurrence_context,
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
from .extraction import EvidenceValidationError, LiteralExtraction
from .hydration import BodyReadRequiredError, hydrate_metadata_reads
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
_task_kind: ContextVar[str | None] = ContextVar("remember_task_kind", default=None)


def safe_task_reason(value: Any) -> str | None:
    if (
        isinstance(value, str)
        and 0 < len(value) <= 80
        and all(char in "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_" for char in value)
    ):
        return value
    return None


def direct_occurrence_evidence(text: str, evidence: list[dict[str, Any]]) -> set[str]:
    """Only a directly quoted claim may authorize the exact-evidence shortcut.

    Sharing a broad paragraph is not occurrence identity: it may describe several
    runs. Such candidates must retain separate proposals and pass contextual review.
    """
    return {
        fingerprint(item)
        for item in evidence
        if canonical_text(item.get("quote", "")) == canonical_text(text)
    }


def checkpoint_provider_identity(provider: Any, seen: frozenset[int] = frozenset()) -> Any:
    """Bind cached model output to public provider identity, never credentials/state.

    Configured model providers expose checkpoint_identity(). Wrappers such as
    LangMemBatchExtraction and StructuredModel retain that identity through their
    manager/model/owner links. Do not serialize a provider's __dict__ or config:
    those may contain clients, headers or credentials.
    """
    if provider is None:
        return None
    kind = f"{type(provider).__module__}.{type(provider).__qualname__}"
    if id(provider) in seen:
        return {"provider": kind, "cycle": True}
    seen = seen | {id(provider)}
    declared = getattr(provider, "checkpoint_identity", None)
    if callable(declared):
        return {"provider": kind, "identity": declared()}
    identity: dict[str, Any] = {"provider": kind}
    for name in ("model_id", "prompt_version"):
        value = getattr(provider, name, None)
        if isinstance(value, str):
            identity[name] = value
    for name in ("provider", "manager", "model", "owner"):
        value = getattr(provider, name, None)
        if value is not None and not isinstance(value, (str, int, float, bool, bytes)):
            identity[name] = checkpoint_provider_identity(value, seen)
    return identity


@hydrate_metadata_reads
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
        self, tx: MetadataTransaction, ctx: TrustedContext, memory: MemorySnapshot, kind: str
    ) -> str:
        task_id = super().enqueue(tx, ctx, memory, kind)
        if tx.read("remember_task_policy", task_id) is None:
            tx.write("remember_task_policy", task_id, self.policy.model_dump(mode="json"))
            tx.write("remember_task_binding", task_id, self.checkpoint_binding(kind))
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

    def decode(self, tx: MetadataTransaction, raw: dict[str, Any]) -> MemorySnapshot:
        if "body_location" not in raw:
            return super().decode(tx, raw)
        record = MemoryRecord.model_validate(raw)
        if self.bodies.remote_only:
            text = self.bodies.verified_text(record.body_location)
            if text is None:
                raise BodyReadRequiredError(record.ref, record.body_location)
        else:
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

    def put(self, tx: MetadataTransaction, snapshot: MemorySnapshot) -> None:
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
        tx: MetadataTransaction,
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
        tx: MetadataTransaction,
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

    def ensure_duplicate_index(self, tx: MetadataTransaction) -> None:
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

    def duplicate(
        self, ctx: TrustedContext, candidate: CandidateFact, scope: Scope
    ) -> MemorySnapshot | None:
        with self.uow.transaction() as tx:
            return self.duplicate_in(tx, ctx, candidate, scope)

    def duplicate_in(
        self,
        tx: MetadataTransaction,
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
            same_evidence = direct_occurrence_evidence(
                item.content, relation.get("evidence", [])
            ) & direct_occurrence_evidence(
                candidate.text, [e.model_dump(mode="json") for e in candidate.evidence]
            )
            if same_evidence:
                return item
        return None

    async def save(self, ctx: TrustedContext, request: RememberRequest) -> RememberReceipt:
        prepared = await self.prepare_save(ctx, request)
        await self.persist_save(ctx, prepared)
        result = await asyncio.to_thread(self.commit_save, ctx, request, prepared)
        return await self.admit_save_cache(ctx, prepared, result)

    async def prepare_save(self, ctx: TrustedContext, request: RememberRequest) -> dict[str, Any]:
        scope = select_scope(ctx, request.selection)

        def inspect_request() -> tuple[str, MemoryRef, Any]:
            with self.uow.transaction() as tx:
                key, previous = self.replay(tx, ctx, "remember.save", request)
                ref = MemoryRef(scope=scope, memory_id=key, version=1)
                self.identity.authorize(tx, ctx, Permission.WRITE, memory_ref(ref))
            return key, ref, previous

        key, ref, previous = await asyncio.to_thread(inspect_request)
        if previous:
            return {"previous": previous["result"]}
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

        def inspect_source() -> dict[str, Any] | None:
            with self.uow.transaction() as tx:
                self.identity.authorize(tx, ctx, Permission.WRITE, memory_ref(ref))
                duplicate = self.source_replay(tx, ctx, request, scope, text, key)
                if duplicate is not None:
                    return {"previous": duplicate.model_dump(mode="json")}
            return None

        duplicate_result = await asyncio.to_thread(inspect_source)
        if duplicate_result is not None:
            return duplicate_result
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
        return {
            "scope": scope.model_dump(mode="json"),
            "key": key,
            "text": text,
            "working_text": working_text,
            "document": document,
            "summarized": summarized,
        }

    async def persist_save(self, ctx: TrustedContext, prepared: dict[str, Any]) -> None:
        if "previous" in prepared:
            return
        scope = Scope.model_validate(prepared["scope"])
        await self.bodies.persist(ctx, scope, prepared["text"])
        if prepared["summarized"]:
            await self.bodies.persist(ctx, scope, prepared["working_text"])

    def commit_save(
        self, ctx: TrustedContext, request: RememberRequest, prepared: dict[str, Any]
    ) -> RememberReceipt:
        if "previous" in prepared:
            return RememberReceipt.model_validate(prepared["previous"])
        scope, key = Scope.model_validate(prepared["scope"]), prepared["key"]
        ref = MemoryRef(scope=scope, memory_id=key, version=1)
        text, working_text = prepared["text"], prepared["working_text"]
        document, summarized = prepared["document"], prepared["summarized"]
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
                task_ids.append(self.enqueue(tx, ctx, item, "remember.project"))
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
        return result

    async def admit_save_cache(
        self, ctx: TrustedContext, prepared: dict[str, Any], result: RememberReceipt
    ) -> RememberReceipt:
        if "previous" in prepared:
            return result
        scope, key = Scope.model_validate(prepared["scope"]), prepared["key"]
        working_text = prepared["working_text"]
        memory = result.memories[0]

        def allowed_before_cache() -> bool:
            with self.uow.transaction() as tx:
                allowed = (
                    self.final_guard(tx, ctx, (memory,), "recall").items[0].decision == "allowed"
                )
            return allowed

        allowed = await asyncio.to_thread(allowed_before_cache)
        cache = await self.bodies.admit(scope, working_text) if allowed else "ineligible"

        def allowed_after_cache() -> bool:
            with self.uow.transaction() as tx:
                still_allowed = (
                    self.final_guard(tx, ctx, (memory,), "recall").items[0].decision == "allowed"
                )
            return still_allowed

        still_allowed = await asyncio.to_thread(allowed_after_cache)
        if not still_allowed and self.bodies.cache:
            # Reads always guard metadata, including while this compensating delete retries.
            try:
                await self.bodies.cache.delete(scope, text_hash(working_text))
                cache = "invalidated"
            except Exception:
                cache = "cleanup_pending"

                def schedule_cleanup() -> None:
                    with self.uow.transaction() as tx:
                        self.enqueue(tx, ctx, self.current(tx, key), "remember.cleanup")

                await asyncio.to_thread(schedule_cleanup)

        def record_cache() -> None:
            with self.uow.transaction() as tx:
                tx.write(
                    "remember_cache_admission",
                    key,
                    {"state": cache, "bytes": len(working_text.encode("utf-8"))},
                )

        await asyncio.to_thread(record_cache)
        return result

    def schedule(
        self, tx: MetadataTransaction, ctx: TrustedContext, scope: Scope, *, force: bool = False
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

    def consolidate(
        self,
        ctx: TrustedContext,
        selection: ScopeSelector,
        *,
        http_request: HttpRequestEvidence | None = None,
    ) -> tuple[str, ...]:
        scope = select_scope(ctx, selection)
        with self.uow.transaction() as tx:
            self.identity.revalidate(tx, ctx)
            mutation = self.mutations.begin(
                tx,
                ctx,
                "remember.consolidate",
                (
                    RecordRef(
                        owner="remember",
                        object_type="scope",
                        object_id=fingerprint(scope.model_dump(mode="json")),
                        scope=scope,
                    ),
                ),
                selection.model_dump(mode="json"),
                http_request=http_request,
            )
            if mutation.previous is not None:
                original_tasks = mutation.previous["task_ids"]
                assert isinstance(original_tasks, list)
                return tuple(str(task) for task in original_tasks)
            tasks = self.schedule(tx, ctx, scope, force=True)
            mutation.finish({"task_ids": list(tasks)}, task_ids=tasks)
            return tasks

    def reprocess(
        self,
        ctx: TrustedContext,
        memory_id: str,
        *,
        http_request: HttpRequestEvidence | None = None,
    ) -> str:
        with self.uow.transaction() as tx:
            item = self.current(tx, memory_id)
            self.identity.authorize(tx, ctx, Permission.WRITE, memory_ref(item.ref))
            mutation = self.mutations.begin(
                tx,
                ctx,
                "remember.reprocess",
                (memory_ref(item.ref),),
                None,
                http_request=http_request,
            )
            if mutation.previous is not None:
                return str(mutation.previous["task_id"])
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
            task_id = self.enqueue(tx, ctx, item, kind)
            mutation.finish({"task_id": task_id}, task_ids=(task_id,))
            return task_id

    def distill(
        self,
        ctx: TrustedContext,
        refs: tuple[MemoryRef, ...],
        *,
        http_request: HttpRequestEvidence | None = None,
    ) -> str:
        if not refs or len(refs) > self.policy.consolidation_messages:
            raise FoundationError(ErrorCode.INVALID_ARGUMENT, "invalid review input count")
        with self.uow.transaction() as tx:
            mutation = self.mutations.begin(
                tx,
                ctx,
                "remember.distill",
                tuple(memory_ref(ref) for ref in refs),
                [ref.model_dump(mode="json") for ref in refs],
                http_request=http_request,
            )
            if mutation.previous is not None:
                return str(mutation.previous["task_id"])
            task_id = self.distill_in(tx, ctx, refs)
            mutation.finish({"task_id": task_id}, task_ids=(task_id,))
            return task_id

    def distill_in(
        self, tx: MetadataTransaction, ctx: TrustedContext, refs: tuple[MemoryRef, ...]
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

    @staticmethod
    def processing_task_closure(
        tx: MetadataTransaction, memory_id: str, all_rows: list[dict[str, Any]]
    ) -> tuple[set[str], set[str]]:
        # Read batch relations once inside the caller's snapshot. A point lookup
        # per historical task (and per closure pass) stalls the shared database
        # transaction during status polling, even when only one memory is relevant.
        batches = dict(tx.rows("remember_batches"))
        by_memory: dict[str, list[dict[str, Any]]] = {}
        for row in all_rows:
            parents = {row["subject"]["object_id"]}
            parents.update(r["memory_id"] for r in batches.get(row["task_id"], {}).get("refs", []))
            for parent in parents:
                by_memory.setdefault(parent, []).append(row)
        memory_ids, selected_tasks = {memory_id}, set()
        pending = [memory_id]
        while pending:
            for row in by_memory.get(pending.pop(), []):
                if row["task_id"] in selected_tasks:
                    continue
                selected_tasks.add(row["task_id"])
                if row.get("result_ref"):
                    result = required_record(tx, RecordRef.model_validate(row["result_ref"]))
                    for ref in result.get("memories", []):
                        if ref["memory_id"] not in memory_ids:
                            memory_ids.add(ref["memory_id"])
                            pending.append(ref["memory_id"])
        return memory_ids, selected_tasks

    def processing_task_rows(
        self, tx: MetadataTransaction, memory_id: str
    ) -> tuple[set[str], dict[str, Any]]:
        lookup = getattr(tx, "processing_tasks_for_memories", None)
        if not callable(lookup):
            envelopes = dict(tx.rows("tasks"))
            memory_ids, selected = self.processing_task_closure(
                tx, memory_id, [value["record"] for value in envelopes.values()]
            )
            return memory_ids, {key: value for key, value in envelopes.items() if key in selected}

        # Keep the same transaction snapshot and transitive batch/result closure,
        # while fetching only related envelopes from providers with a targeted read.
        memory_ids, pending = {memory_id}, [memory_id]
        selected_envelopes: dict[str, Any] = {}
        while pending:
            frontier, pending = pending[:1000], pending[1000:]
            for key, envelope in lookup(tuple(frontier)):
                if key in selected_envelopes:
                    continue
                selected_envelopes[key] = envelope
                row = envelope["record"]
                if row.get("result_ref"):
                    result = required_record(tx, RecordRef.model_validate(row["result_ref"]))
                    for ref in result.get("memories", []):
                        if ref["memory_id"] not in memory_ids:
                            memory_ids.add(ref["memory_id"])
                            pending.append(ref["memory_id"])
        return memory_ids, dict(sorted(selected_envelopes.items()))

    def processing(self, ctx: TrustedContext, memory_id: str) -> dict[str, Any]:
        with self.uow.transaction() as tx:
            item = self.current(tx, memory_id)
            authorize_content_read(self.identity, tx, ctx, memory_ref(item.ref))
            memory_ids, task_envelopes = self.processing_task_rows(tx, memory_id)
            rows = [row["record"] for row in task_envelopes.values()]
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
            recovery = None
            if (
                item.status == MemoryStatus.ACTIVE
                and self.identity.permits(tx, ctx, Permission.WRITE, memory_ref(item.ref))
                and self.final_guard(tx, ctx, (item.ref,), "recall").items[0].decision == "allowed"
            ):
                # Keep failures distinct from successful publication. These are existing
                # authorized commands; no placeholder is indexed and no retry loop is hidden.
                terminal = {"succeeded", "failed", "cancelled", "attention_required"}
                latest = {
                    r["kind"]: r for r in current_rows if r["subject"]["object_id"] == memory_id
                }
                summary_task = latest.get("remember.summarize")
                projection_task = latest.get("remember.project")
                extraction_task = latest.get("remember.extract")
                summary_busy = summary_task and (
                    summary_task["state"] not in terminal
                    or summary_task["effect_status"] == "unknown"
                )
                projection_busy = projection_task and (
                    projection_task["state"] not in terminal
                    or projection_task["effect_status"] == "unknown"
                )
                action = reason = None
                if (
                    summary
                    and summary["memory"] == item.ref.model_dump(mode="json")
                    and summary["state"] != "ready"
                    and not summary_busy
                    and (
                        summary["state"] == "failed"
                        or summary_task
                        and summary_task["state"] in {"failed", "attention_required"}
                    )
                ):
                    action, reason = "reprocess", "working_summary_failed"
                elif (
                    self.projection_buildable(tx, item)
                    and not projection_busy
                    and (
                        item.projection_state == ProjectionState.FAILED
                        or projection_task
                        and projection_task["state"] in {"failed", "attention_required"}
                    )
                ):
                    action, reason = "reindex", "projection_failed"
                elif (
                    item.kind == MemoryKind.WORKING
                    and extraction_task
                    and extraction_task["state"] in {"failed", "attention_required"}
                    and extraction_task["effect_status"] != "unknown"
                    and not summary_busy
                ):
                    action, reason = "reprocess", "working_extraction_failed"
                if action:
                    recovery = {
                        "action": action,
                        "method": "POST",
                        "path": f"/p3/remember/{memory_id}/{action}",
                        "reason": reason,
                        "requires_new_operation_id": True,
                    }
            current_task_ids = {r["task_id"] for r in current_rows}
            rejections = [
                row
                for _, row in tx.rows("remember_support_verifications")
                if row["task_id"] in current_task_ids and row["status"] == "rejected"
            ]
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
                "rejected_candidate_count": len(rejections),
                "candidate_rejections": rejections,
                "tasks": [
                    {
                        **{
                            k: r.get(k)
                            for k in ("task_id", "kind", "state", "error_code", "result_ref")
                        },
                        "terminal_reason": safe_task_reason(
                            task_envelopes[r["task_id"]].get("terminal_reason")
                        ),
                    }
                    for r in rows
                ],
                "artifact": tx.read("remember_artifacts", self.refkey(item.ref)),
                "working_summary": summary,
                "recovery": recovery,
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

    def periodic_pending(self, tx: MetadataTransaction, key: str) -> int:
        row = tx.read("remember_pending", key)
        if not row or row["state"] != "pending":
            return 0
        scope = MemoryRef.model_validate(row["ref"]).scope
        ctx = TrustedContext.model_validate(row["context"]).model_copy(
            update={
                "deadline_at": later(self.identity.clock(), self.processing_seconds),
                "operation_id": fingerprint(["consolidate", row["ref"]]),
            }
        )
        self.identity.revalidate(tx, ctx)
        return len(self.schedule(tx, ctx, scope))

    async def correct_async(
        self, ctx: TrustedContext, memory_id: str, request: CorrectionRequest
    ) -> RememberReceipt:
        prepared = self.prepare_correction(ctx, memory_id, request)
        scope = Scope.model_validate(prepared["scope"])
        for body in dict.fromkeys((prepared["text"], prepared["working_text"])):
            await self.bodies.persist(ctx, scope, body)
        return self.correct(ctx, memory_id, request)

    def prepare_correction(
        self, ctx: TrustedContext, memory_id: str, request: CorrectionRequest
    ) -> dict[str, Any]:
        with self.uow.transaction() as tx:
            item = self.current(tx, memory_id)
            self.identity.authorize(tx, ctx, Permission.CORRECT, memory_ref(item.ref))
        if (
            not request.content.strip()
            or len(request.content.encode("utf-8")) > self.max_input_bytes
        ):
            raise FoundationError(ErrorCode.INVALID_ARGUMENT, "correction exceeds input policy")
        working_text = request.content
        if item.kind == MemoryKind.WORKING:
            with self.uow.transaction() as tx:
                key, _ = self.replay(tx, ctx, "correct_" + memory_id, request)
                prior = tx.read("remember_working_summaries", memory_id)
            if self.summaries.needed(request.content, request.source.kind == "document"):
                source = SourceRef(
                    source_id=fingerprint([key, "correction_source"]),
                    source_version=1,
                    content_hash=text_hash(request.content),
                    locator=self.bodies.location(item.ref.scope, request.content).object_key,
                )
                body = self.summaries.descriptor(
                    source, request.content, (prior or {}).get("task_context", "")
                )
                working_text = body
        return {
            "scope": item.ref.scope.model_dump(mode="json"),
            "text": request.content,
            "working_text": working_text,
            "memory_id": memory_id,
        }

    def working_correction_body(
        self,
        tx: MetadataTransaction,
        ctx: TrustedContext,
        item: MemorySnapshot,
        source: SourceRef,
        request: CorrectionRequest,
    ) -> str:
        prior = tx.read("remember_working_summaries", item.ref.memory_id)
        summarize = self.summaries.needed(request.content, request.source.kind == "document")
        new_ref = item.ref.model_copy(update={"version": item.ref.version + 1})
        if summarize:
            task_context = (prior or {}).get("task_context", "")
            body = self.summaries.descriptor(source, request.content, task_context)
            self.summaries.register(
                tx, item.model_copy(update={"ref": new_ref, "sources": (source,)}), task_context
            )
        else:
            body = request.content
            if prior:
                # Keep the old version binding for audit; it cannot schedule a summary
                # or overwrite this short correction under an earlier worker lease.
                tx.write(
                    "remember_working_summaries", item.ref.memory_id, {**prior, "state": "obsolete"}
                )
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

    def working_task_kind(self, tx: MetadataTransaction, item: MemorySnapshot) -> str:
        row = tx.read("remember_working_summaries", item.ref.memory_id)
        if row and row["memory"] == item.ref.model_dump(mode="json") and row["state"] != "ready":
            return "remember.summarize"
        return "remember.extract"

    def projection_buildable(self, tx: MetadataTransaction, item: MemorySnapshot) -> bool:
        if item.kind != MemoryKind.WORKING:
            return True
        row = tx.read("remember_working_summaries", item.ref.memory_id)
        return (
            not row or row["memory"] != item.ref.model_dump(mode="json") or row["state"] == "ready"
        )

    async def hydrate(
        self, ctx: TrustedContext, refs: tuple[MemoryRef, ...], purpose: str = "recall"
    ) -> None:
        # Authorize before object-store reads; provider keys come only from authority rows.
        locations = []

        def authorized_locations() -> None:
            with self.uow.transaction() as tx:
                for ref in refs:
                    authorize_content_read(self.identity, tx, ctx, memory_ref(ref))
                    if self.final_guard(tx, ctx, (ref,), purpose).items[0].decision != "allowed":
                        continue
                    raw = tx.get(memory_ref(ref, versioned=True))
                    if raw and "body_location" in raw:
                        locations.append(
                            (ref.scope, ResourceLocation.model_validate(raw["body_location"]))
                        )

        await asyncio.to_thread(authorized_locations)
        for scope, location in locations:
            await self.bodies.read(scope, location)

    async def load_async(self, ctx: TrustedContext, refs: tuple[MemoryRef, ...]) -> MemoryReadBatch:
        await self.hydrate(ctx, refs)
        return await asyncio.to_thread(self.load, ctx, refs)

    async def working_async(
        self, ctx: TrustedContext, selection: ScopeSelector, page: PageRequest
    ) -> MemoryReadBatch:
        scope = select_scope(ctx, selection)

        def working_page() -> tuple[Any, Any]:
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
            return refs, cursor

        refs, cursor = await asyncio.to_thread(working_page)
        batch = await self.load_async(ctx, tuple(refs))
        return batch.model_copy(update={"next_cursor": cursor})

    async def preview(
        self, ctx: TrustedContext, ref: MemoryRef, max_chars: int = 240
    ) -> dict[str, Any]:
        """A bounded immutable body prefix, guarded before and after physical I/O.

        The catalog never hydrates whole bodies. This is an excerpt, not a model
        summary or a full-body integrity proof. Body detail retains full hashing.
        """
        if not 1 <= max_chars <= 500:
            raise FoundationError(ErrorCode.INVALID_ARGUMENT, "preview exceeds read budget")
        with self.uow.transaction() as tx:
            authorize_content_read(self.identity, tx, ctx, memory_ref(ref))
            eligible = self.final_guard(tx, ctx, (ref,), "recall").items[0]
            if eligible.decision != "allowed":
                return {"content": None, "status": eligible.reason}
            raw = required_record(tx, memory_ref(ref, versioned=True))
            if "body_location" not in raw:
                return {"content": raw["content"][:max_chars], "status": "excerpt"}
            location = ResourceLocation.model_validate(raw["body_location"])
            self.bodies.check_binding(location)
        verified = self.bodies.verified_text(location)
        cached = (
            verified[:max_chars]
            if verified is not None
            else self.bodies.preview_text(location, max_chars)
        )
        if cached is not None:
            # The eligibility check above runs even when the bytes are cached.
            return {"content": cached, "status": "excerpt"}
        text = await self.bodies.read_prefix(location, raw["body_chars"], max_chars)
        with self.uow.transaction() as tx:
            current = self.final_guard(tx, ctx, (ref,), "recall").items[0]
            if (
                current.decision != "allowed"
                or current.checked_revision != eligible.checked_revision
            ):
                return {"content": None, "status": "changed_during_read"}
        self.bodies.remember_preview(location, max_chars, text)
        return {"content": text, "status": "excerpt"}

    async def read_body(self, ctx: TrustedContext, ref: MemoryRef) -> FullBodyReadResult:
        def inspect_body() -> (
            FullBodyReadResult | tuple[MemoryRecord | MemorySnapshot, ResourceLocation]
        ):
            with self.uow.transaction() as tx:
                authorize_content_read(self.identity, tx, ctx, memory_ref(ref))
                eligibility = self.final_guard(tx, ctx, (ref,), "recall")
                if eligibility.items[0].decision != "allowed":
                    return FullBodyReadResult(
                        memory=ref,
                        outcome="excluded",
                        path="none",
                        reason_code=eligibility.items[0].reason,
                    )
                # Capture the immutable authority address without decoding or fetching
                # its body. A later current() would hide another full read here.
                raw = required_record(tx, memory_ref(ref, versioned=True))
                item: MemoryRecord | MemorySnapshot
                if "body_location" in raw:
                    item = MemoryRecord.model_validate(raw)
                    location = item.body_location
                else:
                    item = MemorySnapshot.model_validate(raw)
                    location = self.bodies.location(ref.scope, item.content)
            return item, location

        with measure_stage("memory_prepare"):
            selected = await asyncio.to_thread(inspect_body)
        if isinstance(selected, FullBodyReadResult):
            return selected
        item, location = selected
        with measure_stage("memory_fetch"):
            content, path = await self.bodies.read(ref.scope, location)

        def recheck_body() -> FullBodyReadResult | GuardStamp:
            with self.uow.transaction() as tx:
                eligible = self.final_guard(tx, ctx, (ref,), "recall").items[0]
                if (
                    eligible.decision != "allowed"
                    or eligible.checked_revision != item.object_revision
                ):
                    return FullBodyReadResult(
                        memory=ref, outcome="stale", path="none", reason_code="changed_during_read"
                    )
                guard = GuardStamp(
                    memory=ref,
                    object_revision=item.object_revision,
                    relations_revision=item.revision,
                    authorization_epoch=ctx.principal.auth_epoch,
                    body_hash=location.content_hash,
                    checked_at=self.identity.clock(),
                )
            return guard

        with measure_stage("memory_validate"):
            guard = await asyncio.to_thread(recheck_body)
        if isinstance(guard, FullBodyReadResult):
            return guard
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
        kind_token = _task_kind.set(task.kind)
        try:
            return await self.run_bound(ctx, task)
        finally:
            _task_kind.reset(kind_token)
            _task_policy.reset(token)

    async def prepare_background(
        self, ctx: TrustedContext, task: TaskRecord
    ) -> tuple[MemorySnapshot, ...] | RunResult:
        def background_inputs() -> tuple[tuple[MemoryRef, ...], Any]:
            with self.uow.transaction() as tx:
                self.tasks.guard(tx, task)
                raw = required_record(tx, task.input_ref)
                ref = MemoryRef.model_validate(raw["ref"])
                batch = tx.read("remember_batches", task.task_id)
                refs = (
                    tuple(MemoryRef.model_validate(x) for x in batch["refs"]) if batch else (ref,)
                )
            return refs, batch

        refs, batch = await asyncio.to_thread(background_inputs)
        maintenance = task.kind in {"remember.cleanup", "remember.revalidate"}
        await self.hydrate(ctx, refs, "cleanup" if maintenance else "recall")
        prepared = await asyncio.to_thread(
            self.background_metadata, ctx, task, refs, batch, maintenance
        )
        if isinstance(prepared, RunResult):
            return prepared
        items = prepared
        if task.kind in {"remember.extract", "remember.compress"}:
            return await self.source_access.originals(ctx, items)
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

                    def read_original(source: Any) -> Any:
                        with self.uow.transaction() as tx:
                            row = tx.read("remember_sources", source.source_id)
                        return row

                    row = await asyncio.to_thread(read_original, source)
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

            def record_review() -> None:
                with self.uow.transaction() as tx:
                    self.tasks.guard(tx, task)
                    tx.write(
                        "remember_review_context",
                        task.task_id,
                        {"episodes": [i.model_dump(mode="json") for i in items]},
                    )

            await asyncio.to_thread(record_review)
            return tuple(originals)
        return items

    def background_metadata(
        self,
        ctx: TrustedContext,
        task: TaskRecord,
        refs: tuple[MemoryRef, ...],
        batch: dict[str, Any] | None,
        maintenance: bool,
    ) -> tuple[MemorySnapshot, ...] | RunResult:
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
        return items

    async def run_bound(self, ctx: TrustedContext, task: TaskRecord) -> RunResult:
        items = await self.prepare_background(ctx, task)
        if isinstance(items, RunResult):
            return items
        if task.kind == "remember.summarize":
            return await self.summaries.process(ctx, task, items[0])
        if task.kind in {"remember.extract", "remember.distill"}:
            return await self.extract_batch(ctx, task, items)
        if task.kind == "remember.revalidate":
            return await self.revalidate_sources(ctx, task, items[0])
        if task.kind == "remember.compress":
            return await self.compress(ctx, task, items[0])
        if task.kind == "remember.cleanup":
            return await self.cleanup(ctx, task, items[0])
        return await self.project(ctx, task, items[0])

    def checkpoint_binding(self, kind: str | None = None) -> str:
        kind = kind or _task_kind.get()
        processing = ("extraction", "comparison", "equivalence_verifier", "support_verifier")
        providers = {
            "remember.extract": processing,
            "remember.distill": processing,
            "remember.summarize": ("summaries",),
            "remember.compress": ("compressor", "quality"),
            "remember.revalidate": ("support_verifier",),
            "remember.project": (),
            "remember.cleanup": (),
        }.get(kind or "", (*processing, "compressor", "quality", "summaries"))
        return fingerprint(
            [
                # Do not replay old key-only merge decisions under the new
                # occurrence evidence policy. Storage-only tasks stay compatible.
                "remember_occurrence_checkpoint_v6"
                if kind in {None, "remember.extract", "remember.distill"}
                else "remember_processing_checkpoint_v2",
                kind,
                self.policy.model_dump(mode="json"),
                self.model_space,
                self.tokenizer.identifier,
                getattr(self, "embedding_tokenizer_id", self.tokenizer.identifier),
                {
                    name: checkpoint_provider_identity(getattr(self, name, None))
                    for name in providers
                },
            ]
        )

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
        prepared = await self.generate_compression(ctx, task, item)
        if prepared["text"] is not None:
            await self.bodies.persist(ctx, item.ref.scope, prepared["text"])
        return self.commit_compression(ctx, task, item, prepared)

    async def generate_compression(
        self, ctx: TrustedContext, task: TaskRecord, item: MemorySnapshot
    ) -> dict[str, Any]:
        from .compression_attempts import compress_part

        output_text = ""
        result: dict[str, Any] = {
            "quality": "failed",
            "published": False,
            "reason": "compression_or_quality_provider_not_configured",
            "target_ratio": self.policy.compression_target_ratio,
            "ratio_required": self.policy.compression_require_ratio,
            "original_bytes": len(item.content.encode("utf-8")),
        }
        if self.compressor is not None and self.quality is not None:
            parts = chunks(item.content, self.tokenizer.count, self.policy.extraction_chunk_tokens)
            compressed, reports = [], []
            for index, (start, end, text) in enumerate(parts):
                checkpoint_key = fingerprint(
                    [
                        task.task_id,
                        self.checkpoint_binding(),
                        item.content_hash,
                        index,
                        text_hash(text),
                        "quality_repair_v1",
                    ]
                )
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
                    part = await compress_part(self, ctx, task, text, checkpoint_key)
                    checkpoint = {
                        **part,
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
            # Keep the target visible even when ratio is advisory. Quality is never optional.
            ratio_met = ratio >= max(5.0, self.policy.compression_target_ratio)
            result.update(
                ratio=ratio,
                ratio_met=ratio_met,
                quality="passed" if quality_ok else "failed",
                stored_bytes=size,
                token_ratio=self.tokenizer.count(item.content)
                / max(1, self.tokenizer.count(output_text)),
                quality_evidence=[r["quality"] for r in reports],
                quality_attempts=[r.get("attempts", 1) for r in reports],
                covered_ranges=[[r["start_char"], r["end_char"]] for r in reports],
                strategy="chunked_quality_v3",
                reason="quality_rejected" if not quality_ok else "ratio_unmet",
                declared_use="locator_only"
                if any(r["quality"].get("declared_use") == "locator_only" for r in reports)
                else "supported_summary",
            )
            if quality_ok and (ratio_met or not self.policy.compression_require_ratio):
                location = self.bodies.location(item.ref.scope, output_text)
                result.update(
                    quality="passed",
                    published=True,
                    reason="quality_and_ratio_passed"
                    if ratio_met
                    else "quality_passed_ratio_target_unmet",
                    location=location.model_copy(update={"kind": "artifact"}).model_dump(
                        mode="json"
                    ),
                )
        return {"result": result, "text": output_text if result["published"] else None}

    def commit_compression(
        self, ctx: TrustedContext, task: TaskRecord, item: MemorySnapshot, prepared: dict[str, Any]
    ) -> RunResult:
        result = prepared["result"]
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

    async def occurrence_evidence_context(
        self,
        ctx: TrustedContext,
        task: TaskRecord,
        candidate: CandidateFact,
        target: MemorySnapshot,
        relation: dict[str, Any],
        items: tuple[MemorySnapshot, ...],
    ) -> OccurrenceContext | None:
        existing = occurrence_context(candidate, target, relation, items)
        if existing is not None:
            return existing
        # Only fetch complete bounded originals. Long sources require a separately
        # designed contextual reader; an arbitrary excerpt cannot prove identity.
        originals = list(items)
        for source in target.sources:
            if any(source in item.sources for item in originals):
                continue
            try:
                with self.uow.transaction() as tx:
                    self.tasks.guard(tx, task)
                    self.source_access.checked(tx, ctx, source)
                    manifest = tx.read("remember_source_ranges", source.source_id)
                    if (
                        not manifest
                        or manifest["source"] != source.model_dump(mode="json")
                        or not 0 < manifest["chars"] <= 8192
                    ):
                        continue
                    size = manifest["chars"]
                page = await self.source_access.read(ctx, source, 0, size)
                text = page["content"]
                if not page["is_complete"] or text_hash(text) != source.content_hash:
                    continue
                with self.uow.transaction() as tx:
                    self.tasks.guard(tx, task)
                    self.source_access.checked(tx, ctx, source)
                originals.append(
                    target.model_copy(
                        update={
                            "content": text,
                            "content_hash": source.content_hash,
                            "sources": (source,),
                        }
                    )
                )
            except FoundationError as exc:
                if exc.code not in {
                    ErrorCode.NOT_FOUND,
                    ErrorCode.MEMORY_GONE,
                    ErrorCode.FORBIDDEN,
                    ErrorCode.DEPENDENCY_UNAVAILABLE,
                }:
                    raise
                continue
            except (OSError, TimeoutError):
                continue
            found = occurrence_context(candidate, target, relation, tuple(originals))
            if found is not None:
                return found
        return None

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
        if stored and stored.get("binding") == self.checkpoint_binding():
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
                        if summary and summary["memory"] != item.ref.model_dump(mode="json"):
                            summary = None
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
                        [
                            task.task_id,
                            self.checkpoint_binding(),
                            item.ref.model_dump(mode="json"),
                            offset,
                            text_hash(piece),
                        ]
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
        rejections: dict[str, dict[str, Any]] = {}
        for candidate_index, candidate in enumerate(found):
            candidate = self.validate_candidate(candidate, items)
            quotes = "\n".join(e.quote for e in candidate.evidence)
            if candidate.text not in quotes:
                if self.support_verifier is None:
                    raise FoundationError(
                        ErrorCode.CONTRACT_VIOLATION,
                        "paraphrased candidate requires an independent support verifier",
                    )
                candidate_hash = fingerprint(candidate.model_dump(mode="json"))
                binding = self.checkpoint_binding()
                verification_key = fingerprint([task.task_id, binding, candidate_hash])
                with self.uow.transaction() as tx:
                    verification = tx.read("remember_support_verifications", verification_key)
                if verification is None:
                    self.consume_call(task)
                    # Provider failures must propagate: unavailable evidence is not
                    # a negative verdict and must remain retryable without a cache entry.
                    supported = await self.support_verifier.verify(ctx, candidate, quotes)
                    if not isinstance(supported, bool):
                        raise FoundationError(
                            ErrorCode.CONTRACT_VIOLATION, "invalid support verifier verdict"
                        )
                    verification = {
                        "task_id": task.task_id,
                        "binding": binding,
                        "candidate_hash": candidate_hash,
                        "sources": [s.model_dump(mode="json") for s in candidate.sources],
                        "evidence": [e.model_dump(mode="json") for e in candidate.evidence],
                        "status": "supported" if supported else "rejected",
                        "reason": "evidence_supports_claim"
                        if supported
                        else "evidence_does_not_support_claim",
                    }
                    with self.uow.transaction() as tx:
                        self.tasks.guard(tx, task)
                        tx.write("remember_support_verifications", verification_key, verification)
                if verification["status"] == "rejected":
                    rejections[verification_key] = verification
                    continue
            # Identical words in distinct events or source ranges are not one occurrence.
            key: tuple[Any, ...]
            if candidate.kind == "episodic":
                # Generated keys are hints, including when they collide. Only identical
                # claims grounded in the same exact evidence may coalesce before review.
                key = (
                    candidate.kind,
                    canonical_text(candidate.text),
                    tuple(
                        sorted(fingerprint(e.model_dump(mode="json")) for e in candidate.evidence)
                    ),
                    None
                    if direct_occurrence_evidence(
                        candidate.text, [e.model_dump(mode="json") for e in candidate.evidence]
                    )
                    else candidate_index,
                )
            else:
                # A generated fact/event key identifies a retrieval candidate, not
                # evidence that different clauses should be concatenated into one fact.
                key = (candidate.kind, canonical_text(candidate.text))
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
                {
                    "binding": self.checkpoint_binding(),
                    "candidates": [c.model_dump(mode="json") for c in merged.values()],
                    "rejections": list(rejections.values()),
                },
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
                kwargs: dict[str, Any] = {}
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
                        self.checkpoint_binding(),
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
                        except ValueError as exc:
                            if repair == 2:
                                raise FoundationError(
                                    ErrorCode.CONTRACT_VIOLATION,
                                    "model extraction evidence invalid after bounded repair",
                                ) from exc
                            if getattr(self.extraction, "supports_evidence_repair", False):
                                kwargs["repair_feedback"] = {
                                    **(
                                        exc.feedback
                                        if isinstance(exc, EvidenceValidationError)
                                        else {"reason": "invalid_extraction_schema"}
                                    ),
                                    "attempt": repair + 1,
                                    "instruction": "Regenerate the complete result. Copy exact "
                                    "unique source quotes including whitespace, punctuation and "
                                    "Markdown. Never invent missing evidence or source IDs.",
                                }
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
                    memory_source="long_term",
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
        return self.related_metadata(ctx, candidate, scope, discovered)

    def related_metadata(
        self, ctx: TrustedContext, candidate: CandidateFact, scope: Scope, discovered: list[str]
    ) -> tuple[MemorySnapshot, ...]:
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
                near_exact = bool(
                    item.kind.value == candidate.kind
                    and discovery_text(candidate.text)
                    and discovery_text(item.content) == discovery_text(candidate.text)
                )
                stable = bool(
                    candidate.fact_key
                    and relation.get("fact_key") == candidate.fact_key
                    or candidate.event_key
                    and relation.get("event_key") == candidate.event_key
                )
                if (
                    exact
                    or near_exact
                    or stable
                    or key in discovered
                    or item.projection_state != ProjectionState.READY
                ):
                    eligible.append(
                        (
                            0 if exact or near_exact or stable else 1 if key in discovered else 2,
                            item,
                        )
                    )
            rank = {key: index for index, key in reversed(list(enumerate(discovered)))}
            eligible.sort(
                key=lambda x: (x[0], rank.get(x[1].ref.memory_id, len(rank)), x[1].ref.memory_id)
            )
            return tuple(x[1] for x in eligible[: self.policy.comparison_candidates])

    async def extract_batch(
        self, ctx: TrustedContext, task: TaskRecord, items: tuple[MemorySnapshot, ...]
    ) -> RunResult:
        for _ in range(self.policy.max_commit_retries + 1):
            prepared = await self.generate_extraction(ctx, task, items)
            for candidate, decision, _ in prepared["proposals"]:
                # Commit may preserve an earlier equivalence as a conflict after
                # a batch amendment. Its immutable body must already be confirmed.
                if decision["outcome"] != "reject":
                    await self.bodies.persist(ctx, items[0].ref.scope, candidate["text"])
            result = self.commit_extraction(ctx, task, items, prepared)
            if result is not None:
                return result
        return RunResult(
            outcome="failed",
            effect_status=EffectStatus.NO_EFFECT,
            reason="comparison commit conflict retry budget exhausted",
        )

    async def generate_extraction(
        self, ctx: TrustedContext, task: TaskRecord, items: tuple[MemorySnapshot, ...]
    ) -> dict[str, Any]:
        candidates = await self.candidates(ctx, task, items)
        if task.kind == "remember.distill":
            candidates = tuple(c for c in candidates if c.kind == "semantic")
        with self.uow.transaction() as tx:
            batch = tx.read("remember_batches", task.task_id)
            candidate_checkpoint = tx.read("remember_candidates", task.task_id) or {}
            rejections = candidate_checkpoint.get("rejections", [])
        refs = (
            tuple(MemoryRef.model_validate(r) for r in batch["refs"])
            if batch
            else tuple(x.ref for x in items)
        )
        with self.uow.transaction() as tx:
            self.tasks.guard(tx, task)
            attempt = (tx.read("remember_comparison_retries", task.task_id) or {}).get(
                "attempts", 0
            )
            if attempt > self.policy.max_commit_retries:
                raise FoundationError(
                    ErrorCode.CONTRACT_VIOLATION, "comparison retry budget exhausted"
                )
            space_key = self.space_key(items[0].ref.scope)
            expected_space_seq = tx.read("remember_space_seq", space_key) or 0
            source_binding = self.comparison_source_binding(tx, items)
            processing_binding = self.checkpoint_binding()
            checkpoint_key = fingerprint(
                [
                    "remember_comparison_candidates_v1",
                    task.task_id,
                    task.kind,
                    task.input_ref.model_dump(mode="json"),
                    processing_binding,
                    attempt,
                    space_key,
                    expected_space_seq,
                    source_binding,
                    [r.model_dump(mode="json") for r in refs],
                    [item.model_dump(mode="json") for item in items],
                    [candidate.model_dump(mode="json") for candidate in candidates],
                ]
            )
            checkpoint = tx.read("remember_comparison_parts", checkpoint_key)
        proposals: list[tuple[CandidateFact, ComparisonDecision, MemorySnapshot | None]] = []
        virtual: dict[str, MemorySnapshot] = {}
        virtual_relations: dict[str, Any] = {}
        cursor = 0
        if checkpoint is not None:
            cursor = checkpoint["cursor"]
            if type(cursor) is not int or not 0 <= cursor <= len(candidates):
                raise FoundationError(ErrorCode.CONTRACT_VIOLATION, "invalid comparison cursor")
            proposals = [
                (
                    self.validate_candidate(CandidateFact.model_validate(c), items),
                    ComparisonDecision.model_validate(d),
                    MemorySnapshot.model_validate(t) if t else None,
                )
                for c, d, t in checkpoint["proposals"]
            ]
            # A list preserves candidate order through the canonical JSON encoder;
            # a map would sort virtual IDs and change later model request order.
            for raw_virtual in checkpoint["virtual"]:
                item = MemorySnapshot.model_validate(raw_virtual)
                virtual[item.ref.memory_id] = item
            virtual_relations = checkpoint["virtual_relations"]
        for candidate_index, candidate in enumerate(candidates[cursor:], start=cursor):
            verified_amend_identity = False
            existing: tuple[MemorySnapshot, ...]
            duplicate = self.duplicate(ctx, candidate, items[0].ref.scope)
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
            if decision.outcome == "create":
                # A model may overlook punctuation variants across chunks or batches.
                # Discovery never authorizes merging: independent conditions and event
                # identity checks below still decide whether this is the same memory.
                repeated = next(
                    (
                        item
                        for item in existing
                        if item.kind.value == candidate.kind
                        and discovery_text(candidate.text)
                        and discovery_text(item.content) == discovery_text(candidate.text)
                        and (
                            candidate.kind == "semantic"
                            or any(source in item.sources for source in candidate.sources)
                        )
                    ),
                    None,
                )
                if repeated is not None:
                    target = repeated
                    decision = ComparisonDecision(
                        outcome="no_change"
                        if canonical_text(target.content) == canonical_text(candidate.text)
                        else "equivalent",
                        target_id=target.ref.memory_id,
                        reason="near_exact_requires_identity_and_conditions_verification",
                    )
            if (
                decision.outcome in {"no_change", "equivalent", "amend", "correct", "conflict"}
                and target is None
            ):
                raise FoundationError(
                    ErrorCode.CONTRACT_VIOLATION, "comparison target was not a candidate"
                )
            if decision.outcome in {"create", "reject"} and decision.target_id is not None:
                raise FoundationError(ErrorCode.CONTRACT_VIOLATION, "unexpected comparison target")
            if decision.outcome == "no_change" and target is not None:
                if target.kind.value != candidate.kind:
                    raise FoundationError(
                        ErrorCode.CONTRACT_VIOLATION, "duplicate target kind mismatch"
                    )
                if canonical_text(target.content) != canonical_text(candidate.text):
                    # A model may use NONE for paraphrases. Require the same independent
                    # identity/condition check as an explicit equivalence proposal.
                    decision = decision.model_copy(
                        update={
                            "outcome": "equivalent",
                            "reason": "model_no_change_requires_equivalence_verification: "
                            + decision.reason,
                        }
                    )
            if decision.outcome == "no_change" and candidate.kind == "episodic":
                assert target is not None
                with self.uow.transaction() as tx:
                    relation = tx.read(
                        "remember_relations", target.ref.memory_id
                    ) or virtual_relations.get(target.ref.memory_id, {})
                same_origin = bool(
                    direct_occurrence_evidence(target.content, relation.get("evidence", []))
                    & direct_occurrence_evidence(
                        candidate.text, [e.model_dump(mode="json") for e in candidate.evidence]
                    )
                )
                if not same_origin:
                    decision = ComparisonDecision(
                        outcome="equivalent",
                        target_id=target.ref.memory_id,
                        reason="same_text_requires_occurrence_verification",
                    )
            if decision.outcome == "equivalent" or (
                decision.outcome == "amend"
                and candidate.kind == "episodic"
                and target is not None
                and target.kind == MemoryKind.EPISODIC
            ):
                checking_amend = decision.outcome == "amend"
                if target is None or target.kind.value != candidate.kind:
                    raise FoundationError(
                        ErrorCode.CONTRACT_VIOLATION, "equivalence target kind mismatch"
                    )
                with self.uow.transaction() as tx:
                    relation = tx.read(
                        "remember_relations", target.ref.memory_id
                    ) or virtual_relations.get(target.ref.memory_id, {})
                same_evidence = bool(
                    direct_occurrence_evidence(target.content, relation.get("evidence", []))
                    & direct_occurrence_evidence(
                        candidate.text, [e.model_dump(mode="json") for e in candidate.evidence]
                    )
                )
                identity_known = candidate.kind == "semantic" or same_evidence
                alias_context = None
                verifier = self.equivalence_verifier
                verify_occurrence = getattr(verifier, "verify_occurrence", None)
                if not identity_known and callable(verify_occurrence):
                    import json

                    alias_context = await self.occurrence_evidence_context(
                        ctx, task, candidate, target, relation, items
                    )
                    if alias_context is not None:
                        context_tokens = self.tokenizer.count(
                            json.dumps(
                                {
                                    "candidate": candidate.model_dump(mode="json"),
                                    "existing": target.model_dump(mode="json"),
                                    "occurrence_context": alias_context.model_dump(mode="json"),
                                },
                                ensure_ascii=False,
                            )
                        )
                        if context_tokens > self.policy.comparison_context_tokens:
                            alias_context = None
                if candidate.kind == "semantic" and verifier is None:
                    raise FoundationError(
                        ErrorCode.DEPENDENCY_UNAVAILABLE,
                        "semantic equivalence verifier is not configured",
                    )
                # A directly quoted claim establishes occurrence identity, not that added
                # statements preserve the old claim. Any changed text needs a verifier.
                verified = checking_amend and same_evidence and candidate.text == target.content
                if verified:
                    with self.uow.transaction() as tx:
                        self.tasks.guard(tx, task)
                        tx.write(
                            "remember_equivalence_checks",
                            fingerprint([task.task_id, attempt, len(proposals)]),
                            {
                                "same_occurrence": True,
                                "basis": "exact_shared_evidence",
                                "evidence": [e.model_dump(mode="json") for e in candidate.evidence],
                                "candidate_event_key": candidate.event_key,
                                "existing_event_key": relation.get("event_key"),
                                "compared_memory": target.ref.model_dump(mode="json"),
                                "compared_object_revision": target.object_revision,
                                "compared_content_hash": target.content_hash,
                            },
                        )
                elif (identity_known and verifier is not None) or alias_context is not None:
                    self.consume_call(task)
                    verdict: EquivalenceVerdict
                    if alias_context is not None:
                        assert callable(verify_occurrence)
                        occurrence_verdict = OccurrenceVerdict.model_validate(
                            await verify_occurrence(ctx, candidate, target, alias_context)
                        )
                        occurrence_verified = (
                            occurrence_verdict.same_occurrence
                            and occurrence_verdict.candidate_context_quote
                            == alias_context.candidate_context.quote
                            and occurrence_verdict.existing_context_quote
                            == alias_context.existing_context.quote
                        )
                        verdict = occurrence_verdict
                    else:
                        assert verifier is not None
                        verdict = EquivalenceVerdict.model_validate(
                            await verifier.verify(ctx, candidate, target)
                        )
                        occurrence_verified = identity_known
                    audit = verdict.model_dump(mode="json")
                    if alias_context is not None:
                        # Audit is tied to this target version and immutable source;
                        # no global alias map or future identity shortcut is created.
                        audit.update(
                            occurrence_context=alias_context.model_dump(mode="json"),
                            candidate_event_key=candidate.event_key,
                            existing_event_key=relation.get("event_key"),
                            compared_memory=target.ref.model_dump(mode="json"),
                            compared_object_revision=target.object_revision,
                            compared_content_hash=target.content_hash,
                        )
                    with self.uow.transaction() as tx:
                        self.tasks.guard(tx, task)
                        tx.write(
                            "remember_equivalence_checks",
                            fingerprint([task.task_id, attempt, len(proposals)]),
                            audit,
                        )
                    verified = (
                        (checking_amend or verdict.equivalent)
                        and verdict.same_identity
                        and verdict.preserves_conditions
                        and verdict.candidate_quote == candidate.text
                        and verdict.existing_quote == target.content
                        and occurrence_verified
                    )
                if not verified:
                    decision = ComparisonDecision(
                        outcome="create", reason="equivalence_not_verified_preserve_candidate"
                    )
                    target = None
                elif checking_amend:
                    verified_amend_identity = True
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
                    relation = tx.read(
                        "remember_relations", target.ref.memory_id
                    ) or virtual_relations.get(target.ref.memory_id, {})
                same_event = bool(
                    candidate.kind == "episodic"
                    and target.kind == MemoryKind.EPISODIC
                    and verified_amend_identity
                )
                if same_event and target.content in candidate.text:
                    if relation.get("event_key"):
                        # Only this verified proposal is bound to the canonical key for
                        # commit's existing CAS check. The audit retains the generated key.
                        candidate = candidate.model_copy(
                            update={"event_key": relation["event_key"]}
                        )
                        with self.uow.transaction() as tx:
                            self.identity.authorize(
                                tx, ctx, Permission.CORRECT, memory_ref(target.ref)
                            )
                    else:
                        decision = decision.model_copy(
                            update={
                                "outcome": "conflict",
                                "reason": "verified_occurrence_has_no_canonical_key_preserve_both",
                            }
                        )
                elif candidate.kind == "episodic" and not same_event:
                    decision = ComparisonDecision(
                        outcome="create", reason="unverified_event_identity_preserve_occurrence"
                    )
                    target = None
                else:
                    decision = decision.model_copy(
                        update={
                            "outcome": "conflict",
                            "reason": "unverified_amendment_preserve_both: " + decision.reason,
                        }
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
                        "supersedes": None,
                        "projection_state": ProjectionState.PENDING,
                        "model_space": None,
                    }
                )
                virtual_relations[virtual_id] = {
                    "event_key": candidate.event_key,
                    "fact_key": candidate.fact_key,
                    "evidence": [entry.model_dump(mode="json") for entry in candidate.evidence],
                }
            # Only fully validated decisions and their resulting virtual state are
            # durable. A failed model/verifier call cannot advance this cursor.
            # Keep the input index independent from proposals (e.g. rejection or
            # future skip branches); retrying must never shift candidate identity.
            with self.uow.transaction() as tx:
                self.tasks.guard(tx, task)
                if (
                    (tx.read("remember_space_seq", space_key) or 0) == expected_space_seq
                    and (tx.read("remember_comparison_retries", task.task_id) or {}).get(
                        "attempts", 0
                    )
                    == attempt
                    and self.comparison_source_binding(tx, items) == source_binding
                    and self.checkpoint_binding() == processing_binding
                ):
                    tx.write(
                        "remember_comparison_parts",
                        checkpoint_key,
                        {
                            "task_id": task.task_id,
                            "binding": checkpoint_key,
                            "cursor": candidate_index + 1,
                            "proposals": [
                                [
                                    c.model_dump(mode="json"),
                                    d.model_dump(mode="json"),
                                    t.model_dump(mode="json") if t else None,
                                ]
                                for c, d, t in proposals
                            ],
                            "virtual": [v.model_dump(mode="json") for v in virtual.values()],
                            "virtual_relations": virtual_relations,
                        },
                    )
        return {
            "attempt": attempt,
            "space_key": space_key,
            "expected_space_seq": expected_space_seq,
            "refs": [r.model_dump(mode="json") for r in refs],
            "virtual": list(virtual),
            "candidate_count": len(candidates),
            "candidate_rejections": rejections,
            "proposals": [
                [
                    c.model_dump(mode="json"),
                    d.model_dump(mode="json"),
                    t.model_dump(mode="json") if t else None,
                ]
                for c, d, t in proposals
            ],
        }

    @staticmethod
    def comparison_source_binding(
        tx: MetadataTransaction, items: tuple[MemorySnapshot, ...]
    ) -> str:
        """Source validity/provenance changes invalidate saved semantic decisions."""
        return fingerprint(
            [
                [
                    source.model_dump(mode="json"),
                    tx.read("remember_sources", source.source_id),
                    tx.read("remember_source_input", source.source_id),
                    tx.read("remember_source_policy", source.source_id),
                ]
                for item in items
                for source in item.sources
            ]
        )

    def commit_extraction(
        self,
        ctx: TrustedContext,
        task: TaskRecord,
        items: tuple[MemorySnapshot, ...],
        prepared: dict[str, Any],
    ) -> RunResult | None:
        attempt, space_key = prepared["attempt"], prepared["space_key"]
        expected_space_seq, virtual = prepared["expected_space_seq"], prepared["virtual"]
        refs = tuple(MemoryRef.model_validate(r) for r in prepared["refs"])
        proposals = [
            (
                CandidateFact.model_validate(c),
                ComparisonDecision.model_validate(d),
                MemorySnapshot.model_validate(t) if t else None,
            )
            for c, d, t in prepared["proposals"]
        ]
        with self.uow.transaction() as tx:
            self.tasks.guard(tx, task)
            if (tx.read("remember_space_seq", space_key) or 0) != expected_space_seq:
                tx.write("remember_comparison_retries", task.task_id, {"attempts": attempt + 1})
                return None
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
                    self.current(tx, target.ref.memory_id).object_revision != target.object_revision
                    or self.final_guard(tx, ctx, (target.ref,), "recall").items[0].decision
                    != "allowed"
                )
                for _, _, target in proposals
            ):
                tx.write("remember_comparison_retries", task.task_id, {"attempts": attempt + 1})
                return None
            outputs, decisions = [], []
            for index, (candidate, decision, target) in enumerate(proposals):
                compared_ref = target.ref if target else None
                if target is not None:
                    # Earlier proposals can change this object inside the same
                    # transaction, after the external CAS checks above passed.
                    latest = self.current(tx, target.ref.memory_id)
                    changed_content = latest.content_hash != target.content_hash
                    if decision.outcome == "amend":
                        relation = tx.read("remember_relations", latest.ref.memory_id) or {}
                        if (
                            candidate.kind == "episodic"
                            and latest.kind == MemoryKind.EPISODIC
                            and candidate.event_key
                            and relation.get("event_key") == candidate.event_key
                            and latest.content in candidate.text
                        ):
                            self.identity.authorize(
                                tx, ctx, Permission.CORRECT, memory_ref(latest.ref)
                            )
                        else:
                            decision = decision.model_copy(
                                update={
                                    "outcome": "conflict",
                                    "reason": "batch_amendment_would_lose_content_preserve_both",
                                }
                            )
                    elif decision.outcome in {"no_change", "equivalent"} and changed_content:
                        # Equivalence was checked against an older body, so it
                        # cannot be recorded as proof for this newly amended one.
                        decision = decision.model_copy(
                            update={
                                "outcome": "conflict",
                                "reason": "batch_comparison_target_changed_preserve_both",
                            }
                        )
                    target = latest
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
                        "compared_memory": compared_ref.model_dump(mode="json")
                        if compared_ref
                        else None,
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
                            s.model_dump_json(): s for s in (*current.sources, *candidate.sources)
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
                    tx.write("remember_conflicts", group.group_id, group.model_dump(mode="json"))
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
                    "candidate_count": prepared["candidate_count"],
                    "rejected_candidate_count": len(prepared.get("candidate_rejections", [])),
                    "candidate_rejections": prepared.get("candidate_rejections", []),
                    "zero_output": not outputs,
                    "zero_output_reason": (
                        "all_candidates_rejected"
                        if not prepared["candidate_count"] and prepared.get("candidate_rejections")
                        else "no_candidates"
                        if not prepared["candidate_count"]
                        else "no_publication"
                    )
                    if not outputs
                    else None,
                    "awaiting_extraction_provider": deferred,
                },
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
        self, tx: MetadataTransaction, ctx: TrustedContext, refs: tuple[MemoryRef, ...]
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
        prepared = await self.generate_projection(ctx, task, item)
        if isinstance(prepared, RunResult):
            return prepared
        result = await self.publish_projection(ctx, task, item, prepared)
        if result is not None:
            return result
        return await self.commit_projection(ctx, task, item, prepared)

    async def generate_projection(
        self, ctx: TrustedContext, task: TaskRecord, item: MemorySnapshot
    ) -> dict[str, Any] | RunResult:
        with self.uow.transaction() as tx:
            if not self.projection_buildable(tx, item):
                return RunResult(
                    outcome="obsolete",
                    effect_status=EffectStatus.NO_EFFECT,
                    reason="Working summary is not ready for projection",
                )
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
                memory_source="working" if item.kind == MemoryKind.WORKING else "long_term",
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
                return RunResult(
                    outcome="obsolete",
                    effect_status=EffectStatus.NO_EFFECT,
                    reason="source changed before embedding",
                )
            checkpoint_key = fingerprint([target.vector_id, self.checkpoint_binding()])
            with self.uow.transaction() as tx:
                checkpoint = tx.read("remember_chunk_vectors", checkpoint_key)
                if checkpoint is not None:
                    self.tasks.progress.part(
                        tx,
                        ctx,
                        task,
                        "embedding",
                        "remember_chunk_vectors",
                        checkpoint_key,
                        config_version=self.policy.version,
                    )
            if checkpoint is None:
                self.consume_call(task)
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
                    tx.write("remember_chunk_vectors", checkpoint_key, checkpoint)
                    self.tasks.progress.part(
                        tx,
                        ctx,
                        task,
                        "embedding",
                        "remember_chunk_vectors",
                        checkpoint_key,
                        config_version=self.policy.version,
                    )
            if dimensions is not None and dimensions != len(checkpoint["vector"]):
                raise FoundationError(ErrorCode.CONTRACT_VIOLATION, "mixed embedding dimensions")
            dimensions = len(checkpoint["vector"])
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
        return {
            "targets": [t.model_dump(mode="json") for t in targets],
            "descriptors": [d.model_dump(mode="json") for d in descriptors],
            "dimensions": dimensions,
            "binding": self.checkpoint_binding(),
        }

    async def publish_projection(
        self,
        ctx: TrustedContext,
        task: TaskRecord,
        item: MemorySnapshot,
        prepared: dict[str, Any],
        *,
        reconcile: bool = False,
    ) -> RunResult | None:
        targets = tuple(ProjectionTarget.model_validate(t) for t in prepared["targets"])
        for target in targets:
            with self.uow.transaction() as tx:
                self.tasks.guard(tx, task)
                valid = (
                    self.final_guard(tx, ctx, (item.ref,), "recall").items[0].decision == "allowed"
                )
                key = fingerprint([task.task_id, "project", target.vector_id])
                started = tx.read("temporal_projection_writes", key)
                vector_key = fingerprint([target.vector_id, prepared["binding"]])
                checkpoint = tx.read("remember_chunk_vectors", vector_key)
            if not valid:
                return await self.discard_projection(ctx, task, targets)
            observed = await self.projections.inspect(ctx, target, task.task_id)
            if observed.target != target or observed.operation_id != task.task_id:
                raise FoundationError(
                    ErrorCode.CONTRACT_VIOLATION, "projection query binding mismatch"
                )
            if observed.state != "verified":
                if started:
                    return RunResult(
                        outcome="uncertain",
                        effect_status=EffectStatus.UNKNOWN,
                        operation_id=task.task_id,
                        reason="projection write unconfirmed",
                    )
                if reconcile:
                    return RunResult(
                        outcome="retryable_no_effect",
                        effect_status=EffectStatus.NO_EFFECT,
                        reason="projection write never started",
                    )
                with self.uow.transaction() as tx:
                    self.tasks.guard(tx, task)
                    tx.write(
                        "temporal_projection_writes",
                        key,
                        {"target": target.model_dump(mode="json")},
                    )
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
                    reason="projection write unconfirmed",
                )
        return None

    async def commit_projection(
        self, ctx: TrustedContext, task: TaskRecord, item: MemorySnapshot, prepared: dict[str, Any]
    ) -> RunResult:
        result = self.commit_projection_metadata(ctx, task, item, prepared)
        if result is not None:
            return result
        targets = tuple(ProjectionTarget.model_validate(t) for t in prepared["targets"])
        return await self.discard_projection(ctx, task, targets)

    def commit_projection_metadata(
        self, ctx: TrustedContext, task: TaskRecord, item: MemorySnapshot, prepared: dict[str, Any]
    ) -> RunResult | None:
        targets = tuple(ProjectionTarget.model_validate(t) for t in prepared["targets"])
        descriptors = tuple(ChunkDescriptor.model_validate(d) for d in prepared["descriptors"])
        generation, dimensions = task.task_id, prepared["dimensions"]
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
        return None

    async def discard_projection(
        self, ctx: TrustedContext, task: TaskRecord, targets: tuple[ProjectionTarget, ...]
    ) -> RunResult:
        result = await self.publish_cleanup(
            ctx, task, {"targets": [t.model_dump(mode="json") for t in targets], "legacy": []}
        )
        if result is not None:
            return result
        return RunResult(
            outcome="obsolete",
            effect_status=EffectStatus.CONFIRMED,
            reason="invalidated generation cleaned",
        )

    async def cleanup(
        self, ctx: TrustedContext, task: TaskRecord, item: MemorySnapshot
    ) -> RunResult:
        prepared = self.prepare_cleanup(ctx, task, item)
        if isinstance(prepared, RunResult):
            return prepared
        result = await self.publish_cleanup(ctx, task, prepared)
        if result is not None:
            return result
        return self.commit_cleanup(ctx, task)

    def prepare_cleanup(
        self, ctx: TrustedContext, task: TaskRecord, item: MemorySnapshot
    ) -> dict[str, Any] | RunResult:
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
                and row["target"]["memory"]["version"] <= item.ref.version
                and row["target"]["memory"]["scope"] == item.ref.scope.model_dump(mode="json")
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
        return {
            "targets": [t.model_dump(mode="json") for t in targets],
            "legacy": [i.model_dump(mode="json") for i in legacy],
        }

    async def publish_cleanup(
        self,
        ctx: TrustedContext,
        task: TaskRecord,
        prepared: dict[str, Any],
        *,
        reconcile: bool = False,
    ) -> RunResult | None:
        targets = [ProjectionTarget.model_validate(t) for t in prepared["targets"]]
        legacy = [MemorySnapshot.model_validate(i) for i in prepared["legacy"]]
        with self.uow.transaction() as tx:
            self.tasks.guard(tx, task)
            frozen = MemoryRef.model_validate(required_record(tx, task.input_ref)["ref"])
        # An expired v1 cleanup can arrive after correction has published v2.
        # Its authority ends at the admitted version, including on replay.
        if any(
            ref.memory_id != frozen.memory_id
            or ref.scope != frozen.scope
            or ref.version > frozen.version
            for ref in [*(target.memory for target in targets), *(item.ref for item in legacy)]
        ):
            raise FoundationError(
                ErrorCode.CONTRACT_VIOLATION, "cleanup target exceeds admitted memory version"
            )
        for target in targets:
            key = fingerprint([task.task_id, "delete", target.vector_id])
            with self.uow.transaction() as tx:
                self.tasks.guard(tx, task)
                started = tx.read("temporal_projection_writes", key)
            result = await self.projections.inspect(ctx, target, task.task_id)
            if result.target != target or result.operation_id != task.task_id:
                raise FoundationError(
                    ErrorCode.CONTRACT_VIOLATION, "cleanup query binding mismatch"
                )
            if started:
                if result.state != "absent":
                    return RunResult(
                        outcome="uncertain",
                        effect_status=EffectStatus.UNKNOWN,
                        operation_id=task.task_id,
                        reason="cleanup write unconfirmed",
                    )
            else:
                if reconcile:
                    return RunResult(
                        outcome="retryable_no_effect",
                        effect_status=EffectStatus.NO_EFFECT,
                        reason="cleanup write never started",
                    )
                with self.uow.transaction() as tx:
                    self.tasks.guard(tx, task)
                    tx.write(
                        "temporal_projection_writes",
                        key,
                        {"target": target.model_dump(mode="json")},
                    )
                result = await self.projections.delete(ctx, target, task.task_id)
            if (
                result.state != "absent"
                or result.target != target
                or result.operation_id != task.task_id
            ):
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
                if not await self.bodies.cache.cleanup_complete(old.ref, old.content_hash):
                    return RunResult(
                        outcome="uncertain",
                        effect_status=EffectStatus.UNKNOWN,
                        operation_id=task.task_id,
                        reason="body cache cleanup unconfirmed",
                    )
        return None

    def commit_cleanup(self, ctx: TrustedContext, task: TaskRecord) -> RunResult:
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
