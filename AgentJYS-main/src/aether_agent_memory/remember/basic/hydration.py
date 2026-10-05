"""Fetch missing immutable bodies after the calling metadata transaction rolls back.

Only synchronous domain methods without a transaction argument are retried.
Asynchronous methods keep their external effects and hydrate in explicit phases.
The exact record and requesting identity are authorized again before any I/O.
"""

import inspect
from collections.abc import Callable
from functools import wraps
from typing import Any

from aether_agent_memory.runtime.contracts.foundation import ResourceLocation
from aether_agent_memory.runtime.contracts.models import ErrorCode, Permission, TrustedContext
from aether_agent_memory.runtime.foundation.common import FoundationError

from ..contracts.models import MemoryRef
from .service import memory_ref


class BodyReadRequiredError(Exception):
    def __init__(self, memory: MemoryRef, location: ResourceLocation) -> None:
        self.memory, self.location = memory, location
        super().__init__("immutable body must be hydrated outside transaction")


def hydrate_missing(owner: Any, ctx: TrustedContext, missing: BodyReadRequiredError) -> None:
    with owner.uow.transaction() as tx:
        owner.identity.authorize(tx, ctx, Permission.READ, memory_ref(missing.memory))
        row = tx.get(memory_ref(missing.memory, versioned=True))
        if row is None or row.get("body_location") != missing.location.model_dump(mode="json"):
            tx.abort(ErrorCode.VERSION_CONFLICT, "body location changed before hydration")
    text = owner.bodies.read_local(missing.location)
    with owner.uow.transaction() as tx:
        owner.identity.authorize(tx, ctx, Permission.READ, memory_ref(missing.memory))
        row = tx.get(memory_ref(missing.memory, versioned=True))
        if row is None or row.get("body_location") != missing.location.model_dump(mode="json"):
            tx.abort(ErrorCode.VERSION_CONFLICT, "body location changed during hydration")
    owner.bodies.remember_verified(missing.location, text)


def hydrate_method(function: Callable[..., Any]) -> Callable[..., Any]:
    signature = inspect.signature(function)

    @wraps(function)
    def call(*args: Any, **kwargs: Any) -> Any:
        arguments = signature.bind(*args, **kwargs).arguments
        instance, ctx = arguments.get("self"), arguments.get("ctx")
        owner = getattr(instance, "remember", getattr(instance, "owner", instance))
        if not isinstance(ctx, TrustedContext) or not hasattr(owner, "bodies"):
            return function(*args, **kwargs)
        seen: set[str] = set()
        while True:
            try:
                return function(*args, **kwargs)
            except BodyReadRequiredError as missing:
                key = missing.location.object_key
                if key in seen or len(seen) >= 1000:
                    raise FoundationError(
                        ErrorCode.CAPACITY_EXCEEDED, "body hydration budget exceeded"
                    ) from None
                seen.add(key)
                hydrate_missing(owner, ctx, missing)

    return call


def hydrate_metadata_reads[T: type[Any]](cls: T) -> T:
    for name in dir(cls):
        function = getattr(cls, name)
        if not inspect.isfunction(function) or inspect.iscoroutinefunction(function):
            continue
        parameters = inspect.signature(function).parameters
        if "ctx" in parameters and "self" in parameters and not {"tx", "sql"} & parameters.keys():
            setattr(cls, name, hydrate_method(function))
    return cls
