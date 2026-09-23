"""Explicit opt-in new Recall workflow; existing HTTP wire format remains stable."""
# 新 Recall 流程的运行入口：复用 RF 请求生命周期，串联候选、组包和事务提交。
# 只有 Host 显式装配完整 B 接口后才启用，现有 HTTP 请求格式保持兼容。

from __future__ import annotations

from aether_agent_memory.recall.contracts.foundation import (
    ContextCommitRequest,
    MemorySearchRequest,
    RecallPlanRequest,
)
from aether_agent_memory.recall.contracts.models import ContextPack, RecallRecord, RecallRequest
from aether_agent_memory.recall.contracts.ports import MemoryCandidatePort
from aether_agent_memory.remember.contracts.foundation import ContextGuardRequest
from aether_agent_memory.remember.contracts.ports import (
    MemoryContextGuardPort,
    MemoryFoundationPort,
)
from aether_agent_memory.runtime.contracts.models import ErrorCode, Permission, TrustedContext
from aether_agent_memory.runtime.foundation.common import FoundationError
from aether_agent_memory.runtime.foundation.requests import select_scope
from aether_agent_memory.runtime.foundation.telemetry import observed

from .assembly import ContextAssembly
from .service import Recall


@observed("recall.generation")
class GenerationRecall(Recall):
    def __init__(
        self,
        base: Recall,
        candidates: MemoryCandidatePort,
        bodies: MemoryFoundationPort,
        guards: MemoryContextGuardPort,
    ) -> None:
        # Reuse the already registered RF event producer and request lifecycle.
        # 共享原 Recall 的事务、事件生产者和配置；新策略版本区分新旧结果。
        self.uow, self.identity, self.events = base.uow, base.identity, base.events
        self.memories, self.embedding, self.vectors = base.memories, base.embedding, base.vectors
        self.model_space, self.settings = base.model_space, base.settings
        self.tokenizer, self.reranker = base.tokenizer, base.reranker
        self.sources = base.sources
        self.policy_version = "generation_" + base.policy_version
        self.assembly = ContextAssembly(self, candidates, bodies, guards)

    async def retrieve(
        self, ctx: TrustedContext, request: RecallRequest, recall_id: str
    ) -> ContextPack:
        # 把旧入口请求转换成新计划，生成计划后读取当前 Recall 修订并条件提交。
        self.stage(ctx, recall_id, "assemble")
        scope = select_scope(ctx, request.selection)
        # auto 在会话/任务作用域下选择双来源，其他情况仅选择长期记忆。
        selected = (
            ("working", "long_term")
            if request.sources == "both"
            or (request.sources == "auto" and (scope.session_id or scope.task_id))
            else ("long_term",)
            if request.sources == "auto"
            else (request.sources,)
        )
        # Working-only 不构造长期检索请求，因此不会触发 Query 编码或向量检索。
        search = (
            MemorySearchRequest(
                operation_id=recall_id,
                purpose="recall",
                query=request.query,
                selection=request.selection,
                model_space=self.model_space,
                memory_top_k=self.settings.candidate_limit,
                chunk_page_size=min(100, self.settings.candidate_limit),
                max_chunk_hits=self.settings.max_discovery,
                max_rounds=10,
                deadline_at=ctx.deadline_at,
            )
            if "long_term" in selected
            else None
        )
        plan = await self.assembly.plan(
            ctx,
            RecallPlanRequest(
                recall_id=recall_id,
                query=request.query,
                selection=request.selection,
                sources=selected,
                token_budget=request.token_budget,
                context_tokenizer=self.tokenizer.identifier,
                policy_version=self.policy_version,
                deadline_at=ctx.deadline_at,
                long_term_search=search,
            ),
        )
        self.stage(
            ctx, recall_id, "finalize", {"units": len(plan.units), "tokens": plan.tokens_used}
        )
        # 提交与读取已提交包共享事务；任一复核失败都会回滚结果和入包事件。
        with self.uow.transaction() as tx:
            row = tx.read("recall_requests", recall_id)
            request_commit = ContextCommitRequest(
                operation_id=ctx.operation_id,
                expected_recall_revision=row["record"]["revision"],
                plan=plan,
                final_guards=tuple(
                    b.guard for u in plan.units for b in u.bodies if b.guard is not None
                ),
            )
            self.assembly.commit(tx, ctx, request_commit)
            result = ContextPack.model_validate(tx.read("recall_requests", recall_id)["pack"])
        return result

    def result(self, ctx: TrustedContext, recall_id: str) -> ContextPack:
        # 历史结果每次重取都按持久化凭据重新向 B 核验，不能直接返回缓存正文。
        with self.uow.transaction() as tx:
            saved = tx.read("recall_assembly", recall_id)
            if saved is not None:
                row = tx.read("recall_requests", recall_id)
                if row is None:
                    raise FoundationError(ErrorCode.NOT_FOUND, "recall not found")
                record = RecallRecord.model_validate(row["record"])
                self.identity.authorize(tx, ctx, Permission.READ, self.ref(record))
                if not record.result_available or row["pack"] is None:
                    raise FoundationError(ErrorCode.REQUEST_IN_PROGRESS, "no committed result")
                self.assembly.revalidate(
                    tx,
                    ctx,
                    ContextGuardRequest.model_validate(saved["expectations"]),
                    ctx.deadline_at,
                )
                return ContextPack.model_validate(row["pack"])
        # 只有没有新流程计划的历史结果，才允许走旧服务自己的复核路径。
        # Results produced before opting in retain the existing retrieval path.
        return super().result(ctx, recall_id)
