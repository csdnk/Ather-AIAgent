"""Pure, bounded Query/Passage compute with durable input and execution bindings.

No chunker, vector sink, Memory mutation or P2 write is reachable here. A backend
must expose actual output bindings; old bare-vector clients are not sufficient.
"""

from __future__ import annotations

import asyncio
import struct
from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta
from typing import Protocol
from uuid import uuid4

from pydantic import Field

from aether_agent_memory.b1.semantic.models import (
    EmbeddingModelBinding,
    SemanticEmbeddingExecution,
    SemanticEmbeddingRequest,
    SemanticEmbeddingResult,
)
from aether_agent_memory.runtime.capability_store import AtomicRecordStore
from aether_agent_memory.runtime.contract_types import (
    ContractModel,
    EmbeddingErrorCode,
    EmbeddingUsage,
    Hash,
    Identifier,
    Number,
    PositiveInt,
    Text,
    Timestamp,
    hash_bytes,
    hash_json,
    hash_text,
    utcnow,
    vector_bytes,
)


class SemanticEmbeddingError(RuntimeError):
    def __init__(self, code: EmbeddingErrorCode) -> None:
        self.code = code
        super().__init__(code)


class TransientEmbeddingError(RuntimeError):
    """Backend proves the attempt ended; the coordinator may retry within its budget."""


class ResolvedEmbeddingInput(ContractModel):
    text: Text
    input_binding_digest: Hash


class ComputedEmbedding(ContractModel):
    vector: list[Number]
    usage: EmbeddingUsage
    model_binding: EmbeddingModelBinding
    evidence_ref: Identifier


class EmbeddingInputPort(Protocol):
    async def authorize(self, request: SemanticEmbeddingRequest) -> bool: ...
    async def resolve(self, request: SemanticEmbeddingRequest) -> ResolvedEmbeddingInput: ...


class EmbeddingBackendPort(Protocol):
    async def compute(self, request: SemanticEmbeddingRequest, text: str) -> ComputedEmbedding: ...


class SemanticEmbeddingCapability(Protocol):
    async def embed(self, request: SemanticEmbeddingRequest) -> SemanticEmbeddingResult: ...
    async def vector_for(
        self, request: SemanticEmbeddingRequest, result: SemanticEmbeddingResult
    ) -> list[float]: ...


@dataclass(frozen=True)
class EmbeddingRuntime:
    binding: EmbeddingModelBinding
    backend: EmbeddingBackendPort
    count_tokens: Callable[[str, EmbeddingUsage], int]
    max_input_tokens: int

    def __post_init__(self) -> None:
        if isinstance(self.max_input_tokens, bool) or self.max_input_tokens <= 0:
            raise ValueError("max_input_tokens must be positive")
        if self.binding.dtype not in {"float32", "float64"}:
            raise ValueError("this adapter supports registered little-endian float32/float64 only")


class EmbeddingPolicy(ContractModel):
    execution_policy_ref: Identifier = "embedding-policy-0.1"
    query_concurrency: PositiveInt = 2
    passage_concurrency: PositiveInt = 1
    query_queue: int = Field(default=64, strict=True, ge=0)
    passage_queue: int = Field(default=128, strict=True, ge=0)
    query_timeout_ms: PositiveInt = 2000
    passage_timeout_ms: PositiveInt = 10000
    max_attempts: int = Field(default=2, strict=True, ge=1, le=2)
    retry_backoff_ms: PositiveInt = 20

    def concurrency(self, usage: EmbeddingUsage) -> int:
        return self.query_concurrency if usage == "Query" else self.passage_concurrency

    def capacity(self, usage: EmbeddingUsage) -> int:
        return self.concurrency(usage) + (
            self.query_queue if usage == "Query" else self.passage_queue
        )

    def timeout_ms(self, usage: EmbeddingUsage) -> int:
        return self.query_timeout_ms if usage == "Query" else self.passage_timeout_ms


class _ExecutionRecord(ContractModel):
    request: SemanticEmbeddingRequest
    execution: SemanticEmbeddingExecution
    policy: EmbeddingPolicy


class _VectorArtifact(ContractModel):
    tenant_id: Identifier
    result: SemanticEmbeddingResult
    vector: list[Number]
    backend_evidence_ref: Identifier
    input_binding_ref: Identifier


def reuse_digest(request: SemanticEmbeddingRequest) -> Hash:
    return hash_json(
        request.model_dump(
            mode="json",
            include={
                "tenant_id",
                "usage",
                "source_hash",
                "input_binding_digest",
                "model_binding",
            },
        )
    )


def make_embedding_request(
    *,
    tenant_id: str,
    caller_ref: str,
    caller_request_ref: str,
    trace_id: str,
    authorization_ref: str,
    usage: EmbeddingUsage,
    input_ref: str,
    source_hash: Hash,
    input_binding_digest: Hash,
    input_binding_ref: str,
    model_binding: EmbeddingModelBinding,
    deadline_at: Timestamp,
    execution_policy_ref: str,
) -> SemanticEmbeddingRequest:
    # The reuse identity excludes all ephemeral request/evidence/deadline identifiers.
    values = dict(
        tenant_id=tenant_id,
        usage=usage,
        source_hash=source_hash,
        input_binding_digest=input_binding_digest,
        model_binding=model_binding,
    )
    digest = hash_json(
        {
            k: v.model_dump(mode="json") if isinstance(v, ContractModel) else v
            for k, v in values.items()
        }
    )
    return SemanticEmbeddingRequest(
        schema_version="embedding-data-0.1",
        created_at=utcnow(),
        embedding_request_id=uuid4().hex,
        caller_ref=caller_ref,
        caller_request_ref=caller_request_ref,
        trace_id=trace_id,
        authorization_ref=authorization_ref,
        input_ref=input_ref,
        input_binding_ref=input_binding_ref,
        deadline_at=deadline_at,
        execution_policy_ref=execution_policy_ref,
        reuse_digest=digest,
        **values,
    )


class SemanticEmbeddingService:
    def __init__(
        self,
        store: AtomicRecordStore,
        inputs: EmbeddingInputPort,
        query_runtime: EmbeddingRuntime,
        passage_runtime: EmbeddingRuntime,
        policy: EmbeddingPolicy | None = None,
    ) -> None:
        if query_runtime.backend is passage_runtime.backend:
            raise ValueError("Query and Passage need independent backend execution resources")
        self.store, self.inputs = store, inputs
        self.runtimes = {"Query": query_runtime, "Passage": passage_runtime}
        self.policy = policy or EmbeddingPolicy()
        self._tasks: dict[tuple[str, str], asyncio.Task[None]] = {}
        self._owner = uuid4().hex
        self._slots = {
            u: asyncio.Semaphore(self.policy.concurrency(u)) for u in ("Query", "Passage")
        }

    @staticmethod
    def _key(request: SemanticEmbeddingRequest) -> str:
        return hash_json([request.caller_ref, request.caller_request_ref]).value

    async def _authorize(self, request: SemanticEmbeddingRequest) -> None:
        try:
            allowed = await self.inputs.authorize(request)
        except SemanticEmbeddingError:
            raise
        except Exception as exc:
            raise SemanticEmbeddingError("EMBEDDING_INPUT_INVALID") from exc
        if allowed is not True:
            raise SemanticEmbeddingError("EMBEDDING_INPUT_INVALID")

    async def _authorized_input(self, request: SemanticEmbeddingRequest) -> str:
        await self._authorize(request)
        try:
            resolved = await self.inputs.resolve(request)
        except SemanticEmbeddingError:
            raise
        except Exception as exc:
            raise SemanticEmbeddingError("EMBEDDING_INPUT_INVALID") from exc
        if (
            not resolved.text.strip()
            or hash_text(resolved.text) != request.source_hash
            or resolved.input_binding_digest != request.input_binding_digest
        ):
            raise SemanticEmbeddingError("EMBEDDING_INPUT_INVALID")
        if reuse_digest(request) != request.reuse_digest:
            raise SemanticEmbeddingError("EMBEDDING_BINDING_MISMATCH")
        runtime = self.runtimes[request.usage]
        if runtime.binding != request.model_binding:
            raise SemanticEmbeddingError("EMBEDDING_BINDING_MISMATCH")
        count = runtime.count_tokens(resolved.text, request.usage)
        if (
            isinstance(count, bool)
            or not isinstance(count, int)
            or not 0 < count <= runtime.max_input_tokens
        ):
            raise SemanticEmbeddingError("EMBEDDING_INPUT_INVALID")
        return resolved.text

    async def embed(self, request: SemanticEmbeddingRequest) -> SemanticEmbeddingResult:
        request = SemanticEmbeddingRequest.model_validate_json(request.model_dump_json())
        wait_deadline = min(
            request.deadline_at,
            utcnow() + timedelta(milliseconds=self.policy.timeout_ms(request.usage)),
        )
        remaining = (wait_deadline - utcnow()).total_seconds()
        if remaining <= 0:
            raise SemanticEmbeddingError("EMBEDDING_DEADLINE_EXCEEDED")
        try:
            async with asyncio.timeout(remaining):
                text = await self._authorized_input(request)
                key = self._admit(request, wait_deadline)
                tenant = request.tenant_id
                task_key = (tenant, key)
                if task_key not in self._tasks:
                    task = asyncio.create_task(self._run(tenant, key, text))
                    self._tasks[task_key] = task
                    task.add_done_callback(lambda t: self._task_done(task_key, t))
                while True:
                    record = self._load(tenant, key)
                    if record.execution.state == "failed":
                        raise SemanticEmbeddingError(
                            record.execution.error_code or "EMBEDDING_COMPUTE_FAILED"
                        )
                    if record.execution.state == "succeeded":
                        result = self._result(record)
                        await self._authorize(request)
                        if utcnow() >= wait_deadline:
                            raise SemanticEmbeddingError("EMBEDDING_DEADLINE_EXCEEDED")
                        return result
                    # Caller cancellation only ends this attachment, not the shared execution.
                    await asyncio.sleep(0.005)
        except TimeoutError as exc:
            raise SemanticEmbeddingError("EMBEDDING_DEADLINE_EXCEEDED") from exc

    def _task_done(self, key: tuple[str, str], task: asyncio.Task[None]) -> None:
        self._tasks.pop(key, None)
        if not task.cancelled():
            task.exception()  # Retrieve persistence exceptions; caller sees no successful result.

    def _admit(self, request: SemanticEmbeddingRequest, deadline: Timestamp) -> str:
        key, tenant = self._key(request), request.tenant_id
        with self.store.transaction() as tx:
            existing = tx.get("semantic-execution", tenant, key)
            if existing is not None:
                old = _ExecutionRecord.model_validate_json(existing)
                if old.request.reuse_digest != request.reuse_digest:
                    raise SemanticEmbeddingError("EMBEDDING_BINDING_MISMATCH")
                return key
            if request.execution_policy_ref != self.policy.execution_policy_ref:
                raise SemanticEmbeddingError("EMBEDDING_BINDING_MISMATCH")
            now = utcnow()
            if now >= deadline:
                raise SemanticEmbeddingError("EMBEDDING_DEADLINE_EXCEEDED")
            active = 0
            for t, k, value in tx.scan("semantic-execution"):
                record = _ExecutionRecord.model_validate_json(value)
                if record.execution.state in ("queued", "running"):
                    if now >= record.execution.deadline_at:
                        tx.put(
                            "semantic-execution",
                            t,
                            k,
                            self._failed(record, "EMBEDDING_DEADLINE_EXCEEDED").model_dump_json(),
                        )
                    elif record.request.usage == request.usage:
                        active += 1
            if active >= self.policy.capacity(request.usage):
                raise SemanticEmbeddingError("EMBEDDING_BUSY")
            request = request.replaced(deadline_at=deadline)
            execution = SemanticEmbeddingExecution(
                schema_version="embedding-data-0.1",
                tenant_id=tenant,
                created_at=now,
                embedding_request_id=request.embedding_request_id,
                request_ref=request.embedding_request_id,
                caller_ref=request.caller_ref,
                caller_request_ref=request.caller_request_ref,
                state="queued",
                attempts_reserved=0,
                deadline_at=deadline,
                execution_policy_ref=request.execution_policy_ref,
                result_ref=None,
                error_code=None,
                lease_owner=None,
                lease_until=None,
                lease_token=None,
                state_version=0,
            )
            record = _ExecutionRecord(request=request, execution=execution, policy=self.policy)
            cached = tx.get("semantic-cache", tenant, request.reuse_digest.value)
            if cached is not None:
                artifact_json = tx.get("semantic-result", tenant, cached)
                if artifact_json is not None:
                    try:
                        artifact = _VectorArtifact.model_validate_json(artifact_json)
                        self._verify_artifact(request, artifact)
                    except ValueError:
                        tx.delete("semantic-cache", tenant, request.reuse_digest.value)
                    else:
                        record = record.replaced(
                            execution=execution.replaced(
                                state="succeeded", result_ref=cached, state_version=1
                            )
                        )
            tx.put("semantic-execution", tenant, key, record.model_dump_json())
        return key

    def _load(self, tenant: str, key: str) -> _ExecutionRecord:
        with self.store.transaction() as tx:
            value = tx.get("semantic-execution", tenant, key)
        if value is None:
            raise SemanticEmbeddingError("EMBEDDING_COMPUTE_FAILED")
        return _ExecutionRecord.model_validate_json(value)

    @staticmethod
    def _failed(record: _ExecutionRecord, code: EmbeddingErrorCode) -> _ExecutionRecord:
        return record.replaced(
            execution=record.execution.replaced(
                state="failed",
                result_ref=None,
                error_code=code,
                lease_owner=None,
                lease_token=None,
                lease_until=None,
                state_version=record.execution.state_version + 1,
            )
        )

    def _fail(self, tenant: str, key: str, token: str | None, code: EmbeddingErrorCode) -> None:
        with self.store.transaction() as tx:
            value = tx.get("semantic-execution", tenant, key)
            if value is None:
                return
            record = _ExecutionRecord.model_validate_json(value)
            if record.execution.state in ("succeeded", "failed"):
                return
            if record.execution.lease_token != token and utcnow() < record.execution.deadline_at:
                return
            tx.put("semantic-execution", tenant, key, self._failed(record, code).model_dump_json())

    async def _run(self, tenant: str, key: str, text: str) -> None:
        record = self._load(tenant, key)
        if record.execution.state in ("succeeded", "failed"):
            return
        token: str | None = None
        remaining = (record.execution.deadline_at - utcnow()).total_seconds()
        try:
            async with asyncio.timeout(max(0, remaining)), self._slots[record.request.usage]:
                while True:
                    with self.store.transaction() as tx:
                        value = tx.get("semantic-execution", tenant, key)
                        if value is None:
                            return
                        record = _ExecutionRecord.model_validate_json(value)
                        execution = record.execution
                        if execution.state in ("succeeded", "failed"):
                            return
                        if utcnow() >= execution.deadline_at:
                            raise SemanticEmbeddingError("EMBEDDING_DEADLINE_EXCEEDED")
                        if execution.state == "running" and execution.lease_token != token:
                            claimed = False
                        else:
                            others = [
                                _ExecutionRecord.model_validate_json(v)
                                for _, _, v in tx.scan("semantic-execution")
                            ]
                            running = sum(
                                r.execution.state == "running"
                                and r.execution.deadline_at > utcnow()
                                and r.request.usage == record.request.usage
                                for r in others
                            )
                            claimed = (
                                execution.state == "running"
                                or running < record.policy.concurrency(record.request.usage)
                            )
                            if claimed:
                                if execution.attempts_reserved >= record.policy.max_attempts:
                                    raise SemanticEmbeddingError("EMBEDDING_COMPUTE_FAILED")
                                token = token or uuid4().hex
                                record = record.replaced(
                                    execution=execution.replaced(
                                        state="running",
                                        lease_owner=self._owner,
                                        lease_token=token,
                                        lease_until=execution.deadline_at,
                                        attempts_reserved=execution.attempts_reserved + 1,
                                        state_version=execution.state_version + 1,
                                    )
                                )
                                tx.put("semantic-execution", tenant, key, record.model_dump_json())
                    if not claimed:
                        await asyncio.sleep(0.01)
                        continue
                    try:
                        computed = await self.runtimes[record.request.usage].backend.compute(
                            record.request, text
                        )
                    except TransientEmbeddingError:
                        if record.execution.attempts_reserved >= record.policy.max_attempts:
                            raise SemanticEmbeddingError("EMBEDDING_COMPUTE_FAILED") from None
                        await asyncio.sleep(record.policy.retry_backoff_ms / 1000)
                        continue
                    assert token is not None
                    self._save_result(tenant, key, token, record, computed)
                    return
        except TimeoutError:
            self._fail(tenant, key, token, "EMBEDDING_DEADLINE_EXCEEDED")
        except SemanticEmbeddingError as exc:
            self._fail(tenant, key, token, exc.code)
        except asyncio.CancelledError:
            # An interrupted worker leaves its persisted lease and attempt intact.
            raise
        except Exception:
            self._fail(tenant, key, token, "EMBEDDING_COMPUTE_FAILED")

    def _save_result(
        self,
        tenant: str,
        key: str,
        token: str,
        record: _ExecutionRecord,
        computed: ComputedEmbedding,
    ) -> None:
        request = record.request
        if computed.usage != request.usage or computed.model_binding != request.model_binding:
            raise SemanticEmbeddingError("EMBEDDING_BINDING_MISMATCH")
        data = vector_bytes(
            computed.vector, request.model_binding.dimension, request.model_binding.dtype
        )
        fmt = "f" if request.model_binding.dtype == "float32" else "d"
        vector = list(struct.unpack("<" + fmt * request.model_binding.dimension, data))
        now = utcnow()
        result = SemanticEmbeddingResult(
            schema_version="embedding-data-0.1",
            tenant_id=tenant,
            created_at=now,
            embedding_result_id=uuid4().hex,
            request_ref=request.embedding_request_id,
            usage=request.usage,
            model_binding=request.model_binding,
            source_hash=request.source_hash,
            input_binding_digest=request.input_binding_digest,
            vector_ref=uuid4().hex,
            vector_hash=hash_bytes(data),
            validation_evidence_ref=uuid4().hex,
            validated_at=now,
        )
        artifact = _VectorArtifact(
            tenant_id=tenant,
            result=result,
            vector=vector,
            backend_evidence_ref=computed.evidence_ref,
            input_binding_ref=request.input_binding_ref,
        )
        with self.store.transaction() as tx:
            latest = tx.get("semantic-execution", tenant, key)
            if latest is None:
                return
            current = _ExecutionRecord.model_validate_json(latest)
            if (
                current.execution.lease_token != token
                or current.execution.state != "running"
                or current.execution.state_version != record.execution.state_version
            ):
                return
            if utcnow() >= current.execution.deadline_at:
                raise SemanticEmbeddingError("EMBEDDING_DEADLINE_EXCEEDED")
            updated = current.replaced(
                execution=current.execution.replaced(
                    state="succeeded",
                    result_ref=result.embedding_result_id,
                    error_code=None,
                    lease_owner=None,
                    lease_token=None,
                    lease_until=None,
                    state_version=current.execution.state_version + 1,
                )
            )
            tx.put(
                "semantic-result", tenant, result.embedding_result_id, artifact.model_dump_json()
            )
            tx.put("semantic-vector", tenant, result.vector_ref, result.embedding_result_id)
            tx.put(
                "semantic-evidence",
                tenant,
                result.validation_evidence_ref,
                artifact.model_dump_json(),
            )
            tx.put("semantic-cache", tenant, request.reuse_digest.value, result.embedding_result_id)
            tx.put("semantic-execution", tenant, key, updated.model_dump_json())

    @staticmethod
    def _verify_artifact(request: SemanticEmbeddingRequest, artifact: _VectorArtifact) -> None:
        result = artifact.result
        if (
            artifact.tenant_id != request.tenant_id
            or result.tenant_id != request.tenant_id
            or result.usage != request.usage
            or result.model_binding != request.model_binding
            or result.source_hash != request.source_hash
            or result.input_binding_digest != request.input_binding_digest
        ):
            raise ValueError("cached result binding mismatch")
        data = vector_bytes(
            artifact.vector, result.model_binding.dimension, result.model_binding.dtype
        )
        if hash_bytes(data) != result.vector_hash:
            raise ValueError("cached vector integrity mismatch")

    def _artifact(self, record: _ExecutionRecord) -> _VectorArtifact:
        with self.store.transaction() as tx:
            value = tx.get(
                "semantic-result", record.request.tenant_id, record.execution.result_ref or ""
            )
        if value is None:
            raise SemanticEmbeddingError("EMBEDDING_COMPUTE_FAILED")
        artifact = _VectorArtifact.model_validate_json(value)
        try:
            self._verify_artifact(record.request, artifact)
        except ValueError as exc:
            raise SemanticEmbeddingError("EMBEDDING_COMPUTE_FAILED") from exc
        return artifact

    def _result(self, record: _ExecutionRecord) -> SemanticEmbeddingResult:
        return self._artifact(record).result

    async def vector_for(
        self, request: SemanticEmbeddingRequest, result: SemanticEmbeddingResult
    ) -> list[float]:
        try:
            async with asyncio.timeout(max(0, (request.deadline_at - utcnow()).total_seconds())):
                await self._authorize(request)
        except TimeoutError as exc:
            raise SemanticEmbeddingError("EMBEDDING_DEADLINE_EXCEEDED") from exc
        record = self._load(request.tenant_id, self._key(request))
        if record.request.reuse_digest != request.reuse_digest:
            raise SemanticEmbeddingError("EMBEDDING_BINDING_MISMATCH")
        artifact = self._artifact(record)
        if artifact.result != result:
            raise SemanticEmbeddingError("EMBEDDING_BINDING_MISMATCH")
        return list(artifact.vector)

    async def close(self) -> None:
        tasks = list(self._tasks.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
