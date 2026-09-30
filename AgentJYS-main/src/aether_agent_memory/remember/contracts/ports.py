"""B owns these ports; A/C never directly mutate Remember tables."""

from typing import Literal, Protocol

from aether_agent_memory.runtime.contracts.models import (
    PageRequest,
    ScopeSelector,
    TrustedContext,
)
from aether_agent_memory.runtime.contracts.ports import Transaction

from .foundation import (
    CandidateQualificationResult,
    CandidateQualificationTarget,
    ChunkProjectionRequest,
    ChunkProjectionResult,
    ContextGuardRequest,
    FullBodyReadResult,
    GuardStamp,
    MemoryRecord,
    MemoryRelationSnapshot,
    ProjectionManifest,
    ProjectionReadiness,
    ReferenceHandoff,
    ReferenceHandoffReceipt,
)
from .models import (
    CorrectionRequest,
    DeleteReceipt,
    DeleteRequest,
    DocumentContent,
    DocumentInput,
    EligibilityBatch,
    ExtractionRequest,
    ExtractionResult,
    LifecycleRequest,
    MemoryReadBatch,
    MemoryRef,
    MemorySnapshot,
    ProjectionRequest,
    ProjectionResult,
    ProjectionTarget,
    RememberReceipt,
    RememberRequest,
    SourceAcquisition,
    SourceRef,
)


class MemoryQualificationPort(Protocol):
    async def qualify(
        self,
        ctx: TrustedContext,
        targets: tuple[CandidateQualificationTarget, ...],
        purpose: Literal["recall", "extraction"],
    ) -> tuple[CandidateQualificationResult, ...]:
        # Require nonempty unique targets, return exactly one result per target.
        #
        # Use one authoritative view per batch, enforce ctx deadline and current
        # authorization; purpose never grants permissions. No reads/access events.
        #
        # 提供方：B。输入必须非空且目标唯一；每个目标恰好返回一条资格结果。
        # 按同一权威视图校验当前权限与期限；purpose 只说明用途，不授予权限。
        # 本接口不读取正文、不计访问热度；依赖无法核验不能冒充 allowed。
        ...


class MemoryContextGuardPort(Protocol):
    def relations(self, ctx: TrustedContext, refs: tuple[MemoryRef, ...]) -> MemoryRelationSnapshot:
        # Return every requested guard and all current conflict memberships from
        # one authorized B snapshot. If any ref cannot be verified, fail explicitly.
        # 提供方：B。返回所有请求成员的当前凭据及完整冲突关系；任何成员无法核验都明确失败。
        ...

    def revalidate_context(
        self, tx: Transaction, ctx: TrustedContext, request: ContextGuardRequest
    ) -> tuple[GuardStamp, ...]:
        # Check current B facts and authorization in tx, or abort on any mismatch.
        #
        # Return every requested member once. Verify current source/lifecycle,
        # body, object/relations revisions and each supplied published generation.
        # Never trust the supplied stamps as proof; never open a nested transaction.
        # Enforce ctx current authorization/epoch and deadline. Return checked_at
        # between the expected check time and ctx deadline. Changed facts raise
        # RESULT_INVALIDATED; invalid identity retains RF authorization errors;
        # unavailable evidence raises DEPENDENCY_UNAVAILABLE, never empty success.
        #
        # 提供方：B；必须复用 tx，禁止另开事务。逐成员复核正文、对象、关系、授权和发布批次。
        # 失配报 RESULT_INVALIDATED，证据不可用报 DEPENDENCY_UNAVAILABLE，权限错误保留 RF 错误码。
        # 必须完整返回当前凭据，不能通过空响应或照抄预期凭据表示成功。
        ...


class MemoryFoundationPort(Protocol):
    async def load_bodies(
        self,
        ctx: TrustedContext,
        refs: tuple[MemoryRef, ...],
    ) -> tuple[FullBodyReadResult, ...]: ...
    def publish_projection(
        self,
        tx: Transaction,
        ctx: TrustedContext,
        manifest: ProjectionManifest,
        expected_revision: int,
    ) -> MemoryRecord: ...
    def accept_handoff(
        self,
        tx: Transaction,
        ctx: TrustedContext,
        request: ReferenceHandoff,
    ) -> ReferenceHandoffReceipt: ...


class RememberPort(Protocol):
    async def save(self, ctx: TrustedContext, request: RememberRequest) -> RememberReceipt: ...
    def get(self, ctx: TrustedContext, memory_id: str) -> MemorySnapshot: ...
    def correct(
        self,
        ctx: TrustedContext,
        memory_id: str,
        request: CorrectionRequest,
    ) -> RememberReceipt: ...
    def lifecycle(
        self,
        ctx: TrustedContext,
        memory_id: str,
        request: LifecycleRequest,
    ) -> MemorySnapshot: ...
    def delete(
        self,
        ctx: TrustedContext,
        memory_id: str,
        request: DeleteRequest,
    ) -> DeleteReceipt: ...
    def delete_source(
        self,
        ctx: TrustedContext,
        source_id: str,
        request: DeleteRequest,
    ) -> DeleteReceipt: ...


class MemoryReadPort(Protocol):
    async def projection_readiness(
        self,
        ctx: TrustedContext,
        selection: ScopeSelector,
        memory_source: Literal["working", "long_term"],
    ) -> ProjectionReadiness: ...

    def working(
        self,
        ctx: TrustedContext,
        selection: ScopeSelector,
        page: PageRequest,
    ) -> MemoryReadBatch: ...
    def load(
        self,
        ctx: TrustedContext,
        refs: tuple[MemoryRef, ...],
    ) -> MemoryReadBatch: ...
    def final_guard(
        self,
        tx: Transaction,
        ctx: TrustedContext,
        refs: tuple[MemoryRef, ...],
        purpose: Literal["recall", "history", "actuate", "cleanup"],
    ) -> EligibilityBatch: ...
    def history(
        self,
        ctx: TrustedContext,
        memory_id: str,
        page: PageRequest,
    ) -> MemoryReadBatch: ...
    def source(self, ctx: TrustedContext, source_id: str) -> SourceRef: ...


class ExtractionPort(Protocol):
    async def extract(
        self,
        ctx: TrustedContext,
        request: ExtractionRequest,
    ) -> ExtractionResult: ...


class DocumentReaderPort(Protocol):
    async def acquire(self, ctx: TrustedContext, document: DocumentInput) -> SourceAcquisition: ...
    async def parse(
        self,
        ctx: TrustedContext,
        acquired: SourceAcquisition,
    ) -> DocumentContent: ...


class ProjectionPort(Protocol):
    async def project(
        self,
        ctx: TrustedContext,
        request: ProjectionRequest,
    ) -> ProjectionResult: ...

    async def inspect(
        self,
        ctx: TrustedContext,
        target: ProjectionTarget,
        operation_id: str,
    ) -> ProjectionResult: ...

    async def delete(
        self,
        ctx: TrustedContext,
        target: ProjectionTarget,
        operation_id: str,
    ) -> ProjectionResult: ...


class GenerationProjectionPort(Protocol):
    async def project(
        self,
        ctx: TrustedContext,
        request: ChunkProjectionRequest,
    ) -> ChunkProjectionResult: ...

    async def inspect(
        self,
        ctx: TrustedContext,
        request: ChunkProjectionRequest,
    ) -> ChunkProjectionResult: ...
