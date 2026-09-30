"""First executable Recall increment: admit, bind Query compute, prepare search.

Stops at RUNNING_VECTOR_SEARCH. It does not invent a ContextPack or source
completion evidence while B/P2 downstream adapters are still being integrated.
"""

from __future__ import annotations

from uuid import uuid4

from aether_agent_memory.p2.contracts import P2SearchInput
from aether_agent_memory.recall.admission import (
    RecallAdmission,
    RecallAdmissionService,
    RecallError,
    RecallInput,
)
from aether_agent_memory.recall.embedding.models import (
    EmbeddingModelBinding,
    SemanticEmbeddingRequest,
)
from aether_agent_memory.recall.embedding.service import (
    SemanticEmbeddingCapability,
    make_embedding_request,
)
from aether_agent_memory.recall.models import (
    EmbeddingCallBinding,
    QueryEmbeddingResult,
    RecallCheckpoint,
    RecallReadAttempt,
)
from aether_agent_memory.runtime.contract_types import (
    ContractModel,
    Identifier,
    Stage,
    hash_bytes,
    hash_json,
    hash_text,
    utcnow,
    vector_bytes,
)


class PreparedQuery(ContractModel):
    recall_id: Identifier
    query_result: QueryEmbeddingResult | None
    search_input: P2SearchInput | None


class RecallQueryService:
    def __init__(
        self,
        admission: RecallAdmissionService,
        embedding: SemanticEmbeddingCapability,
        model_binding: EmbeddingModelBinding,
        *,
        caller_ref: str,
        execution_policy_ref: str,
    ) -> None:
        self.admission, self.embedding, self.model_binding = admission, embedding, model_binding
        self.caller_ref, self.execution_policy_ref = caller_ref, execution_policy_ref

    def _checkpoint(
        self, admission: RecallAdmission, stage: Stage, output: ContractModel, attempt: int = 1
    ) -> RecallCheckpoint:
        request = admission.request
        return RecallCheckpoint(
            schema_version="recall-data-0.2",
            tenant_id=request.tenant_id,
            created_at=utcnow(),
            recall_id=request.recall_id,
            request_id=request.request_id,
            trace_id=request.trace_id,
            checkpoint_ref=uuid4().hex,
            stage=stage,
            input_digest=request.request_fingerprint,
            output_ref=uuid4().hex,
            output_digest=hash_json(output.model_dump(mode="json")),
            completed_at=utcnow(),
            source_schema_versions={"request": request.schema_version},
            policy_version=request.policy_version,
            attempt=attempt,
            sensitivity="restricted_payload",
        )

    async def prepare(self, raw: RecallInput) -> PreparedQuery:
        admitted = await self.admission.admit(raw)
        request = admitted.request
        memory_types = [
            kind
            for kind in request.retrieval_constraints.memory_types
            if ("working" if kind == "Working" else "long_term")
            in request.retrieval_constraints.allowed_sources
        ]
        tenant, recall_id = request.tenant_id, request.recall_id
        token = uuid4().hex
        store = self.admission.store
        with store.transaction() as tx:
            key = tx.get("recall-locator", tenant, recall_id)
            assert key is not None
            value = tx.get("recall-admission", tenant, key)
            assert value is not None
            current = RecallAdmission.model_validate_json(value)
            cached = tx.get("recall-prepared-query", tenant, recall_id)
            if cached is not None:
                if utcnow() >= current.request.deadline_at:
                    raise RecallError("DEADLINE_EXCEEDED")
                try:
                    prepared = PreparedQuery.model_validate_json(cached)
                except ValueError as exc:
                    raise RecallError("REPLAY_REVALIDATION_REQUIRED") from exc
                if (
                    prepared.search_input is None
                    or prepared.search_input.memory_types != memory_types
                ):
                    raise RecallError("REPLAY_REVALIDATION_REQUIRED")
                checkpoint_ref = current.execution.checkpoint_refs.get("RUNNING_QUERY_EMBEDDING")
                saved = tx.get("recall-checkpoint", tenant, checkpoint_ref or "")
                if saved is None:
                    raise RecallError("INVARIANT_VIOLATION")
                checkpoint = RecallCheckpoint.model_validate_json(saved)
                if checkpoint.output_digest != hash_json(prepared.model_dump(mode="json")):
                    raise RecallError("INVARIANT_VIOLATION")
                return prepared
            if utcnow() >= current.request.deadline_at:
                raise RecallError("DEADLINE_EXCEEDED")
            execution = current.execution
            if (
                execution.lease_token is not None
                and execution.lease_until is not None
                and execution.lease_until > utcnow()
            ):
                raise RecallError("ADMISSION_BUSY")
            if execution.state not in ("CREATED", "RUNNING_QUERY_EMBEDDING"):
                raise RecallError("INVARIANT_VIOLATION")
            if execution.state == "CREATED":
                checkpoint = self._checkpoint(current, "RUNNING_REQUEST_VALIDATION", request)
                tx.put("recall-output", tenant, checkpoint.output_ref, request.model_dump_json())
                tx.put(
                    "recall-checkpoint",
                    tenant,
                    checkpoint.checkpoint_ref,
                    checkpoint.model_dump_json(),
                )
                execution = execution.replaced(
                    checkpoint_refs={checkpoint.stage: checkpoint.checkpoint_ref}
                )
            execution = execution.replaced(
                state="RUNNING_QUERY_EMBEDDING",
                lease_owner=self.caller_ref,
                lease_token=token,
                lease_until=request.deadline_at,
                state_version=execution.state_version + 1,
            )
            semantic_request: SemanticEmbeddingRequest | None = None
            if request.retrieval_space_ref is not None:
                if request.retrieval_space_ref != self.model_binding.retrieval_space_ref:
                    raise RecallError("EMBEDDING_CONTRACT_MISMATCH")
                previous = tx.get("recall-query-binding", tenant, recall_id)
                if previous is not None:
                    semantic_request = SemanticEmbeddingRequest.model_validate_json(
                        previous
                    ).replaced(authorization_ref=raw.authorization_ref)
                else:
                    semantic_request = make_embedding_request(
                        tenant_id=tenant,
                        caller_ref=self.caller_ref,
                        caller_request_ref=recall_id,
                        trace_id=request.trace_id,
                        authorization_ref=raw.authorization_ref,
                        usage="Query",
                        input_ref=request.request_ref,
                        source_hash=hash_text(request.query),
                        input_binding_digest=hash_json(
                            {
                                "recall_id": recall_id,
                                "request_fingerprint": request.request_fingerprint.model_dump(),
                            }
                        ),
                        input_binding_ref=request.request_ref,
                        model_binding=self.model_binding,
                        deadline_at=request.deadline_at,
                        execution_policy_ref=self.execution_policy_ref,
                    )
                    tx.put(
                        "recall-query-binding",
                        tenant,
                        recall_id,
                        semantic_request.model_dump_json(),
                    )
                execution = execution.replaced(
                    query_embedding_call=EmbeddingCallBinding(
                        caller_ref=semantic_request.caller_ref,
                        caller_request_ref=recall_id,
                        embedding_request_ref=semantic_request.embedding_request_id,
                    )
                )
                attempts = execution.read_ledger.attempts
                if len(attempts) >= current.policy.remote_read_attempts:
                    raise RecallError("QUERY_EMBEDDING_FAILED")
                attempt = RecallReadAttempt(
                    attempt_id=uuid4().hex,
                    call_key=hash_json(
                        {
                            "tenant_id": tenant,
                            "recall_id": recall_id,
                            "stage": "RUNNING_QUERY_EMBEDDING",
                            "capability": "query_embedding_attach",
                            "logical_target": recall_id,
                            "input_digest": semantic_request.reuse_digest.model_dump(),
                        }
                    ),
                    stage="RUNNING_QUERY_EMBEDDING",
                    capability="query_embedding_attach",
                    logical_target=recall_id,
                    input_digest=semantic_request.reuse_digest,
                    attempt_no=len(attempts) + 1,
                    state="reserved",
                    reserved_bytes=0,
                    received_bytes=None,
                    charged_bytes=0,
                    reserved_at=utcnow(),
                    call_deadline_at=request.deadline_at,
                    closed_at=None,
                    output_ref=None,
                )
                execution = execution.replaced(
                    read_ledger=execution.read_ledger.replaced(attempts=[*attempts, attempt])
                )
            current = current.replaced(execution=execution)
            tx.put("recall-admission", tenant, key, current.model_dump_json())
        try:
            if semantic_request is None:
                output = PreparedQuery(recall_id=recall_id, query_result=None, search_input=None)
            else:
                result = await self.embedding.embed(semantic_request)
                vector = await self.embedding.vector_for(semantic_request, result)
                if (
                    result.tenant_id != tenant
                    or result.usage != "Query"
                    or result.model_binding != self.model_binding
                    or result.source_hash != semantic_request.source_hash
                    or result.input_binding_digest != semantic_request.input_binding_digest
                    or result.vector_hash
                    != hash_bytes(
                        vector_bytes(vector, self.model_binding.dimension, self.model_binding.dtype)
                    )
                ):
                    raise RecallError("EMBEDDING_CONTRACT_MISMATCH")
                query = QueryEmbeddingResult(
                    schema_version="recall-data-0.2",
                    tenant_id=tenant,
                    created_at=utcnow(),
                    recall_id=recall_id,
                    request_id=request.request_id,
                    trace_id=request.trace_id,
                    embedding_result_ref=uuid4().hex,
                    vector_ref=result.vector_ref,
                    usage="Query",
                    model_id=result.model_binding.model_id,
                    model_version=result.model_binding.model_version,
                    dimension=result.model_binding.dimension,
                    dtype=result.model_binding.dtype,
                    embedding_schema_version=result.model_binding.embedding_schema_version,
                    source_hash=result.source_hash,
                    retrieval_space_ref=result.model_binding.retrieval_space_ref,
                    external_contract_ref=result.model_binding.model_contract_ref,
                    source_evidence_ref=result.validation_evidence_ref,
                    validated_at=result.validated_at,
                )
                search = P2SearchInput(
                    query_vector=vector,
                    usage="Query",
                    model_binding=result.model_binding,
                    retrieval_space_ref=result.model_binding.retrieval_space_ref,
                    scope=request.scope,
                    memory_types=memory_types,
                    occurred_after=request.retrieval_constraints.occurred_after,
                    occurred_before=request.retrieval_constraints.occurred_before,
                    top_k=current.policy.vector_top_k,
                )
                output = PreparedQuery(recall_id=recall_id, query_result=query, search_input=search)
            with store.transaction() as tx:
                latest_json = tx.get("recall-admission", tenant, key)
                assert latest_json is not None
                latest = RecallAdmission.model_validate_json(latest_json)
                if (
                    latest.execution.lease_token != token
                    or latest.execution.state_version != current.execution.state_version
                ):
                    raise RecallError("INVARIANT_VIOLATION")
                if utcnow() >= latest.request.deadline_at:
                    raise RecallError("DEADLINE_EXCEEDED")
                checkpoint = self._checkpoint(latest, "RUNNING_QUERY_EMBEDDING", output)
                ledger = latest.execution.read_ledger
                if semantic_request is not None:
                    ledger = ledger.replaced(
                        attempts=[
                            *ledger.attempts[:-1],
                            ledger.attempts[-1].replaced(
                                state="settled",
                                received_bytes=0,
                                closed_at=utcnow(),
                                output_ref=checkpoint.output_ref,
                            ),
                        ]
                    )
                execution = latest.execution.replaced(
                    state="RUNNING_VECTOR_SEARCH",
                    checkpoint_refs={
                        **latest.execution.checkpoint_refs,
                        checkpoint.stage: checkpoint.checkpoint_ref,
                    },
                    read_ledger=ledger,
                    lease_owner=None,
                    lease_until=None,
                    lease_token=None,
                    state_version=latest.execution.state_version + 1,
                )
                tx.put("recall-output", tenant, checkpoint.output_ref, output.model_dump_json())
                tx.put(
                    "recall-checkpoint",
                    tenant,
                    checkpoint.checkpoint_ref,
                    checkpoint.model_dump_json(),
                )
                tx.put("recall-prepared-query", tenant, recall_id, output.model_dump_json())
                tx.put(
                    "recall-admission",
                    tenant,
                    key,
                    latest.replaced(execution=execution).model_dump_json(),
                )
            return output
        finally:
            # Release ownership without resetting inference budget or read attempts.
            with store.transaction() as tx:
                value = tx.get("recall-admission", tenant, key)
                if value is not None:
                    latest = RecallAdmission.model_validate_json(value)
                    if latest.execution.lease_token == token:
                        ledger = latest.execution.read_ledger
                        attempts = [
                            a.replaced(
                                state="uncertain",
                                charged_bytes=a.reserved_bytes,
                                closed_at=utcnow(),
                            )
                            if a.state == "reserved"
                            else a
                            for a in ledger.attempts
                        ]
                        ledger = ledger.replaced(
                            attempts=attempts,
                            bytes_reserved=0,
                            bytes_charged=sum(a.charged_bytes for a in attempts),
                        )
                        execution = latest.execution.replaced(
                            lease_owner=None,
                            lease_until=None,
                            lease_token=None,
                            read_ledger=ledger,
                            state_version=latest.execution.state_version + 1,
                        )
                        tx.put(
                            "recall-admission",
                            tenant,
                            key,
                            latest.replaced(execution=execution).model_dump_json(),
                        )
