"""RC-AUTH-04/05 single-dimension fixtures and actual adverse-index receipts."""

import math
from typing import Any, Literal

from pydantic import model_validator

from aether_agent_memory.recall.contracts.models import VectorCandidate
from aether_agent_memory.runtime.contracts.models import Identifier
from recall_tenant_isolation_support import F4Actor, F4CaseData, F4StageObservation


class ScopeStageObservation(F4StageObservation):
    authorization_evidence_id: Identifier
    sharing_grant_ids: tuple[Identifier, ...]


def assert_no_implicit_sharing(stage: ScopeStageObservation, case: "ScopeIsolationCase") -> None:
    assert stage.authorization_evidence_id == case.authorization_evidence_id
    assert not stage.sharing_grant_ids, "no-share fixture unexpectedly consumed a sharing grant"


class ScopeIsolationCase(F4CaseData):
    dimension: Literal["application_id", "user_id", "agent_id"]
    authorization_evidence_id: Identifier

    @model_validator(mode="after")
    def independent_dimension(self) -> "ScopeIsolationCase":
        a, b = self.actors
        if {a.name, b.name} != {"U01", "U04"}:
            raise ValueError("scope controls require U01 and U04")
        differences = {
            key
            for key, value in a.memory.scope.model_dump().items()
            if value != getattr(b.memory.scope, key)
        }
        if differences != {self.dimension}:
            raise ValueError("exactly the declared authorization dimension must differ")
        if a.memory.memory_id == b.memory.memory_id or a.text == b.text:
            raise ValueError("distinct private memory IDs and body canaries required")
        if not a.memory.scope.session_id or self.request.sources != "working":
            raise ValueError("scope fixture requires bound Working controls")
        if self.request.selection.model_dump(exclude_none=True) != {
            "session_id": a.memory.scope.session_id
        }:
            raise ValueError("baseline selection must only bind the shared session")
        if self.server_settings.candidate_limit != 1:
            raise ValueError("actual server-owned K=1 required")
        return self


def assert_scope_pressure(
    raw_hits: list[dict[str, Any]], case: ScopeIsolationCase, local: F4Actor
) -> None:
    """Only authorized maintenance code may inspect the restricted high-score hit."""
    foreign = next(actor for actor in case.actors if actor.memory != local.memory)
    hits = [VectorCandidate.model_validate(hit) for hit in raw_hits]
    assert len(hits) == 2, "probe must bind both exact competing Refs"
    for hit, owner, rank in zip(hits, (foreign, local), (1, 2), strict=True):
        assert hit.rank == rank and hit.target.memory == owner.memory
        assert hit.target.generation == owner.projection_generation
        assert hit.target.body_hash == owner.body_hash
        assert hit.target.model_space == case.model_space
        assert hit.target.memory_source == "working"
        assert hit.score is not None and math.isfinite(hit.score) and hit.score > 0
    assert hits[0].score is not None and hits[1].score is not None
    assert hits[0].score > hits[1].score, "restricted memory must outrank the legal control"
