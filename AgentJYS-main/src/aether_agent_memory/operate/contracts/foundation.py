"""C scheduling inputs and versioned migration handoff contracts."""

from __future__ import annotations

from typing import Literal, Self

from pydantic import Field, model_validator

from aether_agent_memory.remember.contracts.foundation import (
    ProtectionReference,
    ReferenceHandoffReceipt,
)
from aether_agent_memory.remember.contracts.models import MemoryRef
from aether_agent_memory.runtime.contracts.foundation import ResourceLocation
from aether_agent_memory.runtime.contracts.models import (
    ContractModel,
    Count,
    Identifier,
    Positive,
    Scope,
    Timestamp,
)

from .models import ActionIntent, Tier


class SchedulingRound(ContractModel):
    round_id: Identifier
    scope: Scope
    task_id: Identifier
    policy_version: Identifier
    storage_watermark: Count
    access_watermark: Count
    coverage: Literal["complete", "partial", "unknown"]
    max_actions: Positive
    action_ids: tuple[Identifier, ...]
    started_at: Timestamp
    deadline_at: Timestamp

    @model_validator(mode="after")
    def bounded_round(self) -> Self:
        if (
            len(set(self.action_ids)) != len(self.action_ids)
            or len(self.action_ids) > self.max_actions
        ):
            raise ValueError("round actions must be unique and bounded")
        if self.coverage != "complete" and self.action_ids:
            raise ValueError("incomplete observations cannot authorize placement actions")
        if self.deadline_at <= self.started_at:
            raise ValueError("round deadline must follow its start")
        return self


class BufferEntry(ContractModel):
    memory: MemoryRef
    object_revision: Positive
    revision: Positive
    tier: Tier
    location: ResourceLocation
    content_bytes: Positive
    successful_reads: Count
    last_read_at: Timestamp | None = None
    storage_watermark: Count
    access_watermark: Count
    protection_ids: tuple[Identifier, ...]
    observed_at: Timestamp

    @model_validator(mode="after")
    def read_evidence(self) -> Self:
        if self.successful_reads and self.last_read_at is None:
            raise ValueError("read heat requires a successful full-body read timestamp")
        if self.last_read_at is not None and self.last_read_at > self.observed_at:
            raise ValueError("future reads cannot contribute heat")
        if self.location.kind not in {"body", "vector", "cache"}:
            raise ValueError("buffer entry requires a schedulable resource")
        if len(set(self.protection_ids)) != len(self.protection_ids):
            raise ValueError("duplicate protection reference")
        return self


class MigrationPlan(ContractModel):
    intent: ActionIntent
    expected_object_revision: Positive
    source: ResourceLocation
    target: ResourceLocation
    protection: ProtectionReference
    handoff: ReferenceHandoffReceipt | None = None
    old_copy_cleanup: Literal["blocked", "eligible", "completed", "failed", "unknown"]
    cleanup_evidence_ids: tuple[Identifier, ...] = Field(default=())

    @model_validator(mode="after")
    def migration_binding(self) -> Self:
        memory = self.intent.decision.memory
        if (
            self.source == self.target
            or self.source.kind not in {"body", "vector", "cache"}
            or self.source.kind != self.target.kind
            or self.source.content_hash != self.intent.content_hash
            or self.target.content_hash != self.intent.content_hash
            or self.source.generation != self.target.generation
            or self.target.provider_id != self.intent.provider_id
            or self.target.provider_instance_id != self.intent.provider_instance_id
            or self.protection.memory != memory
            or self.protection.location != self.source
            or self.protection.owner_action_id != self.intent.action_id
        ):
            raise ValueError(
                "migration must bind one action, protected source and equivalent target"
            )
        if self.handoff is not None:
            req = self.handoff.request
            if (
                req.action_id != self.intent.action_id
                or req.memory != memory
                or req.old_location != self.source
                or req.new_location != self.target
                or req.expected_object_revision != self.expected_object_revision
            ):
                raise ValueError("handoff belongs to a different migration")
        if self.old_copy_cleanup != "blocked" and (
            self.handoff is None
            or self.handoff.state != "applied"
            or self.protection.state != "released"
        ):
            raise ValueError("cleanup waits for B address commit and C protection release")
        if self.old_copy_cleanup == "completed" and not self.cleanup_evidence_ids:
            raise ValueError("old-copy cleanup requires confirmed evidence")
        return self
