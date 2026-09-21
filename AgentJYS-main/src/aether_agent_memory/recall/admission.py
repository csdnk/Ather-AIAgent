"""Authenticated scope-union routing and durable first-batch Recall admission.

Admission is not a successful ContextPack. Downstream stages are separate ports;
this module never returns empty content to disguise an unavailable dependency.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import Literal, Protocol, Self, get_args
from uuid import uuid4

from pydantic import Field, ValidationError, model_validator

from aether_agent_memory.recall.models import (
    RecallExecution,
    RecallFinalization,
    RecallReadLedger,
    RecallRequest,
    RecallRequestIndex,
    RecallRetention,
)
from aether_agent_memory.recall.ports import (
    RecallAdmissionPort,
    RecallAdmissionUnconfirmedError,
    RecallExecutionStorePort,
    RecallWriteUnknownError,
)
from aether_agent_memory.recall.routing import scope_union_v1
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
    TerminalState,
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
    tokenizer_id: Identifier | None = None
    tokenizer_version: Identifier | None = None
    template_version: Identifier | None = None
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

    @model_validator(mode="after")
    def consistent_binding(self) -> Self:
        request, index, execution = self.request, self.index, self.execution
        if (
            index.tenant_id != request.tenant_id
            or execution.tenant_id != request.tenant_id
            or index.recall_id != request.recall_id
            or execution.recall_id != request.recall_id
            or index.request_ref != request.request_ref
            or execution.request_ref != request.request_ref
            or index.principal_ref != request.principal_ref
            or index.idempotency_key != request.idempotency_key
            or index.scope_digest != hash_json(request.scope.model_dump(mode="json"))
            or index.request_fingerprint != request.request_fingerprint
            or execution.execution_deadline_at != request.deadline_at
            or execution.policy_version != request.policy_version
            or self.policy.policy_version != request.policy_version
        ):
            raise ValueError("request, index and execution must share the original binding")
        return self


class RecallAdmissionService:
    def __init__(
        self,
        store: AtomicRecordStore | None,
        authorization: RecallAuthorizationPort,
        policy: RecallPolicy,
        *,
        execution_store: RecallExecutionStorePort | None = None,
        admission_gate: RecallAdmissionPort | None = None,
    ) -> None:
        from aether_agent_memory.recall.store import (
            BoundedRecallAdmission,
            RecordRecallExecutionStore,
        )

        if execution_store is None:
            if store is None:
                raise ValueError("an RF execution store Port is required")
            execution_store = RecordRecallExecutionStore(store)
        # Kept solely for the existing experimental Query service compatibility.
        self._legacy_store = store
        self.execution_store = execution_store
        self.authorization, self.policy = authorization, policy
        self.admission_gate = admission_gate or BoundedRecallAdmission()
        self._unconfirmed_keys: set[tuple[str, str]] = set()

    @property
    def store(self) -> AtomicRecordStore:
        """Compatibility for existing Query/input adapters; new code uses the typed Port."""
        if self._legacy_store is None:
            raise RuntimeError("legacy Query service requires its atomic-record adapter")
        return self._legacy_store

    @staticmethod
    def request_key(request: RecallInput) -> str:
        scope_digest = hash_json(request.scope.model_dump(mode="json"))
        return hash_json(
            [request.principal_ref, scope_digest.model_dump(), request.idempotency_key]
        ).value

    async def admit(self, request: RecallInput) -> RecallAdmission:
        try:
            request = RecallInput.model_validate_json(request.model_dump_json())
        except ValidationError as exc:
            raise RecallError("REQUEST_INVALID") from exc
        key = self.request_key(request)
        tenant = request.scope.tenant_id
        original_request = request
        try:
            old = self.execution_store.find(tenant, key)
        except RecallWriteUnknownError as exc:
            raise RecallAdmissionUnconfirmedError("admission unresolved; retry same key") from exc
        if old is None and (tenant, key) in self._unconfirmed_keys:
            raise RecallAdmissionUnconfirmedError("admission unresolved; query original binding")
        if old is not None:
            self._unconfirmed_keys.discard((tenant, key))
        policy = old.policy if old else self.policy
        # All semantic defaults are bound by the server. Explicit legacy values
        # are checked below; omitted fields on a retry resolve to the OLD binding.
        request = request.replaced(
            tokenizer_id=request.tokenizer_id or policy.tokenizer_id,
            tokenizer_version=request.tokenizer_version or policy.tokenizer_version,
            template_version=request.template_version or policy.template_version,
            deadline_at=min(
                request.deadline_at,
                utcnow() + timedelta(milliseconds=policy.request_timeout_ms),
            ),
        )
        self._validate_input(request, policy, replay=old is not None)
        remaining = (request.deadline_at - utcnow()).total_seconds()
        if remaining <= 0:
            raise RecallError("DEADLINE_EXCEEDED")
        try:
            async with asyncio.timeout(remaining):
                auth = await self.authorization.authorize(request)
                auth = RecallAuthorization.model_validate_json(auth.model_dump_json())
        except TimeoutError as exc:
            raise RecallError("AUTHORITY_UNAVAILABLE") from exc
        except RecallError:
            raise
        except Exception as exc:
            raise RecallError("AUTHORITY_UNAVAILABLE") from exc
        self._check_authorization(request, auth)
        if utcnow() >= request.deadline_at:
            raise RecallError("DEADLINE_EXCEEDED")
        # Authorization may await another service while the execution advances.
        # Never return the pre-authorization snapshot or recreate a cleared key.
        try:
            latest = self.execution_store.find(tenant, key)
        except RecallWriteUnknownError as exc:
            raise RecallAdmissionUnconfirmedError("admission unresolved; retry same key") from exc
        if latest is not None:
            replay_input = original_request.replaced(
                tokenizer_id=original_request.tokenizer_id or latest.policy.tokenizer_id,
                tokenizer_version=original_request.tokenizer_version
                or latest.policy.tokenizer_version,
                template_version=original_request.template_version
                or latest.policy.template_version,
            )
            self._check_replay(replay_input, auth, latest)
            self._check_attachable(latest)
            return latest
        if old is not None:
            raise RecallAdmissionUnconfirmedError("previous binding unavailable; do not recreate")
        normalized = self._normalize(request, auth, policy)
        candidate = self._initial(normalized, policy, 6 * policy.max_candidates_total + 8)
        try:
            result = self.execution_store.admit(
                key, candidate, self.admission_gate, auth.valid_until
            )
        except RecallWriteUnknownError:
            # Never retry the write or allocate a new identity after an ambiguous
            # response. A plain absent read is NOT evidence of a rolled-back write.
            self._unconfirmed_keys.add((tenant, key))
            try:
                recovered = self.execution_store.find(tenant, key)
            except Exception as exc:
                raise RecallAdmissionUnconfirmedError(
                    "admission lookup unavailable; retry same key"
                ) from exc
            if recovered is None:
                raise RecallAdmissionUnconfirmedError(
                    "admission unresolved; retry same key"
                ) from None
            result = recovered
        self._unconfirmed_keys.discard((tenant, key))
        self._check_authorization(request, auth)
        if utcnow() >= request.deadline_at:
            raise RecallError("DEADLINE_EXCEEDED")
        # A concurrent first caller may have bound a different server policy.
        # Only explicit caller choices count as input; never compare new defaults.
        replay_input = original_request.replaced(
            tokenizer_id=original_request.tokenizer_id or result.policy.tokenizer_id,
            tokenizer_version=original_request.tokenizer_version or result.policy.tokenizer_version,
            template_version=original_request.template_version or result.policy.template_version,
        )
        self._check_replay(replay_input, auth, result)
        self._check_attachable(result)
        return result

    @staticmethod
    def _check_attachable(admission: RecallAdmission) -> None:
        if (
            admission.execution.state in get_args(TerminalState)
            or admission.execution.retention.payload_state == "cleared"
        ):
            # A later explicit replay capability must check current B facts.
            # Batch one never returns a saved body merely because admission exists.
            raise RecallError("REPLAY_REVALIDATION_REQUIRED")
        if utcnow() >= admission.execution.execution_deadline_at:
            raise RecallError("DEADLINE_EXCEEDED")

    @staticmethod
    def _validate_input(raw: RecallInput, policy: RecallPolicy, *, replay: bool = False) -> None:
        if not raw.query.strip() or len(raw.query) > policy.max_query_chars:
            raise RecallError("REQUEST_INVALID")
        if raw.token_budget > policy.max_context_tokens:
            raise RecallError("REQUEST_INVALID")
        for field in ("tokenizer_id", "tokenizer_version", "template_version"):
            if getattr(raw, field) != getattr(policy, field):
                raise RecallError("IDEMPOTENCY_CONFLICT" if replay else "REQUEST_INVALID")

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
        cls._validate_input(raw, policy, replay=old is not None)
        filters = cls._filters(raw)
        selection = (
            old.source_selection
            if old
            else scope_union_v1(
                raw.scope,
                filters.memory_types,
                working_read=auth.working_read is True,
                long_term_read=auth.long_term_read is True,
            )
        )
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
            tokenizer_id=policy.tokenizer_id,
            tokenizer_version=policy.tokenizer_version,
            template_version=policy.template_version,
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
