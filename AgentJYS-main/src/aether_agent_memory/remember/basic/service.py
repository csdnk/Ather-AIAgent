"""B owns sources, versions, eligibility and durable extraction/projection handlers."""

from __future__ import annotations

from typing import Any, Literal

from aether_agent_memory.recall.contracts.models import EmbeddingRequest
from aether_agent_memory.recall.contracts.ports import EmbeddingPort
from aether_agent_memory.remember.contracts.foundation import (
    CandidateQualificationResult,
    CandidateQualificationTarget,
    ChunkDescriptor,
    ContextGuardRequest,
    GuardStamp,
    ProjectionManifest,
    ProjectionReadiness,
)
from aether_agent_memory.remember.contracts.models import (
    CorrectionRequest,
    DeleteReceipt,
    DeleteRequest,
    EligibilityBatch,
    EligibilityResult,
    ExtractionRequest,
    LifecycleRequest,
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
    SourceRef,
    StorageChanged,
)
from aether_agent_memory.remember.contracts.ports import ExtractionPort, ProjectionPort
from aether_agent_memory.runtime.contracts.foundation import ResourceLocation
from aether_agent_memory.runtime.contracts.models import (
    EffectStatus,
    ErrorCode,
    EventEnvelope,
    Flow,
    PageRequest,
    Permission,
    RecordRef,
    RecoveryAction,
    RecoveryDecision,
    RunResult,
    Scope,
    ScopeSelector,
    TaskRecord,
    TaskSpec,
    TrustedContext,
)
from aether_agent_memory.runtime.contracts.ports import Transaction
from aether_agent_memory.runtime.foundation.common import FoundationError, fingerprint, later
from aether_agent_memory.runtime.foundation.events import Events
from aether_agent_memory.runtime.foundation.identity import Identity
from aether_agent_memory.runtime.foundation.requests import (
    matches,
    request_key,
    select_scope,
    text_hash,
)
from aether_agent_memory.runtime.foundation.storage import (
    SQLiteTransaction,
    SQLiteUnitOfWork,
    native,
)
from aether_agent_memory.runtime.foundation.tasks import Tasks
from aether_agent_memory.runtime.foundation.telemetry import observed


def memory_ref(ref: MemoryRef, *, versioned: bool = False) -> RecordRef:
    return RecordRef(
        owner=Flow.REMEMBER,
        object_type="memory",
        object_id=ref.memory_id,
        scope=ref.scope,
        version=ref.version if versioned else None,
    )


@observed("remember")
class Remember:
    @staticmethod
    def refkey(ref: MemoryRef) -> str:
        return fingerprint(ref.model_dump(mode="json"))

    async def projection_readiness(
        self,
        ctx: TrustedContext,
        selection: ScopeSelector,
        memory_source: Literal["working", "long_term"],
    ) -> ProjectionReadiness:
        from .boundary import RememberBoundary

        return await RememberBoundary(self).projection_readiness(ctx, selection, memory_source)

    async def qualify(
        self,
        ctx: TrustedContext,
        targets: tuple[CandidateQualificationTarget, ...],
        purpose: Literal["recall", "extraction"],
    ) -> tuple[CandidateQualificationResult, ...]:
        from .boundary import RememberBoundary

        return await RememberBoundary(self).qualify(ctx, targets, purpose)

    def revalidate_context(
        self,
        tx: Transaction,
        ctx: TrustedContext,
        request: ContextGuardRequest,
    ) -> tuple[GuardStamp, ...]:
        from .boundary import RememberBoundary

        return RememberBoundary(self).revalidate_context(tx, ctx, request)

    def __init__(
        self,
        uow: SQLiteUnitOfWork,
        identity: Identity,
        tasks: Tasks,
        events: Events,
        extraction: ExtractionPort,
        embedding: EmbeddingPort,
        projections: ProjectionPort,
        model_space: str,
    ) -> None:
        self.uow, self.identity, self.tasks, self.events = uow, identity, tasks, events
        self.extraction, self.embedding, self.projections, self.model_space = (
            extraction,
            embedding,
            projections,
            model_space,
        )
        for kind in ("remember.extract", "remember.project", "remember.cleanup"):
            tasks.register(
                kind,
                "remember",
                self,
                permission=Permission.READ,
            )
        events.register_type("memory.changed", self.validate_event, permission=Permission.READ)

    @staticmethod
    def validate_event(event: EventEnvelope) -> None:
        value = StorageChanged.model_validate(event.payload)
        if (
            event.producer != Flow.REMEMBER
            or event.subject != memory_ref(value.memory, versioned=True)
            or event.subject_revision != value.object_revision
        ):
            raise ValueError("memory event subject/producer mismatch")

    def emit(
        self, tx: SQLiteTransaction, ctx: TrustedContext, memory: MemorySnapshot, change: str
    ) -> None:
        if change == "corrected" and hasattr(self, "retention"):
            self.retention.version_started_in(tx, memory)
        if change in {"archived", "expired"} and hasattr(self, "retention"):
            self.retention.inactive_in(tx, memory)
        prior = tx.read("remember_signal_state", memory.ref.memory_id) or {}
        semantic_hash = fingerprint(
            [
                memory.ref.version,
                memory.content_hash,
                memory.status.value,
                [s.model_dump(mode="json") for s in memory.sources],
                memory.expires_at,
            ]
        )
        sources_hash = fingerprint([s.model_dump(mode="json") for s in memory.sources])
        sequence = prior.get("change_seq", 0) + 1
        semantic_revision = prior.get("semantic_revision", 0) + (
            prior.get("semantic_hash") != semantic_hash or change == "processing"
        )
        relations_revision = prior.get("relations_revision", 0) + (
            prior.get("sources_hash") != sources_hash or change == "processing"
        )
        space_id = fingerprint(memory.ref.scope.model_dump(mode="json"))
        payload = StorageChanged(
            memory=memory.ref,
            object_revision=memory.object_revision,
            change=change,
            status=memory.status,
            projection_state=memory.projection_state,
            content_hash=memory.content_hash,
            source_count=len(memory.sources),
            content_bytes=len(memory.content.encode("utf-8")),
            content_ref=self.bodies.location(memory.ref.scope, memory.content).object_key
            if hasattr(self, "bodies")
            else None,
            importance=memory.importance,
            importance_reason=memory.importance_reason,
            importance_policy_version=memory.importance_policy_version,
            memory_kind=memory.kind,
            expires_at=memory.expires_at,
            previous_state=prior.get("status"),
            change_seq=sequence,
            semantic_revision=max(1, semantic_revision),
            relations_revision=max(1, relations_revision),
            space_commit_seq=tx.read("remember_space_seq", space_id) or 0,
            memory_space_id=space_id,
            signal_type=change,
            deleted=memory.status == MemoryStatus.DELETED,
            reason_code=change,
            importance_basis=tuple(s.source_id for s in memory.sources),
            requires_operate_evaluation=change not in {"artifact_ready", "projection_ready"},
        )
        tx.write(
            "remember_signal_state",
            memory.ref.memory_id,
            {
                "change_seq": sequence,
                "semantic_revision": payload.semantic_revision,
                "relations_revision": payload.relations_revision,
                "semantic_hash": semantic_hash,
                "sources_hash": sources_hash,
                "status": memory.status.value,
            },
        )
        self.events.append(
            tx,
            ctx,
            EventEnvelope(
                event_id=fingerprint(
                    [memory.ref.model_dump(mode="json"), memory.object_revision, change]
                ),
                event_type="memory.changed",
                producer=Flow.REMEMBER,
                subject=memory_ref(memory.ref, versioned=True),
                subject_revision=memory.object_revision,
                occurred_at=self.identity.clock(),
                request_id=ctx.request_id,
                trace_id=ctx.trace_id,
                initiator_id=ctx.principal.principal_id,
                initiator_auth_epoch=ctx.principal.auth_epoch,
                payload=payload.model_dump(mode="json"),
                payload_hash=fingerprint(payload.model_dump(mode="json")),
            ),
        )

    def current(self, tx: SQLiteTransaction, memory_id: str) -> MemorySnapshot:
        pointer = tx.read("remember_current", memory_id)
        raw = None if pointer is None else tx.get(RecordRef.model_validate(pointer))
        if raw is None:
            raise FoundationError(ErrorCode.NOT_FOUND, "memory not found")
        return self.decode(tx, raw)

    def decode(self, tx: SQLiteTransaction, raw: Any) -> MemorySnapshot:
        return MemorySnapshot.model_validate(raw)

    def put(self, tx: SQLiteTransaction, snapshot: MemorySnapshot) -> None:
        ref = memory_ref(snapshot.ref, versioned=True)
        tx.put_if_revision(ref, snapshot.model_dump(mode="json"), tx.revision(ref))
        tx.write("remember_current", snapshot.ref.memory_id, ref.model_dump(mode="json"))

    def change(
        self, tx: SQLiteTransaction, snapshot: MemorySnapshot, **values: Any
    ) -> MemorySnapshot:
        changed = MemorySnapshot.model_validate(
            {
                **snapshot.model_dump(),
                "revision": snapshot.revision + 1,
                "object_revision": snapshot.object_revision + 1,
                **values,
            }
        )
        self.put(tx, changed)
        return changed

    def new_source(
        self,
        tx: SQLiteTransaction,
        source_id: str,
        scope: Scope,
        text: str,
        working_id: str | None = None,
    ) -> SourceRef:
        ref = SourceRef(
            source_id=source_id,
            source_version=1,
            content_hash=text_hash(text),
            locator="sqlite:source:" + source_id,
        )
        tx.write(
            "remember_sources",
            source_id,
            {
                "ref": ref.model_dump(mode="json"),
                "scope": scope.model_dump(mode="json"),
                "text": text,
                "valid": True,
                "revision": 1,
                "working_id": working_id,
            },
        )
        return ref

    def new_memory(
        self,
        tx: SQLiteTransaction,
        memory_id: str,
        scope: Scope,
        text: str,
        sources: tuple[SourceRef, ...],
        kind: MemoryKind,
    ) -> MemorySnapshot:
        item = MemorySnapshot(
            ref=MemoryRef(scope=scope, memory_id=memory_id, version=1),
            revision=1,
            object_revision=1,
            kind=kind,
            status=MemoryStatus.ACTIVE,
            content=text,
            content_hash=text_hash(text),
            sources=sources,
            projection_state=ProjectionState.PENDING,
            created_at=self.identity.clock(),
        )
        self.put(tx, item)
        return item

    def enqueue(
        self, tx: SQLiteTransaction, ctx: TrustedContext, memory: MemorySnapshot, kind: str
    ) -> str:
        deadline_key = request_key(ctx, "remember.processing")
        deadline = tx.read("remember_deadlines", deadline_key)
        if deadline is None:
            deadline = later(self.identity.clock(), getattr(self, "processing_seconds", 86400))
            tx.write("remember_deadlines", deadline_key, deadline)
        ctx = ctx.model_copy(update={"deadline_at": deadline})
        task_id = fingerprint([kind, memory.ref.model_dump(mode="json"), ctx.operation_id])
        ref = memory_ref(memory.ref, versioned=True)
        content = tx.get(ref)
        if content is None:
            tx.abort(ErrorCode.NOT_FOUND, "task input memory missing")
        input_ref = RecordRef(
            owner=Flow.REMEMBER,
            object_type="processing_input",
            object_id=task_id,
            scope=memory.ref.scope,
        )
        frozen = tx.get(input_ref)
        if frozen is None:
            tx.put_if_revision(input_ref, content, None)
        else:
            content = frozen
        task = self.tasks.enqueue(
            tx,
            ctx,
            TaskSpec(
                task_id=task_id,
                owner_flow=Flow.REMEMBER,
                kind=kind,
                subject=memory_ref(memory.ref),
                input_ref=input_ref,
                idempotency_key=task_id,
                input_hash=fingerprint(content),
                initiator_id=ctx.principal.principal_id,
                initiator_auth_epoch=ctx.principal.auth_epoch,
                deadline_at=ctx.deadline_at,
            ),
        )
        tx.write("remember_latest_task", fingerprint([memory.ref.memory_id, kind]), task.task_id)
        return task.task_id

    def replay(
        self, tx: SQLiteTransaction, ctx: TrustedContext, endpoint: str, request: Any
    ) -> tuple[str, dict[str, Any] | None]:
        key = request_key(ctx, endpoint)
        previous = tx.read("remember_operations", key)
        signature = self.operation_signature(request)
        if previous and previous["signature"] != signature:
            tx.abort(ErrorCode.IDEMPOTENCY_CONFLICT, "same operation with different content")
        return key, previous

    @staticmethod
    def operation_signature(request: Any) -> str:
        payload = request.model_dump(mode="json")
        if isinstance(request, RememberRequest) and not request.task_context:
            payload.pop("task_context", None)  # Replay requests stored before this optional field.
        return fingerprint(payload)

    @staticmethod
    def remember_result(tx: SQLiteTransaction, key: str, request: Any, result: Any) -> None:
        tx.write(
            "remember_operations",
            key,
            {
                "signature": Remember.operation_signature(request),
                "result": result.model_dump(mode="json"),
            },
        )

    async def save(self, ctx: TrustedContext, request: RememberRequest) -> RememberReceipt:
        scope = select_scope(ctx, request.selection)
        if request.content.kind != "text":
            raise FoundationError(
                ErrorCode.INVALID_ARGUMENT,
                "document adapter is not configured in the basic profile",
            )
        if not request.content.text.strip() or len(request.content.text.encode()) > getattr(
            self, "max_input_bytes", 65536
        ):
            raise FoundationError(
                ErrorCode.INVALID_ARGUMENT, "text exceeds configured input limits"
            )
        with self.uow.transaction() as tx:
            key, previous = self.replay(tx, ctx, "remember.save", request)
            ref = MemoryRef(scope=scope, memory_id=key, version=1)
            self.identity.authorize(tx, ctx, Permission.WRITE, memory_ref(ref))
            if previous:
                return RememberReceipt.model_validate(previous["result"])
            source = self.new_source(
                tx, fingerprint([key, "source"]), scope, request.content.text, key
            )
            tx.write(
                "remember_source_input", source.source_id, request.source.model_dump(mode="json")
            )
            item = self.new_memory(
                tx, key, scope, request.content.text, (source,), MemoryKind.WORKING
            )
            task_id = self.enqueue(tx, ctx, item, "remember.extract")
            projection_id = self.enqueue(tx, ctx, item, "remember.project")
            self.emit(tx, ctx, item, "saved")
            result = RememberReceipt(
                operation_id=ctx.operation_id,
                saved=True,
                source=source,
                memories=(item.ref,),
                task_ids=(task_id, projection_id),
                phase="saved",
            )
            self.remember_result(tx, key, request, result)
            return result

    def get(self, ctx: TrustedContext, memory_id: str) -> MemorySnapshot:
        with self.uow.transaction() as tx:
            item = self.current(tx, memory_id)
            self.identity.authorize(tx, ctx, Permission.READ, memory_ref(item.ref))
            if item.status == MemoryStatus.DELETED:
                raise FoundationError(ErrorCode.MEMORY_GONE, "memory deleted")
            return item

    def invalidate_working(
        self,
        tx: SQLiteTransaction,
        ctx: TrustedContext,
        item: MemorySnapshot,
        *,
        deleting: bool = False,
    ) -> None:
        if item.kind == MemoryKind.WORKING:
            for key, _ in tx.rows("remember_current"):
                derived = self.current(tx, key)
                if (
                    derived.kind != MemoryKind.WORKING
                    and (
                        derived.status != MemoryStatus.DELETED
                        if deleting
                        else derived.status in {MemoryStatus.ACTIVE, MemoryStatus.ARCHIVED}
                    )
                    and set(s.source_id for s in derived.sources)
                    & set(s.source_id for s in item.sources)
                ):
                    derived = self.change(
                        tx,
                        derived,
                        status=MemoryStatus.DELETED if deleting else MemoryStatus.SUPERSEDED,
                        projection_state=ProjectionState.STALE,
                    )
                    if deleting:
                        self.enqueue(tx, ctx, derived, "remember.cleanup")
                    self.emit(tx, ctx, derived, "deleted" if deleting else "projection_stale")
        # Keep the legacy basic profile compatible; the pipeline owns independent sources.
        if not getattr(self, "independent_working_sources", False):
            for source in item.sources:
                row = tx.read("remember_sources", source.source_id)
                if row and row["working_id"] and row["working_id"] != item.ref.memory_id:
                    working = self.current(tx, row["working_id"])
                    if working.status == MemoryStatus.ACTIVE:
                        working = self.change(
                            tx,
                            working,
                            status=MemoryStatus.DELETED if deleting else MemoryStatus.SUPERSEDED,
                            projection_state=ProjectionState.STALE,
                        )
                        if deleting:
                            self.enqueue(tx, ctx, working, "remember.cleanup")
                        self.emit(tx, ctx, working, "deleted" if deleting else "archived")

    def correct(
        self, ctx: TrustedContext, memory_id: str, request: CorrectionRequest
    ) -> RememberReceipt:
        if not request.content.strip() or len(request.content.encode()) > getattr(
            self, "max_input_bytes", 65536
        ):
            raise FoundationError(ErrorCode.INVALID_ARGUMENT, "correction exceeds input limits")
        with self.uow.transaction() as tx:
            item = self.current(tx, memory_id)
            self.identity.authorize(tx, ctx, Permission.CORRECT, memory_ref(item.ref))
            key, previous = self.replay(tx, ctx, "correct_" + memory_id, request)
            if previous:
                return RememberReceipt.model_validate(previous["result"])
            if (
                item.status
                not in {MemoryStatus.ACTIVE, MemoryStatus.ARCHIVED, MemoryStatus.EXPIRED}
                or item.ref.version != request.expected_version
                or (
                    request.expected_object_revision is not None
                    and request.expected_object_revision != item.object_revision
                )
            ):
                tx.abort(ErrorCode.VERSION_CONFLICT, "memory deleted or version changed")
            self.invalidate_working(tx, ctx, item)
            old = self.change(
                tx, item, status=MemoryStatus.SUPERSEDED, projection_state=ProjectionState.STALE
            )
            self.emit(tx, ctx, old, "projection_stale")
            source = self.new_source(
                tx,
                fingerprint([key, "correction_source"]),
                item.ref.scope,
                request.content,
                item.ref.memory_id if item.kind == MemoryKind.WORKING else None,
            )
            body = (
                self.working_correction_body(tx, ctx, item, source, request)
                if item.kind == MemoryKind.WORKING
                else request.content
            )
            updated = MemorySnapshot.model_validate(
                {
                    **item.model_dump(),
                    "ref": {**item.ref.model_dump(), "version": item.ref.version + 1},
                    "revision": 1,
                    "object_revision": old.object_revision + 1,
                    "content": body,
                    "content_hash": text_hash(body),
                    "sources": (source,),
                    "status": MemoryStatus.ACTIVE,
                    "projection_state": ProjectionState.PENDING,
                    "model_space": None,
                    "supersedes": item.ref,
                    "created_at": self.identity.clock(),
                    "expires_at": None if item.status == MemoryStatus.EXPIRED else item.expires_at,
                }
            )
            tx.write(
                "remember_source_input", source.source_id, request.source.model_dump(mode="json")
            )
            self.put(tx, updated)
            task_id = self.enqueue(
                tx,
                ctx,
                updated,
                self.working_task_kind(tx, updated)
                if item.kind == MemoryKind.WORKING
                else "remember.project",
            )
            task_ids = [task_id]
            if item.kind == MemoryKind.WORKING and self.projection_buildable(tx, updated):
                task_ids.append(self.enqueue(tx, ctx, updated, "remember.project"))
            self.emit(tx, ctx, updated, "corrected")
            result = RememberReceipt(
                operation_id=ctx.operation_id,
                saved=True,
                source=source,
                memories=(updated.ref,),
                task_ids=tuple(task_ids),
                phase="processing",
            )
            self.remember_result(tx, key, request, result)
            tx.write(
                "remember_audit",
                key,
                {
                    "action": "correct",
                    "reason": request.reason,
                    "memory": updated.ref.model_dump(mode="json"),
                    "principal": ctx.principal.principal_id,
                },
            )
            return result

    def working_correction_body(
        self,
        tx: SQLiteTransaction,
        ctx: TrustedContext,
        item: MemorySnapshot,
        source: SourceRef,
        request: CorrectionRequest,
    ) -> str:
        return request.content

    def working_task_kind(self, tx: SQLiteTransaction, item: MemorySnapshot) -> str:
        return "remember.extract"

    def projection_buildable(self, tx: SQLiteTransaction, item: MemorySnapshot) -> bool:
        return True

    def reindex(self, ctx: TrustedContext, memory_id: str) -> str:
        """Queue the current body only; reuse unresolved work and never re-extract facts."""
        with self.uow.transaction() as tx:
            item = self.current(tx, memory_id)
            self.identity.authorize(tx, ctx, Permission.WRITE, memory_ref(item.ref))
            request = {"memory": item.ref.model_dump(mode="json"), "model_space": self.model_space}
            key = request_key(ctx, "reindex_" + memory_id)
            previous = tx.read("remember_operations", key)
            if previous and previous["signature"] != fingerprint(request):
                tx.abort(ErrorCode.IDEMPOTENCY_CONFLICT, "reindex target changed")
            if previous:
                return str(previous["result"]["task_id"])
            if self.final_guard(tx, ctx, (item.ref,), "recall").items[0].decision != "allowed":
                tx.abort(ErrorCode.MEMORY_GONE, "inactive memory cannot be indexed")
            if not self.projection_buildable(tx, item):
                tx.abort(
                    ErrorCode.REQUEST_IN_PROGRESS,
                    "Working summary is not ready; recover it with reprocess before reindex",
                )
            kind = "remember.project"
            task_id = tx.read("remember_latest_task", fingerprint([memory_id, kind]))
            row = tx.read("tasks", task_id) if task_id else None
            original = tx.get(RecordRef.model_validate(row["record"]["input_ref"])) if row else None
            reuse = bool(
                row
                and original
                and original["ref"] == item.ref.model_dump(mode="json")
                and (
                    row["record"]["effect_status"] == "unknown"
                    or row["record"]["state"]
                    not in {"succeeded", "failed", "cancelled", "attention_required"}
                    or item.projection_state == ProjectionState.READY
                    and kind == "remember.project"
                )
            )
            if not reuse:
                item = self.change(
                    tx, item, projection_state=ProjectionState.PENDING, model_space=None
                )
                task_id = self.enqueue(tx, ctx, item, kind)
            tx.write(
                "remember_operations",
                key,
                {"signature": fingerprint(request), "result": {"task_id": task_id}},
            )
            return str(task_id)

    def lifecycle(
        self, ctx: TrustedContext, memory_id: str, request: LifecycleRequest
    ) -> MemorySnapshot:
        with self.uow.transaction() as tx:
            item = self.current(tx, memory_id)
            self.identity.authorize(tx, ctx, Permission.WRITE, memory_ref(item.ref))
            key, previous = self.replay(tx, ctx, "lifecycle_" + memory_id, request)
            if previous:
                return MemorySnapshot.model_validate(previous["result"])
            if item.ref.version != request.expected_version or item.status not in {
                MemoryStatus.ACTIVE,
                MemoryStatus.ARCHIVED,
            }:
                tx.abort(ErrorCode.VERSION_CONFLICT, "cannot change this lifecycle")
            if (
                request.expected_object_revision is not None
                and item.object_revision != request.expected_object_revision
            ):
                tx.abort(ErrorCode.VERSION_CONFLICT, "object revision changed")
            if item.status.value == request.target:
                self.remember_result(tx, key, request, item)
                return item
            if request.target == "active" and (
                item.expires_at is not None
                and item.expires_at <= self.identity.clock()
                or any(
                    not (tx.read("remember_sources", s.source_id) or {}).get("valid")
                    for s in item.sources
                )
            ):
                tx.abort(ErrorCode.MEMORY_GONE, "expired or revoked source cannot reactivate")
            updated = self.change(
                tx,
                item,
                status=request.target,
                projection_state=ProjectionState.PENDING
                if request.target == "active"
                else ProjectionState.STALE,
            )
            if request.target == "archived" and not getattr(
                self, "independent_working_sources", False
            ):
                self.invalidate_working(tx, ctx, item)
            if request.target == "active" and hasattr(self, "retention"):
                self.retention.activated_in(tx, updated)
            if request.target == "active":
                if item.kind == MemoryKind.WORKING:
                    self.enqueue(tx, ctx, updated, self.working_task_kind(tx, updated))
                if self.projection_buildable(tx, updated):
                    self.enqueue(tx, ctx, updated, "remember.project")
            self.emit(tx, ctx, updated, "activated" if request.target == "active" else "archived")
            self.remember_result(tx, key, request, updated)
            tx.write(
                "remember_audit",
                key,
                {
                    "action": request.target,
                    "reason": request.reason,
                    "memory": updated.ref.model_dump(mode="json"),
                },
            )
            return updated

    def delete(self, ctx: TrustedContext, memory_id: str, request: DeleteRequest) -> DeleteReceipt:
        with self.uow.transaction() as tx:
            return self.delete_in(tx, ctx, memory_id, request)

    def delete_in(
        self, tx: SQLiteTransaction, ctx: TrustedContext, memory_id: str, request: DeleteRequest
    ) -> DeleteReceipt:
        """Shared authorized tombstone commit for explicit and enrolled deletion."""
        item = self.current(tx, memory_id)
        self.identity.authorize(tx, ctx, Permission.DELETE, memory_ref(item.ref))
        key, previous = self.replay(tx, ctx, "delete_" + memory_id, request)
        if previous:
            return DeleteReceipt.model_validate(previous["result"])
        if item.object_revision != request.expected_revision:
            tx.abort(ErrorCode.VERSION_CONFLICT, "object revision changed")
        self.invalidate_working(tx, ctx, item, deleting=True)
        updated = self.change(
            tx, item, status=MemoryStatus.DELETED, projection_state=ProjectionState.STALE
        )
        task_id = self.enqueue(tx, ctx, updated, "remember.cleanup")
        self.emit(tx, ctx, updated, "deleted")
        result = DeleteReceipt(
            operation_id=ctx.operation_id,
            blocked=True,
            cleanup_state="pending",
            task_ids=(task_id,),
            remaining_targets=("vector_versions", "operate_cache", "retained_source_policy"),
        )
        self.remember_result(tx, key, request, result)
        return result

    def delete_source(
        self, ctx: TrustedContext, source_id: str, request: DeleteRequest
    ) -> DeleteReceipt:
        with self.uow.transaction() as tx:
            row = tx.read("remember_sources", source_id)
            if row is None:
                raise FoundationError(ErrorCode.NOT_FOUND, "source not found")
            scope = Scope.model_validate(row["scope"])
            self.identity.authorize(
                tx,
                ctx,
                Permission.DELETE,
                RecordRef(
                    owner=Flow.REMEMBER, object_type="source", object_id=source_id, scope=scope
                ),
            )
            key, previous = self.replay(tx, ctx, "source_delete_" + source_id, request)
            if previous:
                return DeleteReceipt.model_validate(previous["result"])
            if row["revision"] != request.expected_revision:
                tx.abort(ErrorCode.VERSION_CONFLICT, "source revision changed")
            tx.write(
                "remember_sources",
                source_id,
                {**row, "valid": False, "revision": row["revision"] + 1},
            )
            tasks = []
            for memory_id, _ in tx.rows("remember_current"):
                item = self.current(tx, memory_id)
                if (
                    any(s.source_id == source_id for s in item.sources)
                    and item.status != MemoryStatus.DELETED
                ):
                    updated = self.change(
                        tx,
                        item,
                        status=MemoryStatus.DELETED,
                        projection_state=ProjectionState.STALE,
                    )
                    tasks.append(self.enqueue(tx, ctx, updated, "remember.cleanup"))
                    self.emit(tx, ctx, updated, "deleted")
            result = DeleteReceipt(
                operation_id=ctx.operation_id,
                blocked=True,
                cleanup_state="pending",
                task_ids=tuple(tasks),
                remaining_targets=("derived_copies", "retained_source_policy"),
            )
            self.remember_result(tx, key, request, result)
            return result

    def final_guard(
        self,
        tx: Transaction,
        ctx: TrustedContext,
        refs: tuple[MemoryRef, ...],
        purpose: Literal["recall", "history", "actuate", "cleanup"],
    ) -> EligibilityBatch:
        sql = native(tx)
        self.identity.revalidate(tx, ctx)
        results = []
        for ref in refs:
            permission = Permission.HISTORY if purpose == "history" else Permission.READ
            allowed = self.identity.permits(tx, ctx, permission, memory_ref(ref))
            try:
                current = self.current(sql, ref.memory_id)
            except FoundationError:
                current = None
            exact = sql.get(memory_ref(ref, versioned=True))
            item = None if exact is None else self.decode(sql, exact)
            reason = "allowed"
            if not allowed:
                reason = "unauthorized"
            elif current is None or item is None:
                reason = "missing"
            elif current.status == MemoryStatus.DELETED and purpose != "cleanup":
                reason = "deleted"
            elif purpose not in {"history", "cleanup"} and (
                current.ref != ref or item.status != MemoryStatus.ACTIVE
            ):
                reason = "old_version_or_inactive"
            elif (
                purpose not in {"history", "cleanup"}
                and item.expires_at
                and item.expires_at <= self.identity.clock()
            ):
                reason = "expired"
            elif purpose != "cleanup" and any(
                not (sql.read("remember_sources", source.source_id) or {}).get("valid", False)
                for source in item.sources
            ):
                reason = "source_deleted"
            elif purpose == "actuate" and item.kind == MemoryKind.WORKING:
                reason = "working_not_scheduled"
            results.append(
                EligibilityResult(
                    ref=ref,
                    decision="allowed" if reason == "allowed" else "excluded",
                    reason=reason,
                    checked_revision=None if current is None else current.object_revision,
                )
            )
        return EligibilityBatch(items=tuple(results), authorization_epoch=ctx.principal.auth_epoch)

    def load(self, ctx: TrustedContext, refs: tuple[MemoryRef, ...]) -> MemoryReadBatch:
        with self.uow.transaction() as tx:
            eligibility = self.final_guard(tx, ctx, refs, "recall")
            items = tuple(
                self.decode(tx, tx.get(memory_ref(e.ref, versioned=True)))
                for e in eligibility.items
                if e.decision == "allowed"
            )
            return MemoryReadBatch(items=items, eligibility=eligibility, conflicts=())

    def working(
        self, ctx: TrustedContext, selection: ScopeSelector, page: PageRequest
    ) -> MemoryReadBatch:
        scope = select_scope(ctx, selection)
        with self.uow.transaction() as tx:
            self.identity.revalidate(tx, ctx)
            items = [self.current(tx, key) for key, _ in tx.rows("remember_current")]
            selected = [
                (m.ref.memory_id, m)
                for m in items
                if m.kind == MemoryKind.WORKING and matches(m.ref.scope, scope)
            ]
            rows, cursor = tx.page(selected, ["working", scope.model_dump(mode="json")], page)
            eligibility = self.final_guard(tx, ctx, tuple(m.ref for m in rows), "recall")
            allowed = {e.ref.memory_id for e in eligibility.items if e.decision == "allowed"}
            return MemoryReadBatch(
                items=tuple(m for m in rows if m.ref.memory_id in allowed),
                eligibility=eligibility,
                conflicts=(),
                next_cursor=cursor,
            )

    def history(self, ctx: TrustedContext, memory_id: str, page: PageRequest) -> MemoryReadBatch:
        with self.uow.transaction() as tx:
            item = self.current(tx, memory_id)
            self.identity.authorize(tx, ctx, Permission.HISTORY, memory_ref(item.ref))
            if item.status == MemoryStatus.DELETED:
                raise FoundationError(ErrorCode.MEMORY_GONE, "deleted history is unavailable")
            refs = [
                MemoryRef(scope=item.ref.scope, memory_id=memory_id, version=v)
                for v in range(1, item.ref.version + 1)
            ]
            values, cursor = tx.page(
                [(f"{r.version:012d}", r) for r in refs],
                ["history", memory_id, ctx.principal.model_dump(mode="json")],
                page,
            )
            eligibility = self.final_guard(tx, ctx, tuple(values), "history")
            return MemoryReadBatch(
                items=tuple(
                    self.decode(tx, tx.get(memory_ref(e.ref, versioned=True)))
                    for e in eligibility.items
                    if e.decision == "allowed"
                ),
                eligibility=eligibility,
                conflicts=(),
                next_cursor=cursor,
            )

    def source(self, ctx: TrustedContext, source_id: str) -> SourceRef:
        with self.uow.transaction() as tx:
            row = tx.read("remember_sources", source_id)
            if row is None:
                raise FoundationError(ErrorCode.NOT_FOUND, "source not found")
            self.identity.authorize(
                tx,
                ctx,
                Permission.READ,
                RecordRef(
                    owner=Flow.REMEMBER,
                    object_type="source",
                    object_id=source_id,
                    scope=Scope.model_validate(row["scope"]),
                ),
            )
            if not row["valid"]:
                raise FoundationError(ErrorCode.MEMORY_GONE, "source deleted")
            return SourceRef.model_validate(row["ref"])

    async def run(self, ctx: TrustedContext, task: TaskRecord) -> RunResult:
        from aether_agent_memory.remember.basic.projection import projection_target

        with self.uow.transaction() as tx:
            self.tasks.guard(tx, task)
            item = self.current(tx, task.subject.object_id)
            input_snapshot = self.decode(tx, tx.get(task.input_ref))
            valid = (
                self.final_guard(
                    tx,
                    ctx,
                    (input_snapshot.ref,),
                    "cleanup" if task.kind.endswith("cleanup") else "recall",
                )
                .items[0]
                .decision
                == "allowed"
            )
            if not valid:
                return RunResult(
                    outcome="obsolete",
                    effect_status=EffectStatus.NO_EFFECT,
                    reason="memory version no longer eligible",
                )
        if task.kind == "remember.extract":
            extracted = await self.extraction.extract(
                ctx,
                ExtractionRequest(
                    source=item.sources[0],
                    text=item.content,
                    existing=(),
                    policy_version="basic_candidates_v1",
                ),
            )
            if len(extracted.candidates) > 32 or any(
                c.sources != item.sources or c.evidence_status != "supported"
                for c in extracted.candidates
            ):
                raise FoundationError(
                    ErrorCode.CONTRACT_VIOLATION, "invalid candidate provenance or count"
                )
            with self.uow.transaction() as tx:
                self.tasks.guard(tx, task)
                if self.final_guard(tx, ctx, (item.ref,), "recall").items[0].decision != "allowed":
                    return RunResult(
                        outcome="obsolete",
                        effect_status=EffectStatus.NO_EFFECT,
                        reason="input invalidated while extracting",
                    )
                refs = []
                for index, candidate in enumerate(extracted.candidates):
                    fact = self.new_memory(
                        tx,
                        fingerprint([task.task_id, index]),
                        item.ref.scope,
                        candidate.text,
                        candidate.sources,
                        MemoryKind.EPISODIC,
                    )
                    refs.append(fact.ref.model_dump(mode="json"))
                    self.enqueue(tx, ctx, fact, "remember.project")
                    self.emit(tx, ctx, fact, "saved")
                return self.finish(
                    tx, ctx, task, {"memories": refs, "extractor": extracted.model_id}
                )
        if task.kind == "remember.cleanup":
            with self.uow.transaction() as tx:
                registered = [
                    ProjectionTarget.model_validate(row["target"])
                    for _, row in tx.rows("remember_projection_targets")
                    if row["target"]["memory"]["memory_id"] == item.ref.memory_id
                ]
            for target in registered:
                cleaned = await self.projections.delete(ctx, target, task.task_id)
                if cleaned.state != "absent" or cleaned.target != target:
                    return RunResult(
                        outcome="uncertain",
                        effect_status=EffectStatus.UNKNOWN,
                        operation_id=task.task_id,
                        reason="generation deletion not yet confirmed",
                    )
            for version in range(1, item.ref.version + 1):
                with self.uow.transaction() as tx:
                    raw = tx.get(
                        memory_ref(
                            MemoryRef(
                                scope=item.ref.scope, memory_id=item.ref.memory_id, version=version
                            ),
                            versioned=True,
                        )
                    )
                if raw:
                    old = self.decode(tx, raw)
                    cleaned = await self.projections.delete(
                        ctx,
                        projection_target(old.ref, old.content_hash, self.model_space),
                        task.task_id,
                    )
                    if cleaned.state != "absent":
                        return RunResult(
                            outcome="uncertain",
                            effect_status=EffectStatus.UNKNOWN,
                            operation_id=task.task_id,
                            reason="vector deletion not yet confirmed",
                        )
            with self.uow.transaction() as tx:
                return self.finish(
                    tx,
                    ctx,
                    task,
                    {"vector_cleanup": "completed", "source_retention": "pending_policy"},
                )
        target = self.basic_projection_target(item, task)
        embedded = await self.embedding.embed(
            ctx,
            EmbeddingRequest(
                operation_id=task.task_id,
                usage="passage",
                texts=(item.content,),
                model_space=self.model_space,
                deadline_at=ctx.deadline_at,
            ),
        )
        if (
            embedded.model_space != self.model_space
            or embedded.operation_id != task.task_id
            or embedded.usage != "passage"
            or len(embedded.items) != 1
            or embedded.items[0].input_hash != item.content_hash
        ):
            raise FoundationError(ErrorCode.CONTRACT_VIOLATION, "embedding input binding mismatch")
        with self.uow.transaction() as tx:
            self.tasks.guard(tx, task)
            tx.write(
                "remember_projection_targets",
                target.vector_id,
                {
                    "target": target.model_dump(mode="json"),
                    "task_id": task.task_id,
                    "dimensions": len(embedded.items[0].vector),
                    "cleanup": "pending",
                },
            )
        projected = await self.projections.project(
            ctx,
            ProjectionRequest(
                operation_id=task.task_id,
                target=target,
                vector=embedded.items[0].vector,
                deadline_at=ctx.deadline_at,
            ),
        )
        if projected.state != "verified" or projected.target != target:
            return RunResult(
                outcome="uncertain",
                effect_status=EffectStatus.UNKNOWN,
                operation_id=task.task_id,
                reason="projection not verified",
            )
        with self.uow.transaction() as tx:
            self.tasks.guard(tx, task)
            if self.final_guard(tx, ctx, (item.ref,), "recall").items[0].decision != "allowed":
                return RunResult(
                    outcome="obsolete",
                    effect_status=EffectStatus.CONFIRMED,
                    reason="old vector excluded by authoritative version",
                )
            current = self.current(tx, item.ref.memory_id)
            self.publish_basic_working_manifest(tx, item, target, task)
            updated = self.change(
                tx, current, projection_state=ProjectionState.READY, model_space=self.model_space
            )
            self.emit(tx, ctx, updated, "projection_ready")
            return self.finish(
                tx,
                ctx,
                task,
                {"memory": updated.ref.model_dump(mode="json"), "projection": "ready"},
            )

    def basic_projection_target(self, item: MemorySnapshot, task: TaskRecord) -> ProjectionTarget:
        from .projection import projection_target

        working = item.kind == MemoryKind.WORKING
        return projection_target(
            item.ref,
            item.content_hash,
            self.model_space,
            generation=task.task_id if working else None,
            body_hash=item.content_hash if working else None,
            memory_source="working" if working else "long_term",
        )

    def publish_basic_working_manifest(
        self,
        tx: SQLiteTransaction,
        item: MemorySnapshot,
        target: ProjectionTarget,
        task: TaskRecord,
    ) -> None:
        if item.kind != MemoryKind.WORKING:
            return
        row = tx.read("remember_projection_targets", target.vector_id)
        if not row or not row.get("dimensions"):
            tx.abort(
                ErrorCode.CONTRACT_VIOLATION, "verified Working projection lacks dimension evidence"
            )
        manifest = ProjectionManifest(
            memory=item.ref,
            generation=task.task_id,
            body_hash=item.content_hash,
            model_space=self.model_space,
            dimensions=row["dimensions"],
            chunker_version="whole_body_v1",
            embedding_tokenizer="provider_input_budget",
            expected_chunk_count=1,
            chunks=(
                ChunkDescriptor(
                    chunk_index=0,
                    vector_id=target.vector_id,
                    start_char=0,
                    end_char=len(item.content),
                    input_hash=item.content_hash,
                    verified=True,
                ),
            ),
            state="ready",
            task_id=task.task_id,
            published_at=self.identity.clock(),
            vector_location=ResourceLocation(
                kind="vector",
                provider_id="projection",
                provider_instance_id=self.model_space,
                namespace="remember_vectors",
                object_key=self.refkey(item.ref),
                generation=task.task_id,
                content_hash=item.content_hash,
            ),
        )
        tx.write("remember_manifests", self.refkey(item.ref), manifest.model_dump(mode="json"))

    def finish(
        self, tx: SQLiteTransaction, ctx: TrustedContext, task: TaskRecord, value: dict[str, Any]
    ) -> RunResult:
        result = RecordRef(
            owner=Flow.REMEMBER,
            object_type="processing_result",
            object_id=task.task_id,
            scope=task.subject.scope,
        )
        tx.put_if_revision(result, value, None)
        self.tasks.complete(tx, ctx, task, result)
        return RunResult(
            outcome="committed",
            effect_status=EffectStatus.CONFIRMED,
            result_ref=result,
            reason="Remember processing committed",
        )

    async def recover(self, ctx: TrustedContext, task: TaskRecord) -> RecoveryDecision:
        # Extraction commits all candidates/results atomically. Projection uses an immutable
        # vector ID: replay is an idempotent upsert, not a new external action.
        with self.uow.transaction() as tx:
            self.tasks.guard(tx, task)
            item = self.current(tx, task.subject.object_id)
            original = self.decode(tx, tx.get(task.input_ref))
            valid = (
                self.final_guard(
                    tx,
                    ctx,
                    (self.decode(tx, tx.get(task.input_ref)).ref,),
                    "cleanup" if task.kind.endswith("cleanup") else "recall",
                )
                .items[0]
                .decision
                == "allowed"
            )
        if not valid:
            effect = EffectStatus.NO_EFFECT
            if task.kind == "remember.project":
                observed = await self.projections.inspect(
                    ctx,
                    self.basic_projection_target(original, task),
                    task.task_id,
                )
                if observed.state not in {"absent", "verified"}:
                    return RecoveryDecision(
                        action=RecoveryAction.QUERY_ONLY,
                        effect_status=EffectStatus.UNKNOWN,
                        original_operation_id=task.task_id,
                        reason="invalidated version still has unresolved projection evidence",
                        evidence=(task.input_ref,),
                    )
                effect = (
                    EffectStatus.CONFIRMED
                    if observed.state == "verified"
                    else EffectStatus.NO_EFFECT
                )
            return RecoveryDecision(
                action=RecoveryAction.CANCEL,
                effect_status=effect,
                reason="old result cannot be committed",
                evidence=(task.input_ref,),
            )
        if task.kind == "remember.project":
            target = self.basic_projection_target(item, task)
            observed = await self.projections.inspect(ctx, target, task.task_id)
            if observed.state == "verified":
                with self.uow.transaction() as tx:
                    self.tasks.guard(tx, task)
                    if (
                        self.final_guard(tx, ctx, (item.ref,), "recall").items[0].decision
                        != "allowed"
                    ):
                        return RecoveryDecision(
                            action=RecoveryAction.CANCEL,
                            effect_status=EffectStatus.CONFIRMED,
                            reason="version invalidated",
                            evidence=(task.input_ref,),
                        )
                    self.publish_basic_working_manifest(tx, item, target, task)
                    updated = self.change(
                        tx,
                        self.current(tx, item.ref.memory_id),
                        projection_state=ProjectionState.READY,
                        model_space=self.model_space,
                    )
                    self.emit(tx, ctx, updated, "projection_ready")
                    self.finish(
                        tx,
                        ctx,
                        task,
                        {"memory": item.ref.model_dump(mode="json"), "projection": "ready"},
                    )
                return RecoveryDecision(
                    action=RecoveryAction.QUERY_ONLY,
                    effect_status=EffectStatus.CONFIRMED,
                    original_operation_id=task.task_id,
                    reason="exact projection verified",
                    evidence=(task.input_ref,),
                )
            if observed.state != "absent":
                return RecoveryDecision(
                    action=RecoveryAction.QUERY_ONLY,
                    effect_status=EffectStatus.UNKNOWN,
                    original_operation_id=task.task_id,
                    reason="projection still unknown",
                    evidence=(task.input_ref,),
                )
        return RecoveryDecision(
            action=RecoveryAction.RESUME,
            effect_status=EffectStatus.NO_EFFECT,
            reason="no committed batch; idempotent projection/cleanup uses original binding",
            evidence=(task.input_ref,),
        )
