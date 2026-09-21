"""Resolve Query text from admitted Recall state through current authorization."""

from aether_agent_memory.recall.admission import (
    RecallAdmission,
    RecallAdmissionService,
    RecallFilters,
    RecallInput,
)
from aether_agent_memory.recall.embedding.models import SemanticEmbeddingRequest
from aether_agent_memory.recall.embedding.service import (
    ResolvedEmbeddingInput,
    SemanticEmbeddingError,
)
from aether_agent_memory.runtime.contract_types import hash_json, hash_text, utcnow


class RecallQueryInputAdapter:
    """Query-only port; Passage requires a separate B-authorized fixed-fragment port."""

    def __init__(self, admission: RecallAdmissionService, *, caller_ref: str) -> None:
        self.admission, self.caller_ref = admission, caller_ref

    async def _resolve_authorized(self, request: SemanticEmbeddingRequest) -> RecallAdmission:
        if request.usage != "Query" or request.caller_ref != self.caller_ref:
            raise SemanticEmbeddingError("EMBEDDING_INPUT_INVALID")
        with self.admission.store.transaction() as tx:
            key = tx.get("recall-locator", request.tenant_id, request.caller_request_ref)
            saved = tx.get("recall-admission", request.tenant_id, key) if key else None
        if saved is None:
            raise SemanticEmbeddingError("EMBEDDING_INPUT_INVALID")
        current = RecallAdmission.model_validate_json(saved)
        original = current.request
        digest = hash_json(
            {
                "recall_id": original.recall_id,
                "request_fingerprint": original.request_fingerprint.model_dump(),
            }
        )
        if (
            original.tenant_id != request.tenant_id
            or original.recall_id != request.caller_request_ref
            or original.retrieval_mode == "working_only"
            or original.request_ref != request.input_ref
            or original.request_ref != request.input_binding_ref
            or hash_text(original.query) != request.source_hash
            or digest != request.input_binding_digest
            or original.retrieval_space_ref != request.model_binding.retrieval_space_ref
            or utcnow() >= original.deadline_at
        ):
            raise SemanticEmbeddingError("EMBEDDING_INPUT_INVALID")
        # Replay uses the original policy and selection, but checks current permissions.
        return await self.admission.admit(
            RecallInput(
                request_id=original.request_id,
                trace_id=original.trace_id,
                query=original.query,
                scope=original.scope,
                principal_ref=original.principal_ref,
                authorization_ref=request.authorization_ref,
                idempotency_key=original.idempotency_key,
                deadline_at=original.deadline_at,
                token_budget=original.token_budget,
                tokenizer_id=original.tokenizer_id,
                tokenizer_version=original.tokenizer_version,
                template_version=original.template_version,
                retrieval_constraints=RecallFilters(
                    memory_types=original.retrieval_constraints.memory_types,
                    occurred_after=original.retrieval_constraints.occurred_after,
                    occurred_before=original.retrieval_constraints.occurred_before,
                ),
            )
        )

    async def authorize(self, request: SemanticEmbeddingRequest) -> bool:
        await self._resolve_authorized(request)
        return True

    async def resolve(self, request: SemanticEmbeddingRequest) -> ResolvedEmbeddingInput:
        current = await self._resolve_authorized(request)
        return ResolvedEmbeddingInput(
            text=current.request.query,
            input_binding_digest=hash_json(
                {
                    "recall_id": current.request.recall_id,
                    "request_fingerprint": current.request.request_fingerprint.model_dump(),
                }
            ),
        )
