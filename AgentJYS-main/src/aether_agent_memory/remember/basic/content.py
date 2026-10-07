"""Immutable complete bodies; explicit object authority and bounded Redis replicas."""

import asyncio
import os
import secrets
from collections import OrderedDict
from pathlib import Path
from typing import Any

from aether_agent_memory.runtime.contracts.foundation import ResourceLocation
from aether_agent_memory.runtime.contracts.models import (
    ErrorCode,
    Scope,
    TrustedContext,
)
from aether_agent_memory.runtime.foundation.common import FoundationError, fingerprint
from aether_agent_memory.runtime.foundation.requests import text_hash
from aether_agent_memory.runtime.storage.cache import BodyCache

from .policy import RememberPolicy


class Bodies:
    def __init__(
        self,
        root: Path,
        policy: RememberPolicy,
        *,
        p2: Any = None,
        cache: BodyCache | None = None,
    ) -> None:
        self.root, self.policy, self.p2, self.cache = root, policy, p2, cache
        binding = getattr(p2, "binding", None)
        self.object_binding = binding() if callable(binding) else None
        self.provider_id = (
            str(self.object_binding["provider"])
            if self.object_binding
            else ("p2" if p2 is not None else "local")
        )
        self.provider_instance_id = (
            fingerprint(self.object_binding) if self.object_binding else "remember"
        )
        self.root.mkdir(parents=True, exist_ok=True)
        self.prepared: dict[str, ResourceLocation] = {}
        self.require_prepared = False
        self.remote_only = False
        self.verified: OrderedDict[str, str] = OrderedDict()
        self.verified_bytes = 0
        self.previews: OrderedDict[tuple[str, int], str] = OrderedDict()
        self.verified_limit = max(policy.max_input_bytes * 2, 128 * 1024 * 1024)

    def remember_verified(self, location: ResourceLocation, text: str) -> None:
        self.check_binding(location)
        if text_hash(text) != location.content_hash:
            raise FoundationError(ErrorCode.CONTRACT_VIOLATION, "body verification differs")
        key = location.object_key
        prior = self.verified.pop(key, None)
        if prior is not None:
            self.verified_bytes -= len(prior.encode("utf-8"))
        self.verified[key] = text
        self.verified_bytes += len(text.encode("utf-8"))
        while self.verified_bytes > self.verified_limit and len(self.verified) > 1:
            _, removed = self.verified.popitem(last=False)
            self.verified_bytes -= len(removed.encode("utf-8"))

    def verified_text(self, location: ResourceLocation) -> str | None:
        self.check_binding(location)
        value = self.verified.get(location.object_key)
        if value is not None:
            self.verified.move_to_end(location.object_key)
        return value

    def preview_text(self, location: ResourceLocation, max_chars: int) -> str | None:
        self.check_binding(location)
        key = (fingerprint(location.model_dump(mode="json")), max_chars)
        value = self.previews.get(key)
        if value is not None:
            self.previews.move_to_end(key)
        return value

    def remember_preview(self, location: ResourceLocation, max_chars: int, text: str) -> None:
        # Bounded immutable excerpts only; callers must still recheck authorization,
        # current revision and source eligibility before using any cached content.
        if not 1 <= max_chars <= 500 or len(text) > max_chars:
            raise ValueError("preview cache bound exceeded")
        self.check_binding(location)
        key = (fingerprint(location.model_dump(mode="json")), max_chars)
        self.previews[key] = text
        self.previews.move_to_end(key)
        while len(self.previews) > 512:
            self.previews.popitem(last=False)

    def location(self, scope: Scope, text: str, kind: str = "body") -> ResourceLocation:
        digest = text_hash(text)
        key = fingerprint([scope.model_dump(mode="json"), digest])
        return ResourceLocation(
            kind=kind,
            provider_id=self.provider_id,
            provider_instance_id=self.provider_instance_id,
            namespace="remember_bodies",
            object_key="remember/bodies/" + key,
            generation=digest,
            content_hash=digest,
        )

    def path(self, location: ResourceLocation) -> Path:
        # Provider keys are never interpreted as local paths.
        return self.root / fingerprint(location.model_dump(mode="json", exclude={"kind"}))

    def stage(self, scope: Scope, text: str) -> ResourceLocation:
        location = self.location(scope, text)
        key = location.object_key
        if self.require_prepared and self.p2 and key not in self.prepared:
            raise FoundationError(ErrorCode.COMMIT_UNCONFIRMED, "P2 body needs stage verification")
        if self.p2 and hasattr(self.p2, "put_object_sync") and key not in self.prepared:
            self.p2.put_object_sync(key, text.encode("utf-8"))
            if self.p2.get_object_sync(key) != text.encode("utf-8"):
                raise FoundationError(ErrorCode.COMMIT_UNCONFIRMED, "P2 body readback differs")
            self.prepared[key] = location
        if self.p2 and key not in self.prepared:
            raise FoundationError(ErrorCode.COMMIT_UNCONFIRMED, "P2 body not verified")
        self.remember_verified(location, text)
        if not self.remote_only:
            self._spool(location, text)
        return location

    def _spool(self, location: ResourceLocation, text: str) -> None:
        self.remember_verified(location, text)
        if self.remote_only:
            return
        path = self.path(location)
        if path.exists():
            try:
                if self.read_local(location) == text:
                    return
            except FoundationError:
                pass
        if text_hash(text) != location.content_hash:
            raise FoundationError(ErrorCode.CONTRACT_VIOLATION, "immutable body changed")
        temporary = path.with_suffix("." + secrets.token_hex(8) + ".tmp")
        data = text.encode("utf-8")
        try:
            with temporary.open("xb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            try:
                os.replace(temporary, path)
            except PermissionError:
                # Windows may deny replacement while another delivery reads the
                # same immutable file. Only an exact already-published copy is success.
                if not path.is_file() or path.read_bytes() != data:
                    raise
        finally:
            temporary.unlink(missing_ok=True)

    async def persist(self, ctx: TrustedContext, scope: Scope, text: str) -> ResourceLocation:
        location = self.location(scope, text)
        if self.p2:
            raw = await self.p2_call("get_object", location.object_key)
            if raw is None:
                await self.p2_call("put_object", location.object_key, text.encode("utf-8"))
                raw = await self.p2_call("get_object", location.object_key)
            if raw != text.encode("utf-8"):
                raise FoundationError(ErrorCode.COMMIT_UNCONFIRMED, "P2 exact body not readable")
        self.prepared[location.object_key] = location
        self.remember_verified(location, text)
        if not self.remote_only:
            await asyncio.to_thread(self._spool, location, text)
        return location

    async def p2_call(self, method: str, *args: Any) -> Any:
        try:
            return await getattr(self.p2, method)(*args)
        except FoundationError:
            raise
        except Exception as exc:
            raise FoundationError(
                ErrorCode.DEPENDENCY_UNAVAILABLE, "P2 body operation unavailable"
            ) from exc

    def read_local(self, location: ResourceLocation) -> str:
        self.check_binding(location)
        try:
            if location.provider_id != "local":
                if self.p2 is None or not hasattr(self.p2, "get_object_sync"):
                    raise OSError("P2 body reader missing")
                raw = self.p2.get_object_sync(location.object_key)
                if raw is None:
                    raise OSError("P2 body missing")
                text = bytes(raw).decode("utf-8")
            else:
                text = self.path(location).read_bytes().decode("utf-8")
        except FoundationError:
            raise
        except Exception:
            raise FoundationError(
                ErrorCode.DEPENDENCY_UNAVAILABLE, "body authority unavailable"
            ) from None
        if text_hash(text) != location.content_hash:
            raise FoundationError(ErrorCode.CONTRACT_VIOLATION, "body hash mismatch")
        return text

    async def read(self, scope: Scope, location: ResourceLocation) -> tuple[str, str]:
        self.check_binding(location)
        if self.cache:
            try:
                value = await self.cache.get(scope, location.content_hash)
                if value is not None:
                    if text_hash(value) != location.content_hash:
                        raise FoundationError(ErrorCode.CONTRACT_VIOLATION, "cache hash differs")
                    self.remember_verified(location, value)
                    if not self.remote_only:
                        await asyncio.to_thread(self._spool, location, value)
                    return value, "cache"
            except Exception:
                pass  # Cache loss cannot erase a durably saved source.
        if location.provider_id != "local":
            if self.p2 is None:
                raise FoundationError(ErrorCode.DEPENDENCY_UNAVAILABLE, "P2 body reader missing")
            raw = await self.p2_call("get_object", location.object_key)
            if raw is None:
                raise FoundationError(ErrorCode.NOT_FOUND, "P2 body missing")
            value = raw.decode("utf-8")
            if text_hash(value) != location.content_hash:
                raise FoundationError(ErrorCode.CONTRACT_VIOLATION, "P2 body hash mismatch")
            self.remember_verified(location, value)
            if not self.remote_only:
                await asyncio.to_thread(self._spool, location, value)
            return value, "p2" if location.provider_id == "p2" else "authority"
        return await asyncio.to_thread(self.read_local, location), "authority"

    def check_binding(self, location: ResourceLocation) -> None:
        if self.object_binding is not None and (
            location.provider_id != self.provider_id
            or location.provider_instance_id != self.provider_instance_id
        ):
            raise FoundationError(
                ErrorCode.VERSION_CONFLICT, "body provider changed; explicit migration required"
            )
        if self.object_binding is None and location.provider_id not in {"local", "p2"}:
            raise FoundationError(ErrorCode.VERSION_CONFLICT, "body provider is not configured")

    async def admit(self, scope: Scope, text: str) -> str:
        if not self.cache:
            return "disabled"
        try:
            return "cached" if await self.cache.put(scope, text) else "not_admitted"
        except Exception:
            return "unavailable"
