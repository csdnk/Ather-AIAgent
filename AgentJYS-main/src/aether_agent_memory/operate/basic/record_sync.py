"""Publish verified Operate placement into MemoryRecord without changing its body.

Only the Redis executor adapter is supported here. Its private receipt layout is
contained in this adapter; Recall never needs to read those private tables.
"""

from __future__ import annotations

from typing import Any

from aether_agent_memory.operate.contracts.models import (
    ActionIntent,
    ActionState,
    PlacementObservation,
    Tier,
)
from aether_agent_memory.remember.basic.service import memory_ref
from aether_agent_memory.remember.contracts.foundation import MemoryRecord
from aether_agent_memory.remember.contracts.models import MemoryRef
from aether_agent_memory.runtime.contracts.models import ErrorCode
from aether_agent_memory.runtime.foundation.common import FoundationError, fingerprint
from aether_agent_memory.runtime.storage.ports import MetadataTransaction

from .cache_port import CacheExecutor


class MemoryRecordSync:
    """Metadata publication shares the transaction that concludes an action.

    PostgreSQL and Redis are not one transaction. A durable submitted/unknown
    action remains recoverable until its verified metadata is also committed.
    """

    def __init__(self, executor: CacheExecutor) -> None:
        self.executor = executor

    @staticmethod
    def key(memory: MemoryRef) -> str:
        return fingerprint([memory.scope.model_dump(mode="json"), memory.memory_id])

    @property
    def supported(self) -> bool:
        return (
            self.executor.provider_id == "redis_hot_cache"
            and self.executor.mode == "real"
            and bool(getattr(self.executor, "policy_managed", False))
        )

    def evidence(self, tx: MetadataTransaction, memory: MemoryRef) -> dict[str, Any] | None:
        """Capture before external verification; reject per-memory changes after it.

        Do not fence on the executor's global epoch: unrelated memories may be
        promoted concurrently. The memory's own fence and registration suffice.
        """
        if not self.supported:
            return None
        executor: Any = self.executor
        record = self.record(tx, memory)
        key = executor.key(memory)
        return {
            "instance": tx.read(executor.table("settings"), "instance"),
            "fence": tx.read(executor.table("fences"), key),
            "copy": tx.read(executor.table("copies"), key),
            "body_location": record["body_location"] if record else None,
        }

    @staticmethod
    def record(tx: MetadataTransaction, memory: MemoryRef) -> dict[str, Any] | None:
        raw: dict[str, Any] | None = tx.get(memory_ref(memory, versioned=True))
        # Old inline MemorySnapshot fixtures/contracts have no location to patch.
        return raw if raw and "body_location" in raw else None

    def reserve(self, tx: MetadataTransaction, intent: ActionIntent) -> None:
        if not self.supported or self.record(tx, intent.decision.memory) is None:
            return
        key = self.key(intent.decision.memory)
        head = tx.read("operate_cache_sync_heads", key)
        if head and head["action_id"] == intent.action_id:
            return
        if head and head["state"] == "pending":
            previous = tx.read("operate_actions", head["action_id"])
            if previous and previous["state"] in {"generated", "submitted", "unknown"}:
                tx.abort(ErrorCode.REQUEST_IN_PROGRESS, "prior cache metadata is not confirmed")
        tx.write(
            "operate_cache_sync_heads",
            key,
            {
                "action_id": intent.action_id,
                "memory": intent.decision.memory.model_dump(mode="json"),
                "state": "pending",
            },
        )

    def conclude(self, tx: MetadataTransaction, intent: ActionIntent, state: ActionState) -> None:
        key = self.key(intent.decision.memory)
        head = tx.read("operate_cache_sync_heads", key)
        if head and head["action_id"] == intent.action_id and state != ActionState.UNKNOWN:
            tx.write("operate_cache_sync_heads", key, {**head, "state": state.value})

    def publish(
        self,
        tx: MetadataTransaction,
        observation: PlacementObservation,
        evidence: dict[str, Any] | None,
        *,
        action: ActionIntent | None = None,
    ) -> str:
        memory = observation.memory
        raw = self.record(tx, memory)
        if raw is None:
            return "legacy_inline"
        if not self.supported:
            raise FoundationError(
                ErrorCode.COMMIT_UNCONFIRMED, "cache location adapter unavailable"
            )
        current = tx.read("remember_current", memory.memory_id)
        ref = memory_ref(memory, versioned=True)
        if (
            current != ref.model_dump(mode="json")
            or raw["ref"] != memory.model_dump(mode="json")
            or raw["status"] != "active"
            or raw["body_location"]["content_hash"] != observation.content_hash
        ):
            return "superseded"
        head = tx.read("operate_cache_sync_heads", self.key(memory))
        if action is not None:
            if not head or head["action_id"] != action.action_id:
                return "superseded"
            if action.content_hash != observation.content_hash:
                return "superseded"
        elif head and head["state"] == "pending":
            return "pending_action"
        if (
            not observation.readable
            or observation.representation_id != "original"
            or observation.provider_instance_id != self.executor.instance_id
            or observation.tier not in {Tier.COLD, Tier.HOT}
            or evidence is None
            or evidence != self.evidence(tx, memory)
            or evidence["instance"] != self.executor.instance_id
        ):
            raise FoundationError(
                ErrorCode.COMMIT_UNCONFIRMED, "cache evidence changed before publish"
            )
        location = None
        if observation.tier == Tier.HOT:
            copy = evidence["copy"]
            if not copy or (
                copy["memory"] != memory.model_dump(mode="json")
                or copy["hash"] != observation.content_hash
                or copy["tier"] != "hot"
            ):
                raise FoundationError(ErrorCode.COMMIT_UNCONFIRMED, "hot copy registration missing")
            executor: Any = self.executor
            cache = executor.cache
            location = cache.describe_location(
                memory.scope,
                observation.content_hash,
                generation=raw["body_location"]["generation"],
            ).model_dump(mode="json")
        elif action is not None and evidence["copy"] is not None:
            raise FoundationError(ErrorCode.COMMIT_UNCONFIRMED, "demotion registration not removed")
        self.patch(tx, memory, raw, location)
        # One compact projection per scoped memory, not an ever-growing event log.
        binding = {
            "memory": memory.model_dump(mode="json"),
            "content_hash": observation.content_hash,
            "cache_location": location,
            "provider_instance_id": observation.provider_instance_id,
            "action_id": action.action_id if action else None,
            "verified_at": observation.observed_at,
        }
        tx.write("operate_cache_bindings", self.key(memory), binding)
        return "synced"

    @staticmethod
    def patch(
        tx: MetadataTransaction,
        memory: MemoryRef,
        raw: dict[str, Any],
        location: dict[str, Any] | None,
    ) -> None:
        if raw.get("cache_location") == location:
            return
        updated = {**raw, "cache_location": location}
        MemoryRecord.model_validate(updated)
        ref = memory_ref(memory, versioned=True)
        # CAS the latest complete row, preserving all Remember-owned fields.
        # Cache placement changes neither semantic versions nor access statistics.
        tx.put_if_revision(ref, updated, tx.revision(ref))

    def clear_inactive(self, tx: MetadataTransaction, memory: MemoryRef) -> None:
        """Caller guards cleanup permission and current ineligibility after purge."""
        raw = self.record(tx, memory)
        if raw is None or raw["ref"] != memory.model_dump(mode="json"):
            return
        self.patch(tx, memory, raw, None)
        binding = tx.read("operate_cache_bindings", self.key(memory))
        if binding and binding["memory"] == memory.model_dump(mode="json"):
            tx.raw.delete("p3_rf_operate_cache_bindings", "system", self.key(memory))
