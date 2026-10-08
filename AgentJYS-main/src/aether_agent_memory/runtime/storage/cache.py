"""Disposable full-body cache; errors must remain distinguishable from misses."""

from typing import Protocol, runtime_checkable

from aether_agent_memory.remember.contracts.models import MemoryRef
from aether_agent_memory.runtime.contracts.foundation import ResourceLocation
from aether_agent_memory.runtime.contracts.models import Scope


class BodyCache(Protocol):
    async def get(self, scope: Scope, digest: str) -> str | None: ...
    async def put(self, scope: Scope, text: str) -> bool: ...
    async def delete(self, scope: Scope, digest: str) -> None: ...
    async def cleanup_complete(self, memory: MemoryRef, digest: str) -> bool: ...


@runtime_checkable
class CacheLocationReader(Protocol):
    """Read a registered address using trusted scope and authority bindings.

    A miss returns None. Invalid addresses, corruption and dependency failures
    remain errors for the caller's authority fallback policy. Reads never admit
    a new copy or publish an address.
    """

    async def read_location(
        self, scope: Scope, location: ResourceLocation, authority: ResourceLocation
    ) -> str | None: ...


@runtime_checkable
class CacheLocationProvider(Protocol):
    """Optional address capability; describing a location never admits a copy.

    The caller must verify admission, current generation and authorization before
    publishing it. Backends without a durable address may return None.
    """

    def describe_location(
        self, scope: Scope, digest: str, *, generation: str
    ) -> ResourceLocation | None: ...


def describe_cache_location(
    cache: BodyCache | None, scope: Scope, digest: str, *, generation: str
) -> ResourceLocation | None:
    if isinstance(cache, CacheLocationProvider):
        return cache.describe_location(scope, digest, generation=generation)
    return None
