"""A provides query orchestration and shared compute/projection mechanisms."""

from typing import Protocol

from aether_agent_memory.runtime.contracts.models import TrustedContext

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
