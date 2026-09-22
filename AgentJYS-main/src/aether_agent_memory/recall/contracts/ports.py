"""A provides query orchestration and shared compute/projection mechanisms."""

from typing import Protocol

from aether_agent_memory.runtime.contracts.models import RecordRef, TrustedContext
from aether_agent_memory.runtime.contracts.ports import Transaction

from .foundation import (
    ChunkProjectionRequest,
    ChunkProjectionResult,
    ChunkSearchRequest,
    ChunkSearchResult,
    ContextAssemblyPlan,
    ContextCommitRequest,
    MemorySearchRequest,
    MemorySearchResult,
    RecallPlanRequest,
)
from .models import (
    ContextPack,
    EmbeddingRequest,
    EmbeddingResult,
    ProjectionRequest,
    ProjectionResult,
    ProjectionTarget,
    RecallRecord,
    RecallRequest,
    VectorSearchRequest,
    VectorSearchResult,
)


class GenerationVectorPort(Protocol):
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
    async def search(
        self,
        ctx: TrustedContext,
        request: ChunkSearchRequest,
    ) -> ChunkSearchResult: ...


class MemoryCandidatePort(Protocol):
    async def search(
        self,
        ctx: TrustedContext,
        request: MemorySearchRequest,
    ) -> MemorySearchResult: ...


class ContextAssemblyPort(Protocol):
    async def plan(
        self,
        ctx: TrustedContext,
        request: RecallPlanRequest,
    ) -> ContextAssemblyPlan: ...
    def commit(
        self,
        tx: Transaction,
        ctx: TrustedContext,
        request: ContextCommitRequest,
    ) -> RecordRef: ...


class RecallPort(Protocol):
    async def recall(self, ctx: TrustedContext, request: RecallRequest) -> ContextPack: ...
    def status(self, ctx: TrustedContext, recall_id: str) -> RecallRecord: ...
    def result(self, ctx: TrustedContext, recall_id: str) -> ContextPack: ...


class EmbeddingPort(Protocol):
    async def embed(
        self,
        ctx: TrustedContext,
        request: EmbeddingRequest,
    ) -> EmbeddingResult: ...


class VectorPort(Protocol):
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
    async def search(
        self,
        ctx: TrustedContext,
        request: VectorSearchRequest,
    ) -> VectorSearchResult: ...
