"""Logical P2 capabilities. Implementations must supply actual provider facts."""

from typing import Protocol

from aether_agent_memory.p2.contracts import (
    P2CallContext,
    P2ContentReadInput,
    P2ContentReadResult,
    P2DeleteInput,
    P2MutationResult,
    P2OperationQueryInput,
    P2ResponseContext,
    P2SearchInput,
    P2SearchResult,
    P2TargetQueryInput,
    P2TargetState,
    P2UpsertInput,
)


class VectorSearchPort(Protocol):
    async def search(
        self, context: P2CallContext, request: P2SearchInput
    ) -> tuple[P2ResponseContext, P2SearchResult]: ...


class ContentReadPort(Protocol):
    async def read(
        self, context: P2CallContext, request: P2ContentReadInput
    ) -> tuple[P2ResponseContext, P2ContentReadResult]: ...


class VectorProjectionProviderPort(Protocol):
    async def upsert(
        self, context: P2CallContext, request: P2UpsertInput
    ) -> tuple[P2ResponseContext, P2MutationResult]: ...
    async def query_operation(
        self, context: P2CallContext, request: P2OperationQueryInput
    ) -> tuple[P2ResponseContext, P2MutationResult]: ...
    async def get_projection(
        self, context: P2CallContext, request: P2TargetQueryInput
    ) -> tuple[P2ResponseContext, P2TargetState]: ...
    async def delete_projection(
        self, context: P2CallContext, request: P2DeleteInput
    ) -> tuple[P2ResponseContext, P2MutationResult]: ...
