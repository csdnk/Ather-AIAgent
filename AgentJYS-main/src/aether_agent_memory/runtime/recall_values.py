"""First-batch document contracts. Fields are required, including nullable fields.

Source: Recall data dictionary and P2 interface requirements, 2026-09-07.
No external provider wire compatibility is implied by these logical models.
"""

from __future__ import annotations

from typing import Self

from pydantic import model_validator

from aether_agent_memory.runtime.contract_types import (
    ByteRange,
    ContractModel,
    CoverageStatus,
    Identifier,
    LogicalSource,
    MemoryLabel,
    ReasonCode,
    Timestamp,
    UInt,
)


class RetrievalConstraints(ContractModel):
    """Recall data dictionary section 0: RetrievalConstraints."""

    memory_types: list[MemoryLabel]
    occurred_after: Timestamp | None
    occurred_before: Timestamp | None
    allowed_sources: list[LogicalSource]

    @model_validator(mode="after")
    def normalized(self) -> Self:
        if not self.memory_types or self.memory_types != [
            t for t in ("Working", "Episodic", "Semantic") if t in self.memory_types
        ]:
            raise ValueError("memory_types must be nonempty, unique and canonically ordered")
        if self.allowed_sources != [
            s for s in ("working", "long_term") if s in self.allowed_sources
        ]:
            raise ValueError("allowed_sources must be unique and canonically ordered")
        if (
            self.occurred_after is not None
            and self.occurred_before is not None
            and self.occurred_after > self.occurred_before
        ):
            raise ValueError("business time interval is reversed")
        return self


class SourceCoverage(ContractModel):
    """Recall data dictionary section 0: SourceCoverage."""

    working: CoverageStatus
    long_term: CoverageStatus


class ContentIdentity(ContractModel):
    """Recall data dictionary section 0: ContentIdentity."""

    tenant_id: Identifier
    memory_id: Identifier
    memory_version: Identifier
    content_ref: Identifier
    content_version: Identifier
    range: ByteRange


class CandidateExclusion(ContractModel):
    """Recall data dictionary section 0: CandidateExclusion."""

    candidate_ref: Identifier
    reason: ReasonCode | None
    rule_ref: Identifier | None
    authority_ref: Identifier
    authority_version: Identifier
    evidence_ref: Identifier
    decided_at: Timestamp


class SourceReference(ContractModel):
    """Recall data dictionary section 0: SourceReference."""

    candidate_id: Identifier
    source_id: LogicalSource
    source_ref: Identifier
    provider_ref: Identifier
    representation_id: Identifier | None
    representation_type: Identifier | None
    source_rank: UInt
    external_contract_ref: Identifier
    source_evidence_ref: Identifier
    observed_at: Timestamp


# Resolve forward references after all nested value types are defined.
CandidateExclusion.model_rebuild()
ContentIdentity.model_rebuild()
RetrievalConstraints.model_rebuild()
SourceCoverage.model_rebuild()
SourceReference.model_rebuild()
