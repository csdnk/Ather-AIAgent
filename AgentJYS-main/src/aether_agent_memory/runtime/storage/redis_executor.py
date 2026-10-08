"""Real hot replicas with PostgreSQL intents, fences and queryable receipts.

Ceph remains the authority; cold/hot describes the absence/presence of a Redis
replica. A configured authority reader is required before admitting placement
actions. Redis-only users retain cache maintenance without claiming cold storage.
All physical reads/writes occur outside metadata transactions.
"""

import asyncio
from collections.abc import Callable
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
    # Redis alone cannot prove an authoritative cold body.
    supported_moves: tuple[str, ...] = ()

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
        *,
        authority_reader: Callable[[TrustedContext, MemoryRef], MemorySnapshot] | None = None,
    ) -> None:
        if uow.backend != "postgresql":
            raise ValueError("Redis execution receipts require PostgreSQL metadata")
        self.uow, self.identity, self.memories, self.cache = uow, identity, memories, cache
        self.authority_reader = authority_reader
        self.policy_managed = authority_reader is not None
        self.supported_moves = ("promote", "demote") if self.policy_managed else ()
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
        *,
        initial: bool = False,
    ) -> tuple[TrustedContext, int | None]:
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
            if initial:
                admission = tx.read(self.table("initial_admissions"), key)
                # A completed first write is not a heat-policy decision. A save
                # replay must not revive a replica subsequently cooled/expired.
                # An unfinished write can retry only while its fence still owns
                # placement; another executor action permanently supersedes it.
                if previous and (
                    admission is None
                    or admission["state"] == "completed"
                    or admission["fence"] != previous["fence"]
                ):
                    return trusted, None
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
            if initial:
                tx.write(
                    self.table("initial_admissions"), key, {"state": "pending", "fence": fence}
                )
        return trusted, fence

    def _materialize(
        self,
        memory: MemorySnapshot,
        ctx: TrustedContext | None,
        repair_id: str | None = None,
        *,
        initial: bool = False,
    ) -> bool:
        trusted, fence = self._reserve(memory, ctx, repair_id, initial=initial)
        if fence is None:
            return self.inspect(memory.ref, memory.content_hash)
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
                if initial:
                    tx.write(
                        self.table("initial_admissions"),
                        self.key(memory.ref),
                        {"state": "completed", "fence": fence},
                    )
        except FoundationError:
            # Evicting a shared digest is safe: another memory can read its authority.
            # It cannot publish this rejected memory or lose the only durable body.
            self.cache.delete_sync(memory.ref.scope, memory.content_hash)
            raise
        return True

    def admit_initial(self, memory: MemorySnapshot, ctx: TrustedContext) -> bool:
        """Register Remember's first verified write without reversing later cooling."""
        return self._materialize(memory, ctx, initial=True)

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
            self.authorize(tx, ctx, memory, live=False, deleting=permanent)
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
            registered = self.registration_current(tx, memory, digest)
        if not registered:
            return False
        try:
            return self.cache.get_sync(memory.scope, digest) is not None
        except FoundationError as exc:
            if exc.code == ErrorCode.CONTRACT_VIOLATION:
                return False
            raise

    def registration_current(self, tx: MetadataTransaction, memory: MemoryRef, digest: str) -> bool:
        """Check placement in the caller's publication transaction, without I/O."""
        row = tx.read(self.table("copies"), self.key(memory))
        return bool(row and row["hash"] == digest)

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
            "tiers": ["cold", "hot"] if self.policy_managed else ["hot"],
            "authority": "ceph" if self.policy_managed else "unconfigured",
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
        if self.policy_managed:
            original = self._authority(ctx, memory)
            hot = self.inspect(memory, original.content_hash)
            with self.uow.transaction() as tx:
                self.authorize(tx, ctx, memory, live=True)
                epoch = tx.read(self.table("settings"), "epoch") or 0
            return PlacementObservation(
                memory=memory,
                representation_id=representation_id,
                provider_instance_id=self.instance_id,
                tier=Tier.HOT if hot else Tier.COLD,
                epoch=epoch,
                readable=True,
                content_hash=original.content_hash,
                observed_at=now(),
            )
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
        if self.policy_managed:
            return self._submit_placement(ctx, intent)
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
        feedback = ExecutionFeedback.model_validate(row["feedback"])
        if self.policy_managed and feedback.state == "running":
            # The original durable intent is resumed after lost replies/restarts.
            # Re-applying an add/remove is safe and never invents a new action ID.
            return self._apply_placement(ctx, intent)
        return feedback

    async def verify_read(self, ctx: TrustedContext, intent: ActionIntent) -> ReadProof:
        if not self.policy_managed:
            raise FoundationError(ErrorCode.INVALID_ARGUMENT, "unsupported action has no proof")
        feedback = await self.query(ctx, intent.action_id)
        observation = await self.observe(ctx, intent.decision.memory, intent.representation_id)
        if (
            feedback.state != "succeeded"
            or observation.tier != intent.decision.target_tier
            or observation.content_hash != intent.content_hash
            or not observation.readable
        ):
            raise FoundationError(ErrorCode.COMMIT_UNCONFIRMED, "placement readback differs")
        return ReadProof(
            action_id=intent.action_id,
            memory=intent.decision.memory,
            provider_instance_id=self.instance_id,
            content_hash=intent.content_hash,
            readable=True,
            verified_at=now(),
            provider_mode="real",
        )

    def _authority(self, ctx: TrustedContext, memory: MemoryRef) -> MemorySnapshot:
        with self.uow.transaction() as tx:
            self.authorize(tx, ctx, memory, live=True)
        if self.authority_reader is None:
            raise FoundationError(ErrorCode.DEPENDENCY_UNAVAILABLE, "Ceph reader missing")
        original = self.authority_reader(ctx, memory)
        if original.ref != memory or text_hash(original.content) != original.content_hash:
            raise FoundationError(ErrorCode.CONTRACT_VIOLATION, "authority identity/hash differs")
        with self.uow.transaction() as tx:
            self.authorize(tx, ctx, memory, live=True)
            guard = self.memories.final_guard(tx, ctx, (memory,), "actuate").items[0]
            if guard.checked_revision != original.object_revision:
                tx.abort(ErrorCode.VERSION_CONFLICT, "authority revision changed")
        return original

    def _submit_placement(self, ctx: TrustedContext, intent: ActionIntent) -> ExecutionFeedback:
        with self.uow.transaction() as tx:
            self.authorize(tx, ctx, intent.decision.memory, live=True)
            if (
                intent.provider_id != self.provider_id
                or intent.provider_instance_id != self.instance_id
                or intent.provider_mode != "real"
            ):
                tx.abort(ErrorCode.VERSION_CONFLICT, "cache provider binding differs")
            previous = tx.read(self.table("actions"), intent.action_id)
            if previous:
                if ActionIntent.model_validate(previous["intent"]) != intent:
                    tx.abort(ErrorCode.IDEMPOTENCY_CONFLICT, "cache action binding changed")
                feedback = ExecutionFeedback.model_validate(previous["feedback"])
                if feedback.state != "running":
                    return feedback
            else:
                epoch = tx.read(self.table("settings"), "epoch") or 0
                valid = (intent.decision.current_tier, intent.decision.target_tier) in {
                    (Tier.COLD, Tier.HOT),
                    (Tier.HOT, Tier.COLD),
                }
                reason = (
                    "unsupported_tier_transition"
                    if not valid
                    else "observation_epoch_changed"
                    if epoch != intent.expected_epoch
                    else "intent_durable"
                )
                feedback = ExecutionFeedback(
                    action_id=intent.action_id,
                    provider_instance_id=self.instance_id,
                    provider_operation_id=intent.action_id,
                    state="running" if reason == "intent_durable" else "failed",
                    observed_at=now(),
                    reason=reason,
                )
                tx.write(
                    self.table("actions"),
                    intent.action_id,
                    {
                        "intent": intent.model_dump(mode="json"),
                        "feedback": feedback.model_dump(mode="json"),
                    },
                )
                if feedback.state == "failed":
                    return feedback
        return self._apply_placement(ctx, intent)

    def _apply_placement(self, ctx: TrustedContext, intent: ActionIntent) -> ExecutionFeedback:
        # Historical receipts can contain a warm target. Never interpret these
        # as a cold demotion when recovering an operation after upgrading.
        if (intent.decision.current_tier, intent.decision.target_tier) not in {
            (Tier.COLD, Tier.HOT),
            (Tier.HOT, Tier.COLD),
        }:
            with self.uow.transaction() as tx:
                self.authorize(tx, ctx, intent.decision.memory, live=True)
                row = tx.read(self.table("actions"), intent.action_id)
                feedback = ExecutionFeedback.model_validate(row["feedback"])
                if feedback.state != "running":
                    return feedback
                feedback = feedback.model_copy(
                    update={
                        "state": "failed",
                        "reason": "unsupported_tier_transition",
                        "observed_at": now(),
                    }
                )
                tx.write(
                    self.table("actions"),
                    intent.action_id,
                    {
                        **row,
                        "feedback": feedback.model_dump(mode="json"),
                    },
                )
                return feedback
        # On any uncertain I/O failure the durable receipt remains running. Query
        # resumes this same operation; it cannot remove the Ceph original.
        original = self._authority(ctx, intent.decision.memory)
        if original.content_hash != intent.content_hash:
            raise FoundationError(ErrorCode.VERSION_CONFLICT, "action body binding changed")
        try:
            if intent.decision.target_tier == Tier.HOT:
                if not self.inspect(original.ref, original.content_hash):
                    self._materialize(original, ctx)
            else:
                # The authority was freshly read and hashed above. Purge removes
                # only optional Redis bytes; it never calls an object-store delete.
                self.purge(original.ref, permanent=False, ctx=ctx)
            observation = self.observe_sync(ctx, original.ref, intent.representation_id)
            if observation.tier != intent.decision.target_tier:
                raise FoundationError(ErrorCode.COMMIT_UNCONFIRMED, "placement not yet visible")
            feedback = ExecutionFeedback(
                action_id=intent.action_id,
                provider_instance_id=self.instance_id,
                provider_operation_id=intent.action_id,
                state="succeeded",
                observation=observation,
                observed_at=now(),
                reason="hot_replica_verified"
                if observation.tier == Tier.HOT
                else "ceph_verified_hot_removed",
            )
        except CacheCapacityError:
            feedback = ExecutionFeedback(
                action_id=intent.action_id,
                provider_instance_id=self.instance_id,
                provider_operation_id=intent.action_id,
                state="failed",
                observed_at=now(),
                reason="cache_capacity_unavailable",
            )
        with self.uow.transaction() as tx:
            row = tx.read(self.table("actions"), intent.action_id)
            if row["feedback"]["state"] != "running":
                return ExecutionFeedback.model_validate(row["feedback"])
            tx.write(
                self.table("actions"),
                intent.action_id,
                {**row, "feedback": feedback.model_dump(mode="json")},
            )
        return feedback
