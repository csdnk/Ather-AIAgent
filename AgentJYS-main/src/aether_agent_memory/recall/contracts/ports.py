"""A provides query orchestration and shared compute/projection mechanisms."""

from typing import Protocol

from aether_agent_memory.remember.contracts.ports import (
    GenerationProjectionPort,
    ProjectionPort,
)
from aether_agent_memory.runtime.contracts.models import RecordRef, TrustedContext
from aether_agent_memory.runtime.contracts.ports import Transaction

from .foundation import (
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
    RecallRecord,
    RecallRequest,
    VectorSearchRequest,
    VectorSearchResult,
)


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


class VectorSearchPort(Protocol):
    async def search(
        self,
        ctx: TrustedContext,
        request: VectorSearchRequest,
    ) -> VectorSearchResult: ...


class GenerationSearchPort(Protocol):
    async def search(
        self,
        ctx: TrustedContext,
        request: ChunkSearchRequest,
    ) -> ChunkSearchResult: ...


class VectorPort(ProjectionPort, VectorSearchPort, Protocol):
    """Legacy combined injection contract. New flows receive one role-specific port."""


class GenerationVectorPort(GenerationProjectionPort, GenerationSearchPort, Protocol):
    """Legacy import only; generation writes belong to B and searches to A."""
