"""Existing native CPU inference adapted to the current P3 EmbeddingPort.

RF remains the only task/recovery authority. This adapter calls the native
backend directly; it does not start the older SemanticEmbeddingService queue.
"""

from __future__ import annotations

import asyncio
import math
import secrets
from datetime import datetime
from pathlib import Path
from typing import Any

from aether_agent_memory.recall.contracts.models import (
    EmbeddingItem,
    EmbeddingRequest,
    EmbeddingResult,
)
from aether_agent_memory.recall.embedding.models import EmbeddingModelBinding
from aether_agent_memory.recall.embedding.native import (
    NativeEmbeddingBackend,
    NativeEmbeddingSettings,
)
from aether_agent_memory.recall.embedding.service import (
    SemanticEmbeddingError,
    make_embedding_request,
)
from aether_agent_memory.runtime.contract_types import EmbeddingUsage, hash_json, hash_text
from aether_agent_memory.runtime.contracts.models import ErrorCode, TrustedContext
from aether_agent_memory.runtime.foundation.common import FoundationError, fingerprint, now
from aether_agent_memory.runtime.foundation.identity import Identity
from aether_agent_memory.runtime.foundation.requests import text_hash
from aether_agent_memory.runtime.foundation.storage import SQLiteUnitOfWork
from aether_agent_memory.runtime.foundation.telemetry import observed


@observed("embedding.native")
class NativeP3Embedding:
    dimensions = 512

    def __init__(
        self,
        uow: SQLiteUnitOfWork,
        identity: Identity,
        settings: NativeEmbeddingSettings | None = None,
    ) -> None:
        self.uow, self.identity = uow, identity
        self.backends: dict[EmbeddingUsage, NativeEmbeddingBackend] = {}
        self.closed = False
        try:
            settings = settings or NativeEmbeddingSettings()
            for usage in ("Query", "Passage"):
                self.backends[usage] = NativeEmbeddingBackend(settings)
            description = self.backends["Query"].describe()
            binding_fields = {
                key: description[key]
                for key in (
                    "model_id",
                    "model_version",
                    "dimension",
                    "dtype",
                    "embedding_schema_version",
                    "preprocessing_version",
                )
            }
            self.model_space = "bge_" + fingerprint(binding_fields)
            self.binding = EmbeddingModelBinding(
                **binding_fields,
                retrieval_space_ref=self.model_space,
                model_contract_ref="p3-native-bge-local-v1",
            )
            for backend in self.backends.values():
                backend.bind(self.binding, records=uow.store)
            with uow.transaction() as tx:
                # No silent relabeling of existing lexical or old-model vectors.
                prior = tx.read("settings", "p3_embedding_binding")
                current = self.binding.model_dump(mode="json")
                if prior and prior != current:
                    tx.abort(
                        ErrorCode.CONTRACT_VIOLATION,
                        "model binding changed; explicit reindex or fresh database required",
                    )
                if any(
                    row["target"]["model_space"] != self.model_space
                    for _, row in tx.rows("recall_vectors")
                ):
                    tx.abort(
                        ErrorCode.CONTRACT_VIOLATION,
                        "existing vectors belong to another model space; reindex required",
                    )
                tx.write("settings", "p3_embedding_binding", current)
        except BaseException:
            self.close()
            raise

    @classmethod
    def from_config(
        cls,
        uow: SQLiteUnitOfWork,
        identity: Identity,
        path: str | Path | None,
    ) -> NativeP3Embedding:
        settings = (
            NativeEmbeddingSettings.model_validate_json(Path(path).read_text(encoding="utf-8"))
            if path
            else NativeEmbeddingSettings()
        )
        return cls(uow, identity, settings)

    async def embed(self, ctx: TrustedContext, request: EmbeddingRequest) -> EmbeddingResult:
        request = EmbeddingRequest.model_validate_json(request.model_dump_json())
        usage: EmbeddingUsage = "Query" if request.usage == "query" else "Passage"
        if self.closed:
            raise FoundationError(ErrorCode.DEPENDENCY_UNAVAILABLE, "native embedding is closed")
        if request.model_space != self.model_space or request.deadline_at > ctx.deadline_at:
            raise FoundationError(ErrorCode.CONTRACT_VIOLATION, "model space or deadline mismatch")
        backend = self.backends[usage]
        if len(request.texts) > 32 or any(
            not text.strip()
            or len(text.encode("utf-8")) > 65536
            or backend.count_tokens(text, usage) > backend.max_input_tokens
            for text in request.texts
        ):
            raise FoundationError(
                ErrorCode.INVALID_ARGUMENT, "embedding input exceeds model limits"
            )
        key = fingerprint(
            [
                ctx.principal.principal_id,
                ctx.principal.home_scope.model_dump(mode="json"),
                request.operation_id,
                request.usage,
            ]
        )
        digest = fingerprint(
            {"model_space": self.model_space, "input_hashes": [text_hash(t) for t in request.texts]}
        )
        attempt_id = secrets.token_hex(16)
        with self.uow.transaction() as tx:
            self.identity.revalidate(tx, ctx)
            prior = tx.read("native_embedding_inputs", key)
            if prior and prior["digest"] != digest:
                tx.abort(ErrorCode.IDEMPOTENCY_CONFLICT, "embedding operation content changed")
            tx.write("native_embedding_inputs", key, {"digest": digest})
            row = {
                "operation_id": request.operation_id,
                "scope": ctx.principal.home_scope.model_dump(mode="json"),
                "trace_id": ctx.trace_id,
                "usage": request.usage,
                "model_space": self.model_space,
                "dimensions": self.dimensions,
                "input_hashes": [text_hash(t) for t in request.texts],
                "state": "started",
                "started_at": now(),
                "evidence_refs": [],
            }
            tx.write("native_embedding_attempts", attempt_id, row)
        evidence: list[str] = []
        try:
            seconds = (
                datetime.fromisoformat(request.deadline_at) - datetime.fromisoformat(now())
            ).total_seconds()
            async with asyncio.timeout(max(0, seconds)):
                items = []
                for index, text in enumerate(request.texts):
                    native_request = make_embedding_request(
                        tenant_id=ctx.principal.home_scope.tenant_id,
                        caller_ref="p3_" + request.usage,
                        caller_request_ref=fingerprint([key, index]),
                        trace_id=ctx.trace_id,
                        authorization_ref=fingerprint(ctx.principal.model_dump(mode="json")),
                        usage=usage,
                        input_ref=fingerprint([key, index, text_hash(text)]),
                        source_hash=hash_text(text),
                        input_binding_digest=hash_json(
                            {
                                "scope": ctx.principal.home_scope.model_dump(mode="json"),
                                "principal": ctx.principal.principal_id,
                                "source_hash": text_hash(text),
                                "usage": usage,
                                "model_space": self.model_space,
                            }
                        ),
                        input_binding_ref=digest,
                        model_binding=self.binding,
                        deadline_at=datetime.fromisoformat(request.deadline_at),
                        execution_policy_ref="p3-rf-native-no-internal-retry-v1",
                    )
                    computed = await backend.compute(native_request, text)
                    if (
                        computed.usage != usage
                        or computed.model_binding != self.binding
                        or len(computed.vector) != self.dimensions
                        or not all(math.isfinite(v) for v in computed.vector)
                        or abs(math.sqrt(sum(v * v for v in computed.vector)) - 1) > 1e-4
                    ):
                        raise FoundationError(
                            ErrorCode.CONTRACT_VIOLATION, "native vector binding or values invalid"
                        )
                    evidence.append(computed.evidence_ref)
                    items.append(
                        EmbeddingItem(
                            index=index, input_hash=text_hash(text), vector=tuple(computed.vector)
                        )
                    )
                result = EmbeddingResult(
                    operation_id=request.operation_id,
                    usage=request.usage,
                    model_space=self.model_space,
                    dimensions=self.dimensions,
                    items=tuple(items),
                )
                with self.uow.transaction() as tx:
                    self.identity.revalidate(tx, ctx)
                    if now() >= request.deadline_at:
                        tx.abort(ErrorCode.DEADLINE_EXCEEDED, "embedding result arrived too late")
                    tx.write(
                        "native_embedding_attempts",
                        attempt_id,
                        {
                            **row,
                            "state": "succeeded",
                            "finished_at": now(),
                            "evidence_refs": evidence,
                        },
                    )
                return result
        except BaseException as exc:
            code = self.error_code(exc)
            with self.uow.transaction() as tx:
                tx.write(
                    "native_embedding_attempts",
                    attempt_id,
                    {
                        **row,
                        "state": "cancelled"
                        if isinstance(exc, asyncio.CancelledError)
                        else "failed",
                        "finished_at": now(),
                        "evidence_refs": evidence,
                        "error_code": code.value,
                    },
                )
            if isinstance(exc, (asyncio.CancelledError, KeyboardInterrupt, SystemExit)):
                raise
            if isinstance(exc, FoundationError):
                raise
            raise FoundationError(
                code, "native embedding failed; inspect trace and evidence"
            ) from exc

    @staticmethod
    def error_code(error: BaseException) -> ErrorCode:
        if isinstance(error, FoundationError):
            return error.code
        if isinstance(error, TimeoutError):
            return ErrorCode.DEADLINE_EXCEEDED
        if isinstance(error, SemanticEmbeddingError):
            return {
                "EMBEDDING_INPUT_INVALID": ErrorCode.INVALID_ARGUMENT,
                "EMBEDDING_BINDING_MISMATCH": ErrorCode.CONTRACT_VIOLATION,
                "EMBEDDING_DEADLINE_EXCEEDED": ErrorCode.DEADLINE_EXCEEDED,
            }.get(error.code, ErrorCode.DEPENDENCY_UNAVAILABLE)
        return ErrorCode.DEPENDENCY_UNAVAILABLE

    async def health(self, ctx: TrustedContext) -> dict[str, Any]:
        for usage in ("query", "passage"):
            await self.embed(
                ctx,
                EmbeddingRequest(
                    operation_id="health_" + secrets.token_hex(8),
                    usage=usage,
                    texts=("健康检查",),
                    model_space=self.model_space,
                    deadline_at=ctx.deadline_at,
                ),
            )
        return {"state": "available"}

    def close(self) -> None:
        self.closed = True
        for backend in self.backends.values():
            backend.shutdown()
