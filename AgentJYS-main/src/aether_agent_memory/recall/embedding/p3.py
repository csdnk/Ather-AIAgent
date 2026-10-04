"""Existing native CPU inference adapted to the current P3 EmbeddingPort.

RF remains the only task/recovery authority. This adapter calls the native
backend directly; it does not start the older SemanticEmbeddingService queue.
"""
# P3 原生编码适配：统一 RF 身份、幂等与期限，把 Query/Passage 请求交给真实模型。
# 运行记录只保存输入摘要和证据引用，不保存待编码正文；重试调度仍由 RF 负责。

from __future__ import annotations

import asyncio
import math
import secrets
from datetime import datetime
from pathlib import Path
from typing import Any

from aether_agent_memory.recall.contracts.foundation import EmbeddingSpace
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
from aether_agent_memory.recall.embedding.spaces import validate_embedding
from aether_agent_memory.runtime.contract_types import EmbeddingUsage, hash_json, hash_text
from aether_agent_memory.runtime.contracts.models import ErrorCode, TrustedContext
from aether_agent_memory.runtime.foundation.common import FoundationError, fingerprint, now
from aether_agent_memory.runtime.foundation.identity import Identity
from aether_agent_memory.runtime.foundation.requests import text_hash
from aether_agent_memory.runtime.foundation.telemetry import observed
from aether_agent_memory.runtime.storage.ports import MetadataUnitOfWork


@observed("embedding.native")
class NativeP3Embedding:
    dimensions = 512

    def __init__(
        self,
        uow: MetadataUnitOfWork,
        identity: Identity,
        settings: NativeEmbeddingSettings | None = None,
    ) -> None:
        # 加载 Query/Passage 后端并校验共同模型空间，再与数据库中的既有绑定对照。
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
            # 保留旧空间 ID 的推导方式；新增完整空间描述单独持久化，避免给用户向量自动改标。
            self.model_space = "bge_" + fingerprint(binding_fields)
            self.binding = EmbeddingModelBinding(
                **binding_fields,
                retrieval_space_ref=self.model_space,
                model_contract_ref="p3-native-bge-local-v1",
            )
            if self.backends["Passage"].describe() != description:
                raise FoundationError(ErrorCode.CONTRACT_VIOLATION, "query/passage model mismatch")
            self.dimensions = self.binding.dimension
            # Query/Passage 使用各自规定前缀，但模型、维度和归一化必须兼容。
            # 输入上限取实际加载后端的较小值，而非只信配置声明。
            self.space = EmbeddingSpace(
                model_space=self.model_space,
                model_id=self.binding.model_id,
                model_revision=self.binding.model_version,
                dimensions=self.dimensions,
                tokenizer_id="tok_" + fingerprint(self.binding.preprocessing_version),
                query_prefix=settings.query_prefix,
                passage_prefix=settings.passage_prefix,
                normalization="unit",
                metric="cosine",
                max_input_tokens=min(b.max_input_tokens for b in self.backends.values()),
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
                # 同 ID 的分词器、前缀或度量变化也必须拒绝，不能复用旧索引冒充新空间。
                previous_space = tx.read("embedding_spaces", self.model_space)
                space_value = self.space.model_dump(mode="json")
                if previous_space is not None and previous_space != space_value:
                    tx.abort(ErrorCode.CONTRACT_VIOLATION, "model space configuration changed")
                tx.write("embedding_spaces", self.model_space, space_value)
        except BaseException:
            self.close()
            raise

    @classmethod
    def from_config(
        cls,
        uow: MetadataUnitOfWork,
        identity: Identity,
        path: str | Path | None,
    ) -> NativeP3Embedding:
        # 从显式配置文件读取模型设置；未指定时沿用默认 BGE 中文模型。
        settings = (
            NativeEmbeddingSettings.model_validate_json(Path(path).read_text(encoding="utf-8"))
            if path
            else NativeEmbeddingSettings()
        )
        return cls(uow, identity, settings)

    async def embed(self, ctx: TrustedContext, request: EmbeddingRequest) -> EmbeddingResult:
        # 先检查输入和幂等身份，再执行编码；成功交付之前重新检查权限与期限。
        request = EmbeddingRequest.model_validate_json(request.model_dump_json())
        usage: EmbeddingUsage = "Query" if request.usage == "query" else "Passage"
        if self.closed:
            raise FoundationError(ErrorCode.DEPENDENCY_UNAVAILABLE, "native embedding is closed")
        if request.model_space != self.model_space or request.deadline_at > ctx.deadline_at:
            raise FoundationError(ErrorCode.CONTRACT_VIOLATION, "model space or deadline mismatch")
        backend = self.backends[usage]
        # 按模型格式化后的真实 token 数检查输入；超限明确拒绝，不静默截断语义。
        if len(request.texts) > 32 or any(
            not text.strip()
            or len(text.encode("utf-8")) > 65536
            or backend.count_tokens(text, usage) > backend.max_input_tokens
            for text in request.texts
        ):
            raise FoundationError(
                ErrorCode.INVALID_ARGUMENT, "embedding input exceeds model limits"
            )
        # 幂等键按调用人、作用域、业务操作与用途隔离；同键换正文将触发冲突。
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
        # 一次业务操作可有多次实际尝试；独立 attempt_id 保留每次执行证据。
        attempt_id = secrets.token_hex(16)
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

        def begin_attempt() -> None:
            with self.uow.transaction() as tx:
                self.identity.revalidate(tx, ctx)
                prior = tx.read("native_embedding_inputs", key)
                if prior and prior["digest"] != digest:
                    tx.abort(ErrorCode.IDEMPOTENCY_CONFLICT, "embedding operation content changed")
                tx.write("native_embedding_inputs", key, {"digest": digest})
                tx.write("native_embedding_attempts", attempt_id, row)

        await asyncio.to_thread(begin_attempt)
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
                    # 底层负责实际工作占用：取消等待后，工作未结束前不能把后端当成空闲。
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
                validate_embedding(request, result, self.space)

                # 推理期间可能撤权或超时；结果写为成功并交付之前必须再次检查。
                def complete_attempt() -> None:
                    with self.uow.transaction() as tx:
                        self.identity.revalidate(tx, ctx)
                        if now() >= request.deadline_at:
                            tx.abort(
                                ErrorCode.DEADLINE_EXCEEDED, "embedding result arrived too late"
                            )
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

                await asyncio.to_thread(complete_attempt)
                return result
        # 失败或取消也落尝试记录；取消信号继续上抛，不伪装成成功或内部重试。
        except BaseException as exc:
            code = self.error_code(exc)
            cancelled = isinstance(exc, asyncio.CancelledError)

            def fail_attempt() -> None:
                with self.uow.transaction() as tx:
                    tx.write(
                        "native_embedding_attempts",
                        attempt_id,
                        {
                            **row,
                            "state": "cancelled" if cancelled else "failed",
                            "finished_at": now(),
                            "evidence_refs": evidence,
                            "error_code": code.value,
                        },
                    )

            await asyncio.to_thread(fail_attempt)
            if isinstance(exc, (asyncio.CancelledError, KeyboardInterrupt, SystemExit)):
                raise
            if isinstance(exc, FoundationError):
                raise
            raise FoundationError(
                code, "native embedding failed; inspect trace and evidence"
            ) from exc

    @staticmethod
    def error_code(error: BaseException) -> ErrorCode:
        # 把底层错误映射为 RF 可识别的错误码，保留超时、非法输入和绑定错误的区别。
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
        # 分别执行真实 Query 和 Passage 编码，只有两条路径都成功才报告可用。
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
        # 停止接收请求，并等待底层实际推理结束；调用方取消不等于线程资源已释放。
        self.closed = True
        for backend in self.backends.values():
            backend.shutdown()
