"""Authenticated scope-union routing and durable first-batch Recall admission.

Admission is not a successful ContextPack. Downstream stages are separate ports;
this module never returns empty content to disguise an unavailable dependency.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import Literal, Protocol, Self
from uuid import uuid4

from pydantic import Field, model_validator

from aether_agent_memory.memory.recall.models import (
    RecallExecution,
    RecallFinalization,
    RecallReadLedger,
    RecallRequest,
    RecallRequestIndex,
    RecallRetention,
    SourceSelection,
)
from aether_agent_memory.runtime.capability_store import AtomicRecordStore
from aether_agent_memory.runtime.contract_types import (
    Boolean,
    ContractModel,
    Identifier,
    LogicalSource,
    MemoryLabel,
    PositiveInt,
    ReasonCode,
    Scope,
    Text,
    Timestamp,
    UInt,
    hash_json,
    utcnow,
)
from aether_agent_memory.runtime.recall_values import RetrievalConstraints, SourceCoverage


class RecallError(RuntimeError):
    def __init__(self, code: ReasonCode) -> None:
        self.code = code
        super().__init__(code)


class RecallFilters(ContractModel):
    memory_types: list[MemoryLabel] | None = None
    occurred_after: Timestamp | None = None
    occurred_before: Timestamp | None = None

    @model_validator(mode="after")
    def valid_range(self) -> Self:
        if self.memory_types == []:
            raise ValueError("memory_types cannot be empty")
        if (
            self.occurred_after is not None
            and self.occurred_before is not None
            and self.occurred_after > self.occurred_before
        ):
            raise ValueError("reversed business time range")
        return self


class RecallInput(ContractModel):
    """New in-process ingress, not a replacement for frozen Northbound v1."""

    request_id: Identifier
    trace_id: Identifier
    query: Text
    scope: Scope
    principal_ref: Identifier
    authorization_ref: Identifier
    idempotency_key: Identifier
    deadline_at: Timestamp
    token_budget: PositiveInt
    tokenizer_id: Identifier
    tokenizer_version: Identifier
    template_version: Identifier
    retrieval_constraints: RecallFilters | None = None


class RecallAuthorization(ContractModel):
    """An authorization adapter's verified snapshot; never accepted from HTTP input."""

    scope: Scope
    principal_ref: Identifier
    evidence_ref: Identifier
    scope_valid: Boolean | None
    working_read: Boolean | None
    long_term_read: Boolean | None
    valid_until: Timestamp


class RecallAuthorizationPort(Protocol):
    async def authorize(self, request: RecallInput) -> RecallAuthorization: ...


class RecallPolicy(ContractModel):
    policy_version: Identifier = "design-v0.1-source-union-v1"
    max_query_chars: int = Field(default=16000, strict=True, ge=1, le=64000)
    request_timeout_ms: int = Field(default=2000, strict=True, ge=100, le=10000)
    max_context_tokens: int = Field(default=32768, strict=True, ge=1, le=131072)
    working_limit: int = Field(default=32, strict=True, ge=1, le=256)
    vector_top_k: int = Field(default=64, strict=True, ge=1, le=512)
    max_candidates_total: int = Field(default=96, strict=True, ge=1, le=768)
    max_inflight_requests: int = Field(default=64, strict=True, ge=1, le=4096)
    max_queued_requests: int = Field(default=128, strict=True, ge=0, le=8192)
    outbox_max_events: int = Field(default=100000, strict=True, ge=100, le=1000000)
    finalization_reserve_ms: int = Field(default=100, strict=True, ge=10, le=500)
    finalization_recovery_hours: int = Field(default=24, strict=True, ge=1, le=168)
    max_loaded_bytes_per_request: int = Field(default=2097152, strict=True, ge=65536, le=33554432)
    max_content_fragment_bytes: int = Field(default=65536, strict=True, ge=4096, le=4194304)
    remote_read_attempts: int = Field(default=2, strict=True, ge=1, le=3)
    retrieval_space_ref: Identifier | None = None
    tokenizer_id: Identifier
    tokenizer_version: Identifier
    template_version: Identifier

    @model_validator(mode="after")
    def consistent(self) -> Self:
        if self.request_timeout_ms <= self.finalization_reserve_ms:
            raise ValueError("request deadline must leave finalization time")
        if self.max_candidates_total < self.working_limit + self.vector_top_k:
            raise ValueError("candidate capacity must cover both source limits")
        if self.outbox_max_events < 6 * self.max_candidates_total + 8:
            raise ValueError("outbox capacity must cover one full execution")
        if self.max_loaded_bytes_per_request < self.max_content_fragment_bytes:
            raise ValueError("total byte budget must cover one complete fragment")
        return self


class RecallAdmission(ContractModel):
    request: RecallRequest
    index: RecallRequestIndex
    execution: RecallExecution
    policy: RecallPolicy
    reserved_events: UInt


class RecallAdmissionService:
    def __init__(
        self, store: AtomicRecordStore, authorization: RecallAuthorizationPort, policy: RecallPolicy
    ) -> None:
        self.store, self.authorization, self.policy = store, authorization, policy

    async def admit(self, request: RecallInput) -> RecallAdmission:
        # Revalidate nested/mutable containers before crossing the trust boundary.
        request = RecallInput.model_validate_json(request.model_dump_json())
        request = request.replaced(
            deadline_at=min(
                request.deadline_at,
                utcnow() + timedelta(milliseconds=self.policy.request_timeout_ms),
            )
        )
        remaining = (request.deadline_at - utcnow()).total_seconds()
        if remaining <= 0:
            raise RecallError("DEADLINE_EXCEEDED")
        try:
            async with asyncio.timeout(remaining):
                auth = await self.authorization.authorize(request)
        except TimeoutError as exc:
            raise RecallError("AUTHORITY_UNAVAILABLE") from exc
        except RecallError:
            raise
        except Exception as exc:
            raise RecallError("AUTHORITY_UNAVAILABLE") from exc
        self._check_authorization(request, auth)
        scope_digest = hash_json(request.scope.model_dump(mode="json"))
        key = hash_json(
            [request.principal_ref, scope_digest.model_dump(), request.idempotency_key]
        ).value
        with self.store.transaction() as tx:
            now = utcnow()
            if now >= request.deadline_at or now >= auth.valid_until:
                raise RecallError("DEADLINE_EXCEEDED")
            old = tx.get("recall-admission", request.scope.tenant_id, key)
            if old is not None:
                admission = RecallAdmission.model_validate_json(old)
                self._check_replay(request, auth, admission)
                return admission
            normalized = self._normalize(request, auth, self.policy)
            active = [
                RecallAdmission.model_validate_json(v) for _, _, v in tx.scan("recall-admission")
            ]
            active = [a for a in active if a.execution.completed_at is None]
            reserve = 6 * self.policy.max_candidates_total + 8
            if (
                len(active) >= self.policy.max_inflight_requests + self.policy.max_queued_requests
                or sum(a.reserved_events for a in active) + reserve > self.policy.outbox_max_events
            ):
                raise RecallError("ADMISSION_BUSY")
            admission = self._initial(normalized, self.policy, reserve)
            tx.put("recall-admission", request.scope.tenant_id, key, admission.model_dump_json())
            tx.put("recall-locator", request.scope.tenant_id, normalized.recall_id, key)
            return admission

    @staticmethod
    def _check_authorization(request: RecallInput, auth: RecallAuthorization) -> None:
        current = bool(request.scope.session_id or request.scope.task_id)
        if (
            auth.scope_valid is None
            or auth.long_term_read is None
            or (current and auth.working_read is None)
        ):
            raise RecallError("AUTHORITY_UNAVAILABLE")
        if (
            auth.scope_valid is not True
            or auth.scope != request.scope
            or auth.principal_ref != request.principal_ref
        ):
            raise RecallError("SCOPE_DENIED")
        if utcnow() >= auth.valid_until:
            raise RecallError("AUTHORITY_UNAVAILABLE")

    @staticmethod
    def _filters(request: RecallInput) -> RetrievalConstraints:
        raw = request.retrieval_constraints or RecallFilters()
        order: tuple[MemoryLabel, ...] = ("Working", "Episodic", "Semantic")
        types = [t for t in order if raw.memory_types is None or t in raw.memory_types]
        return RetrievalConstraints(
            memory_types=types,
            occurred_after=raw.occurred_after,
            occurred_before=raw.occurred_before,
            allowed_sources=[],
        )

    @classmethod
    def _normalize(
        cls,
        raw: RecallInput,
        auth: RecallAuthorization,
        policy: RecallPolicy,
        old: RecallRequest | None = None,
    ) -> RecallRequest:
        if not raw.query.strip() or len(raw.query) > policy.max_query_chars:
            raise RecallError("REQUEST_INVALID")
        if raw.token_budget > policy.max_context_tokens:
            raise RecallError("REQUEST_INVALID")
        for field in ("tokenizer_id", "tokenizer_version", "template_version"):
            if getattr(raw, field) != getattr(policy, field):
                raise RecallError("REQUEST_INVALID")
        filters = cls._filters(raw)
        if old is None:
            current = bool(raw.scope.session_id or raw.scope.task_id)
            working = (
                "no_current_scope"
                if not current
                else "not_authorized"
                if not auth.working_read
                else "type_filtered"
                if "Working" not in filters.memory_types
                else "eligible"
            )
            long_term = (
                "not_authorized"
                if not auth.long_term_read
                else "type_filtered"
                if not {"Episodic", "Semantic"}.intersection(filters.memory_types)
                else "eligible"
            )
            selection = SourceSelection(
                strategy="scope_union_v1",
                working_eligibility=working,
                long_term_eligibility=long_term,
            )
        else:
            selection = old.source_selection
        sources: list[LogicalSource] = []
        if selection.working_eligibility == "eligible":
            sources.append("working")
        if selection.long_term_eligibility == "eligible":
            sources.append("long_term")
        if not sources:
            readable = (
                bool(raw.scope.session_id or raw.scope.task_id) and auth.working_read
            ) or auth.long_term_read
            raise RecallError("REQUEST_INVALID" if readable else "SCOPE_DENIED")
        mode: Literal["combined", "working_only", "long_term_only"] = (
            "combined"
            if len(sources) == 2
            else "working_only"
            if sources[0] == "working"
            else "long_term_only"
        )
        space = (
            (old.retrieval_space_ref if old else policy.retrieval_space_ref)
            if "long_term" in sources
            else None
        )
        if "long_term" in sources and space is None:
            raise RecallError("EMBEDDING_CONTRACT_MISMATCH")
        now = utcnow()
        values = dict(
            query=raw.query,
            scope=raw.scope,
            retrieval_mode=mode,
            source_selection=selection,
            retrieval_constraints=filters.replaced(allowed_sources=sources),
            token_budget=raw.token_budget,
            tokenizer_id=raw.tokenizer_id,
            tokenizer_version=raw.tokenizer_version,
            template_version=raw.template_version,
            retrieval_space_ref=space,
            policy_version=policy.policy_version,
        )
        semantic = {
            k: v.model_dump(mode="json") if isinstance(v, ContractModel) else v
            for k, v in values.items()
        }
        return RecallRequest(
            schema_version="recall-request-0.2",
            tenant_id=raw.scope.tenant_id,
            created_at=old.created_at if old else now,
            recall_id=old.recall_id if old else uuid4().hex,
            request_id=old.request_id if old else raw.request_id,
            trace_id=old.trace_id if old else raw.trace_id,
            request_ref=old.request_ref if old else uuid4().hex,
            principal_ref=raw.principal_ref,
            authorization_ref=old.authorization_ref if old else auth.evidence_ref,
            idempotency_key=raw.idempotency_key,
            request_fingerprint=hash_json(semantic),
            deadline_at=old.deadline_at
            if old
            else min(raw.deadline_at, now + timedelta(milliseconds=policy.request_timeout_ms)),
            **values,
        )

    @classmethod
    def _check_replay(
        cls, raw: RecallInput, auth: RecallAuthorization, old: RecallAdmission
    ) -> None:
        sources = old.request.retrieval_constraints.allowed_sources
        if ("working" in sources and not auth.working_read) or (
            "long_term" in sources and not auth.long_term_read
        ):
            raise RecallError("SCOPE_DENIED")
        normalized = cls._normalize(raw, auth, old.policy, old.request)
        if normalized.request_fingerprint != old.request.request_fingerprint:
            raise RecallError("IDEMPOTENCY_CONFLICT")

    @staticmethod
    def _initial(request: RecallRequest, policy: RecallPolicy, reserve: int) -> RecallAdmission:
        header = dict(
            schema_version="recall-data-0.2",
            tenant_id=request.tenant_id,
            created_at=request.created_at,
        )
        recovery = request.created_at + timedelta(hours=policy.finalization_recovery_hours)
        sources = request.retrieval_constraints.allowed_sources
        execution = RecallExecution(
            **header,
            recall_id=request.recall_id,
            request_id=request.request_id,
            trace_id=request.trace_id,
            state_version=0,
            state="CREATED",
            request_ref=request.request_ref,
            policy_version=request.policy_version,
            execution_deadline_at=request.deadline_at,
            checkpoint_refs={},
            lease_owner=None,
            lease_until=None,
            lease_token=None,
            source_coverage=SourceCoverage(
                working="unavailable" if "working" in sources else "not_requested",
                long_term="unavailable" if "long_term" in sources else "not_requested",
            ),
            excluded_candidates=[],
            degradation_reasons=[],
            confirmed_empty=False,
            fatal_reason=None,
            result_ref=None,
            result_digest=None,
            final_validation_at=None,
            completed_at=None,
            last_event_sequence=0,
            source_result_refs={},
            query_embedding_call=None,
            read_ledger=RecallReadLedger(
                attempts=[], bytes_charged=0, bytes_reserved=0, admission_cursors={}
            ),
            final_validation=None,
            finalization=RecallFinalization(
                commit_id=uuid4().hex,
                generation=0,
                phase="idle",
                draft_ref=None,
                draft_digest=None,
                proposed_state=None,
                publish_before=None,
                recovery_deadline_at=recovery,
                attempts_reserved=0,
                last_call=None,
                recovery_not_before=None,
                commit_evidence_ref=None,
            ),
            retention=RecallRetention(
                payload_retain_until=None,
                metadata_retain_until=recovery,
                payload_state="retained",
                payload_cleared_at=None,
            ),
        )
        index = RecallRequestIndex(
            **header,
            principal_ref=request.principal_ref,
            scope_digest=hash_json(request.scope.model_dump(mode="json")),
            idempotency_key=request.idempotency_key,
            request_fingerprint=request.request_fingerprint,
            recall_id=request.recall_id,
            request_ref=request.request_ref,
        )
        return RecallAdmission(
            request=request,
            index=index,
            execution=execution,
            policy=policy,
            reserved_events=reserve,
        )
