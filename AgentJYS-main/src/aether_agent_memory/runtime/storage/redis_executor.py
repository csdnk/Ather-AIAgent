"""Real hot replicas with PostgreSQL intents, fences and queryable receipts.

This provider exposes only Redis hot copies. It never labels a directory or a
Ceph authority object as an unconfigured warm/cold tier. Such movements have a
stable failed receipt. Authority and lifecycle decisions stay behind MemoryReadPort.
All Redis calls occur outside metadata transactions.
"""

import asyncio
from typing import Any

from aether_agent_memory.operate.basic.cache_port import CacheCapacityError, CacheCopy
from aether_agent_memory.operate.contracts.models import (
    ActionIntent,
    ExecutionFeedback,
    PlacementObservation,
    ReadProof,
    ResourceSnapshot,
    Tier,
)
from aether_agent_memory.remember.basic.service import memory_ref
from aether_agent_memory.remember.contracts.models import MemoryRef, MemorySnapshot
from aether_agent_memory.remember.contracts.ports import MemoryReadPort
from aether_agent_memory.runtime.contracts.models import (
    ErrorCode,
    Permission,
    Scope,
    TrustedContext,
)
from aether_agent_memory.runtime.foundation.common import FoundationError, fingerprint, now
from aether_agent_memory.runtime.foundation.identity import Identity
from aether_agent_memory.runtime.foundation.requests import text_hash
from aether_agent_memory.runtime.storage.ports import MetadataTransaction, MetadataUnitOfWork

from .redis_cache import RedisCache


class RedisExecutor:
    provider_id = "redis_hot_cache"
    mode = "real"
    # Static admission capability: querying Redis cannot enable an absent tier.
    supported_moves: tuple = ()

    @property
    def capacity(self) -> int:
        """Per-scope body quota shared by all executor and read-cache writers."""
        return min(self.cache.capacity_bytes, self.cache.policy.cache_scope_bytes)

    @capacity.setter
    def capacity(self, value: int) -> None:
        if type(value) is not int or value < 1:
            raise ValueError("Redis cache capacity must be positive bytes")
        self.cache.capacity_bytes = value

    def __init__(
        self,
        uow: MetadataUnitOfWork,
        identity: Identity,
        memories: MemoryReadPort,
        cache: RedisCache,
    ) -> None:
        if uow.backend != "postgresql":
            raise ValueError("Redis execution receipts require PostgreSQL metadata")
        self.uow, self.identity, self.memories, self.cache = uow, identity, memories, cache
        self.capacity = cache.policy.cache_scope_bytes
        self.prefix = "redis_" + fingerprint(cache.namespace)[:24]
        self.instance_id = fingerprint([self.provider_id, cache.namespace, cache.resource_id])
        with self.uow.transaction() as tx:
            previous = tx.read(self.table("settings"), "instance")
            if previous is not None and previous != self.instance_id:
                tx.abort(ErrorCode.VERSION_CONFLICT, "Redis resource changed; migration required")
            if previous is None:
                tx.write(self.table("settings"), "instance", self.instance_id)

    def table(self, kind: str) -> str:
        return self.prefix + "_" + kind

    @staticmethod
    def key(memory: MemoryRef) -> str:
        return fingerprint(memory.model_dump(mode="json"))

    @staticmethod
    def group(memory: MemoryRef) -> str:
        return fingerprint([memory.scope.model_dump(mode="json"), memory.memory_id])

    def authorize(
        self,
        tx: MetadataTransaction,
        ctx: TrustedContext | None,
        memory: MemoryRef,
        *,
        live: bool,
        deleting: bool = False,
    ) -> TrustedContext:
        if ctx is None:
            tx.abort(ErrorCode.UNAUTHENTICATED, "cache operation requires trusted context")
        self.identity.authorize(
            tx, ctx, Permission.DELETE if deleting else Permission.READ, memory_ref(memory)
        )
        if live:
            tombstone = tx.read(self.table("tombstones"), self.group(memory)) or 0
            if memory.version <= tombstone:
                tx.abort(ErrorCode.MEMORY_GONE, "cache version is permanently fenced")
            eligible = self.memories.final_guard(tx, ctx, (memory,), "actuate").items[0]
            if eligible.decision != "allowed":
                tx.abort(ErrorCode.MEMORY_GONE, "cache memory is no longer eligible")
        return ctx

    def _reserve(
        self,
        memory: MemorySnapshot,
        ctx: TrustedContext | None,
        repair_id: str | None,
    ) -> tuple[TrustedContext, int]:
        if text_hash(memory.content) != memory.content_hash:
            raise FoundationError(ErrorCode.CONTRACT_VIOLATION, "cache input hash differs")
        key = self.key(memory.ref)
        with self.uow.transaction() as tx:
            trusted = self.authorize(tx, ctx, memory.ref, live=True)
            guarded = self.memories.final_guard(tx, trusted, (memory.ref,), "actuate").items[0]
            if guarded.checked_revision != memory.object_revision:
                tx.abort(ErrorCode.VERSION_CONFLICT, "cache snapshot revision changed")
            previous = tx.read(self.table("fences"), key)
            if previous and previous["hash"] != memory.content_hash:
                tx.abort(ErrorCode.IDEMPOTENCY_CONFLICT, "cache version bytes changed")
            if repair_id:
                if tx.read(self.table("copies"), key) is None:
                    tx.abort(ErrorCode.MEMORY_GONE, "repair target was removed")
                binding = {"memory": memory.ref.model_dump_json(), "hash": memory.content_hash}
                old = tx.read(self.table("repairs"), repair_id)
                if old is not None and old != binding:
                    tx.abort(ErrorCode.IDEMPOTENCY_CONFLICT, "repair operation binding changed")
                if old is None:
                    tx.write(self.table("repairs"), repair_id, binding)
            fence = (previous["fence"] if previous else 0) + 1
            tx.write(
                self.table("fences"),
                key,
                {
                    "memory": memory.ref.model_dump(mode="json"),
                    "hash": memory.content_hash,
                    "fence": fence,
                },
            )
        return trusted, fence

    def _materialize(
        self,
        memory: MemorySnapshot,
        ctx: TrustedContext | None,
        repair_id: str | None = None,
    ) -> None:
        trusted, fence = self._reserve(memory, ctx, repair_id)
        try:
            admitted = self.cache.put_sync(memory.ref.scope, memory.content)
            if not admitted:
                raise CacheCapacityError("Redis scope capacity exhausted")
            if self.cache.get_sync(memory.ref.scope, memory.content_hash) != memory.content:
                raise FoundationError(ErrorCode.COMMIT_UNCONFIRMED, "cache write not readable")
        except CacheCapacityError:
            raise
        except FoundationError as exc:
            if exc.code == ErrorCode.CONTRACT_VIOLATION:
                raise
            raise FoundationError(
                ErrorCode.COMMIT_UNCONFIRMED, "cache effect awaits readback"
            ) from None
        except Exception:
            raise FoundationError(
                ErrorCode.COMMIT_UNCONFIRMED, "cache effect awaits readback"
            ) from None
        try:
            with self.uow.transaction() as tx:
                self.authorize(tx, trusted, memory.ref, live=True)
                row = tx.read(self.table("fences"), self.key(memory.ref))
                if row is None or row["fence"] != fence:
                    tx.abort(ErrorCode.VERSION_CONFLICT, "cache publication fence changed")
                guarded = self.memories.final_guard(tx, trusted, (memory.ref,), "actuate").items[0]
                if guarded.checked_revision != memory.object_revision:
                    tx.abort(ErrorCode.VERSION_CONFLICT, "cache snapshot changed during write")
                tx.write(
                    self.table("copies"),
                    self.key(memory.ref),
                    {
                        "memory": memory.ref.model_dump(mode="json"),
                        "hash": memory.content_hash,
                        "tier": Tier.HOT.value,
                    },
                )
                epoch = tx.read(self.table("settings"), "epoch") or 0
                tx.write(self.table("settings"), "epoch", epoch + 1)
        except FoundationError:
            # Evicting a shared digest is safe: another memory can read its authority.
            # It cannot publish this rejected memory or lose the only durable body.
            self.cache.delete_sync(memory.ref.scope, memory.content_hash)
            raise

    def ensure(self, memory: MemorySnapshot, ctx: TrustedContext | None = None) -> None:
        self._materialize(memory, ctx)

    def repair(
        self,
        memory: MemorySnapshot,
        operation_id: str,
        ctx: TrustedContext | None = None,
    ) -> None:
        self._materialize(memory, ctx, operation_id)

    def repair_record(self, operation_id: str) -> tuple[str, str] | None:
        # This proves the original intent only. The maintenance caller independently
        # checks current eligibility and Redis bytes before accepting recovery.
        with self.uow.transaction() as tx:
            row = tx.read(self.table("repairs"), operation_id)
        return (row["memory"], row["hash"]) if row else None

    def _matching(self, tx: MetadataTransaction, memory: MemoryRef) -> list[tuple[str, Any]]:
        return [
            (key, row)
            for key, row in tx.rows(self.table("fences"))
            if MemoryRef.model_validate(row["memory"]).scope == memory.scope
            and row["memory"]["memory_id"] == memory.memory_id
            and row["memory"]["version"] <= memory.version
        ]

    def purge(
        self,
        memory: MemoryRef,
        *,
        permanent: bool,
        ctx: TrustedContext | None = None,
    ) -> None:
        with self.uow.transaction() as tx:
            self.authorize(tx, ctx, memory, live=False, deleting=True)
            if permanent:
                tombstone = tx.read(self.table("tombstones"), self.group(memory)) or 0
                tx.write(
                    self.table("tombstones"), self.group(memory), max(memory.version, tombstone)
                )
            rows = self._matching(tx, memory)
            for key, row in rows:
                tx.write(self.table("fences"), key, {**row, "fence": row["fence"] + 1})
                tx.raw.delete("p3_rf_" + self.table("copies"), "system", key)
            epoch = tx.read(self.table("settings"), "epoch") or 0
            tx.write(self.table("settings"), "epoch", epoch + 1)
        for _, row in rows:
            self.cache.delete_sync(memory.scope, row["hash"])
        if not self.cleanup_complete(memory, permanent=permanent):
            raise FoundationError(ErrorCode.COMMIT_UNCONFIRMED, "cache purge awaits readback")

    def copies(
        self,
        cursor: str = "",
        limit: int = 16,
        *,
        digest: str | None = None,
    ) -> list[CacheCopy]:
        with self.uow.transaction() as tx:
            rows = tx.rows_after(self.table("copies"), cursor, limit=limit)
        return [
            CacheCopy(key, MemoryRef.model_validate(row["memory"]), Tier.HOT, row["hash"])
            for key, row in rows
            if digest is None or row["hash"] == digest
        ]

    def inspect(self, memory: MemoryRef, digest: str) -> bool:
        with self.uow.transaction() as tx:
            row = tx.read(self.table("copies"), self.key(memory))
        if not row or row["hash"] != digest:
            return False
        try:
            return self.cache.get_sync(memory.scope, digest) is not None
        except FoundationError as exc:
            if exc.code == ErrorCode.CONTRACT_VIOLATION:
                return False
            raise

    def cleanup_complete(self, memory: MemoryRef, *, permanent: bool) -> bool:
        with self.uow.transaction() as tx:
            rows = self._matching(tx, memory)
            if any(tx.read(self.table("copies"), key) is not None for key, _ in rows):
                return False
        return all(self.cache.raw_sync(memory.scope, row["hash"]) is None for _, row in rows)

    def read_cached(self, scope: Scope, digest: str) -> str | None:
        return self.cache.get_sync(scope, digest)

    def probe(self) -> dict[str, object]:
        self.cache.check_connection()
        return {
            "state": "available",
            "provider": self.provider_id,
            "instance_id": self.instance_id,
            "tiers": ["hot"],
            "receipt_backend": "postgresql",
        }

    async def resources(self, ctx: TrustedContext) -> ResourceSnapshot:
        return await asyncio.to_thread(self.resources_sync, ctx)

    def resources_sync(self, ctx: TrustedContext) -> ResourceSnapshot:
        with self.uow.transaction() as tx:
            self.identity.revalidate(tx, ctx)
            epoch = tx.read(self.table("settings"), "epoch") or 0
        used = self.cache.usage_sync(ctx.principal.home_scope)
        return ResourceSnapshot(
            provider_id=self.provider_id,
            provider_instance_id=self.instance_id,
            epoch=epoch,
            available_bytes=max(0, min(self.capacity, self.cache.policy.cache_scope_bytes) - used),
            observed_at=now(),
            supported_moves=self.supported_moves,
        )

    async def observe(
        self,
        ctx: TrustedContext,
        memory: MemoryRef,
        representation_id: str,
    ) -> PlacementObservation:
        return await asyncio.to_thread(self.observe_sync, ctx, memory, representation_id)

    def observe_sync(
        self, ctx: TrustedContext, memory: MemoryRef, representation_id: str
    ) -> PlacementObservation:
        with self.uow.transaction() as tx:
            self.authorize(tx, ctx, memory, live=True)
            row = tx.read(self.table("copies"), self.key(memory))
            epoch = tx.read(self.table("settings"), "epoch") or 0
            if row is None:
                tx.abort(ErrorCode.NOT_FOUND, "cache copy is not registered")
        readable = self.inspect(memory, row["hash"])
        return PlacementObservation(
            memory=memory,
            representation_id=representation_id,
            provider_instance_id=self.instance_id,
            tier=Tier.HOT,
            epoch=epoch,
            readable=readable,
            content_hash=row["hash"],
            observed_at=now(),
        )

    async def submit(self, ctx: TrustedContext, intent: ActionIntent) -> ExecutionFeedback:
        return await asyncio.to_thread(self.submit_sync, ctx, intent)

    def submit_sync(self, ctx: TrustedContext, intent: ActionIntent) -> ExecutionFeedback:
        with self.uow.transaction() as tx:
            self.authorize(tx, ctx, intent.decision.memory, live=True)
            previous = tx.read(self.table("actions"), intent.action_id)
            if previous:
                if ActionIntent.model_validate(previous["intent"]) != intent:
                    tx.abort(ErrorCode.IDEMPOTENCY_CONFLICT, "cache action binding changed")
                return ExecutionFeedback.model_validate(previous["feedback"])
            if (
                intent.provider_id != self.provider_id
                or intent.provider_instance_id != self.instance_id
            ):
                tx.abort(ErrorCode.VERSION_CONFLICT, "cache provider binding differs")
            feedback = ExecutionFeedback(
                action_id=intent.action_id,
                provider_instance_id=self.instance_id,
                provider_operation_id=intent.action_id,
                state="failed",
                observed_at=now(),
                reason="unsupported_tier_transition",
            )
            tx.write(
                self.table("actions"),
                intent.action_id,
                {
                    "intent": intent.model_dump(mode="json"),
                    "feedback": feedback.model_dump(mode="json"),
                },
            )
        return feedback

    async def query(self, ctx: TrustedContext, action_id: str) -> ExecutionFeedback:
        return await asyncio.to_thread(self.query_sync, ctx, action_id)

    def query_sync(self, ctx: TrustedContext, action_id: str) -> ExecutionFeedback:
        with self.uow.transaction() as tx:
            self.identity.revalidate(tx, ctx)
            row = tx.read(self.table("actions"), action_id)
            if row is None:
                return ExecutionFeedback(
                    action_id=action_id,
                    provider_instance_id=self.instance_id,
                    state="not_found",
                    observed_at=now(),
                )
            intent = ActionIntent.model_validate(row["intent"])
            self.authorize(tx, ctx, intent.decision.memory, live=False)
        return ExecutionFeedback.model_validate(row["feedback"])

    async def verify_read(self, ctx: TrustedContext, intent: ActionIntent) -> ReadProof:
        await self.query(ctx, intent.action_id)
        raise FoundationError(
            ErrorCode.INVALID_ARGUMENT, "unsupported action has no movement proof"
        )
