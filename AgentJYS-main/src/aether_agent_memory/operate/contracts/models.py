"""C owns decisions and action conclusions; providers own execution evidence."""

from __future__ import annotations

from enum import StrEnum
from typing import Literal, Self

from pydantic import Field, model_validator

from aether_agent_memory.remember.contracts.models import MemoryRef
from aether_agent_memory.runtime.contracts.models import (
    ContractModel,
    Count,
    Digest,
    Identifier,
    NonEmpty,
    Positive,
    Timestamp,
)


class Tier(StrEnum):
    COLD = "cold"
    WARM = "warm"
    HOT = "hot"


class ActionState(StrEnum):
    GENERATED = "generated"
    SUBMITTED = "submitted"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    UNKNOWN = "unknown"
    CANCELLED = "cancelled"


class SchedulingInput(ContractModel):
    memory: MemoryRef
    object_revision: Positive
    storage_watermark: Count
    access_watermark: Count
    successful_reads: Count
    current_tier: Tier
    importance: float = Field(ge=0, le=1)
    available_bytes: Count
    content_bytes: Positive
    coverage: Literal["complete", "partial", "unknown"]
    observed_at: Timestamp
    policy_version: Identifier


class PlacementDecision(ContractModel):
    decision_id: Identifier
    memory: MemoryRef
    outcome: Literal["promote", "demote", "keep", "defer"]
    current_tier: Tier
    target_tier: Tier
    reason: NonEmpty
    policy_version: Identifier
    storage_watermark: Count
    access_watermark: Count

    @model_validator(mode="after")
    def adjacent_move(self) -> Self:
        levels = {Tier.COLD: 0, Tier.WARM: 1, Tier.HOT: 2}
        delta = levels[self.target_tier] - levels[self.current_tier]
        expected = {"promote": 1, "demote": -1, "keep": 0, "defer": 0}
        if delta != expected[self.outcome]:
            raise ValueError("ordinary placement changes must use adjacent tiers")
        return self


class ActionIntent(ContractModel):
    action_id: Identifier
    decision: PlacementDecision
    representation_id: Identifier
    content_hash: Digest
    provider_id: Identifier
    provider_instance_id: Identifier
    expected_epoch: Count
    provider_mode: Literal["simulated", "real"]
    created_at: Timestamp

    @model_validator(mode="after")
    def actionable(self) -> Self:
        if self.decision.outcome not in {"promote", "demote"}:
            raise ValueError("keep/defer are decisions, not external actions")
        return self


class PlacementObservation(ContractModel):
    memory: MemoryRef
    representation_id: Identifier
    provider_instance_id: Identifier
    tier: Tier
    epoch: Count
    readable: bool
    content_hash: Digest
    observed_at: Timestamp


class ReadProof(ContractModel):
    action_id: Identifier
    memory: MemoryRef
    provider_instance_id: Identifier
    content_hash: Digest
    readable: Literal[True]
    verified_at: Timestamp
    provider_mode: Literal["simulated", "real"]


class ResourceSnapshot(ContractModel):
    provider_id: Identifier
    provider_instance_id: Identifier
    epoch: Count
    available_bytes: Count
    observed_at: Timestamp
    supported_moves: tuple[Literal["promote", "demote"], ...]


class ExecutionFeedback(ContractModel):
    action_id: Identifier
    provider_instance_id: Identifier
    provider_operation_id: Identifier | None = None
    state: Literal["accepted", "running", "succeeded", "failed", "unknown", "not_found"]
    observed_at: Timestamp
    observation: PlacementObservation | None = None
    read_proof: ReadProof | None = None
    reason: NonEmpty | None = None


class ActionRecord(ContractModel):
    intent: ActionIntent
    state: ActionState
    revision: Positive
    feedback: ExecutionFeedback | None = None
    cleanup_state: Literal["not_required", "pending", "completed", "failed", "unknown"]
    reason: NonEmpty | None = None

    @model_validator(mode="after")
    def successful_evidence(self) -> Self:
        if self.state != ActionState.SUCCEEDED:
            return self
        feedback = self.feedback
        if feedback is None or feedback.state != "succeeded":
            raise ValueError("success requires confirmed execution feedback")
        observation, proof = feedback.observation, feedback.read_proof
        if observation is None or proof is None:
            raise ValueError("success requires placement and actual read evidence")
        intent = self.intent
        if feedback.action_id != intent.action_id or proof.action_id != intent.action_id:
            raise ValueError("feedback must identify the original action")
        if any(
            instance != intent.provider_instance_id
            for instance in (
                feedback.provider_instance_id,
                observation.provider_instance_id,
                proof.provider_instance_id,
            )
        ):
            raise ValueError("a provider restart cannot prove the previous action")
        if observation.memory != intent.decision.memory or proof.memory != intent.decision.memory:
            raise ValueError("proof must identify the exact memory version and scope")
        if observation.representation_id != intent.representation_id:
            raise ValueError("representation mismatch")
        if observation.tier != intent.decision.target_tier or not observation.readable:
            raise ValueError("target must be reached and readable")
        if observation.epoch < intent.expected_epoch:
            raise ValueError("stale observation")
        if (
            proof.content_hash != intent.content_hash
            or observation.content_hash != intent.content_hash
        ):
            raise ValueError("content mismatch")
        if proof.provider_mode != intent.provider_mode:
            raise ValueError("simulated evidence cannot confirm a real action")
        return self


class ActionChanged(ContractModel):
    action_id: Identifier
    memory: MemoryRef
    state: ActionState
    provider_mode: Literal["simulated", "real"]
    observed_epoch: Count | None = None
    reason: NonEmpty
