"""B owns these ports; A/C never directly mutate Remember tables."""

from typing import Literal, Protocol

from aether_agent_memory.runtime.contracts.models import (
    PageRequest,
    ScopeSelector,
    TrustedContext,
)
from aether_agent_memory.runtime.contracts.ports import Transaction

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
    RememberReceipt,
    RememberRequest,
    SourceAcquisition,
    SourceRef,
)


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
