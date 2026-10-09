"""RC-AUTH-07/08 request attacks and job-bound authoritative denial receipts."""

import math
from typing import Any, Literal

from pydantic import model_validator

from aether_agent_memory.recall.contracts.models import VectorCandidate
from aether_agent_memory.remember.contracts.foundation import CandidateQualificationResult
from aether_agent_memory.remember.contracts.models import MemoryRef
from aether_agent_memory.runtime.contracts.models import Identifier, Principal
from recall_tenant_isolation_support import F4CaseData, F4StageObservation, assert_public_isolation

REQUEST_VARIANTS = tuple(
    f"{location}-{field}"
    for location in ("body", "selection")
    for field in (
        "tenant_id",
        "application_id",
        "user_id",
        "agent_id",
        "scope",
        "grant",
        "auth_epoch",
    )
)
INDEX_VARIANTS = ("index-scope", "index-grant", "index-auth_epoch")


class ForgeryCase(F4CaseData):
    principal: Principal
    q06_mapping_id: Identifier | None = None
    q18_capability_id: Identifier | None = None

    @model_validator(mode="after")
    def bound_subject(self) -> "ForgeryCase":
        a, b = self.actors
        if (a.name, b.name) != ("U01", "U04"):
            raise ValueError("ordered U01/U04 fixture required")
        if self.principal.principal_id != a.name or self.principal.home_scope != a.memory.scope:
            raise ValueError("principal must bind actual ordinary home scope")
        if "memory:read" not in self.principal.permissions:
            raise ValueError("legal READ control required")
        if any(
            getattr(a.memory.scope, key) == getattr(b.memory.scope, key)
            for key in ("tenant_id", "application_id", "user_id", "agent_id")
        ):
            raise ValueError("all four foreign identity values must differ")
        if a.memory.memory_id == b.memory.memory_id or a.text == b.text:
            raise ValueError("distinct private ID/body canaries required")
        if (
            a.memory.scope.session_id != b.memory.scope.session_id
            or a.memory.scope.task_id != b.memory.scope.task_id
        ):
            raise ValueError("same session/task isolates identity forgery")
        if (
            not a.memory.scope.session_id
            or self.request.selection.model_dump(exclude_none=True)
            != {"session_id": a.memory.scope.session_id}
            or self.request.sources != "working"
        ):
            raise ValueError("bound Working request required")
        if (
            self.server_settings.candidate_limit != 1
            or self.server_settings.rerank_policy != "required"
        ):
            raise ValueError("actual K=1 and required reranker positive control required")
        return self


def forged_request(case: ForgeryCase, variant: str) -> tuple[dict[str, Any], int]:
    assert variant in REQUEST_VARIANTS
    location, field = variant.split("-", 1)
    foreign_scope = case.actors[1].memory.scope.model_dump(mode="json")
    values: dict[str, Any] = {
        **foreign_scope,
        "scope": foreign_scope,
        "grant": {
            "principal_id": "U04",
            "permission": "memory:read",
            "resource": "forged-resource",
        },
        "auth_epoch": case.principal.auth_epoch + 100,
    }
    payload = case.request.model_dump(mode="json")
    destination = payload if location == "body" else payload["selection"]
    destination[field] = values[field]
    status = (
        403
        if location == "selection" and field in {"application_id", "user_id", "agent_id"}
        else 422
    )
    return payload, status


class ForgeryStageObservation(F4StageObservation):
    trusted_principal: Principal
    q06_mapping_id: Identifier
    body_read_attempts: tuple[MemoryRef, ...]
    injection_kind: Literal["index-scope", "index-grant", "index-auth_epoch"] | None = None
    q18_capability_id: Identifier | None = None
    injection_evidence_id: Identifier | None = None
    injection_operation_id: Identifier | None = None
    raw_index_hit: dict[str, Any] | None = None
    index_hits: tuple[VectorCandidate, ...] = ()
    qualification_results: tuple[CandidateQualificationResult, ...] = ()
    stage_sequence: tuple[int, int, int, int, int] | None = None


def assert_trusted_subject(stage: ForgeryStageObservation, case: ForgeryCase) -> None:
    assert stage.trusted_principal == case.principal, (
        "request/index overwrote trusted principal or epoch"
    )
    assert stage.q06_mapping_id == case.q06_mapping_id, "unbound Q06 qualification/body mapping"
    assert stage.body_read_attempts == (case.actors[0].memory,), (
        "excluded candidate reached body loading"
    )


def assert_authority_veto(stage: ForgeryStageObservation, case: ForgeryCase, variant: str) -> None:
    """A candidate qualification is not a body read; inspect both independently."""
    assert variant in INDEX_VARIANTS and stage.injection_kind == variant
    assert stage.q18_capability_id == case.q18_capability_id
    assert stage.injection_evidence_id and stage.injection_operation_id == stage.operation_id
    assert stage.stage_sequence is not None
    assert all(
        a < b for a, b in zip(stage.stage_sequence[:-1], stage.stage_sequence[1:], strict=True)
    )
    assert stage.stage_sequence[0] >= 0, "injection/qualify/load/rerank/Pack barrier order missing"
    assert len(stage.index_hits) == 2
    spoof, local = stage.index_hits
    owner, foreign = case.actors
    assert spoof.rank == 1 and local.rank == 2
    assert local.target.memory == owner.memory
    assert local.target.body_hash == owner.body_hash
    assert local.target.generation == owner.projection_generation
    assert spoof.target.memory.memory_id == foreign.memory.memory_id
    assert spoof.target.memory.scope == owner.memory.scope, (
        "controlled target must forge local scope"
    )
    assert spoof.target.memory.version == foreign.memory.version
    assert (
        spoof.target.body_hash == foreign.body_hash
        and spoof.target.generation == foreign.projection_generation
    )
    for hit in stage.index_hits:
        assert hit.target.model_space == case.model_space and hit.target.memory_source == "working"
        assert hit.score is not None and math.isfinite(hit.score) and hit.score > 0
    assert spoof.score is not None and local.score is not None and spoof.score > local.score
    raw = stage.raw_index_hit
    assert raw is not None
    assert raw["entity"]["target"] == spoof.target.model_dump(mode="json")
    assert raw["distance"] == spoof.score
    if variant == "index-grant":
        assert raw["entity"]["grant"] == {"principal_id": "U01", "permission": "memory:read"}
    elif variant == "index-auth_epoch":
        assert isinstance(raw["entity"]["auth_epoch"], int)
        assert raw["entity"]["auth_epoch"] > case.principal.auth_epoch
    denied = [
        result
        for result in stage.qualification_results
        if result.target.model_dump(mode="json") == spoof.target.model_dump(mode="json")
    ]
    assert len(denied) == 1 and denied[0].decision == "excluded", (
        "Remember authority must explicitly veto"
    )
    assert denied[0].guard is None and denied[0].manifest is None
    assert_trusted_subject(stage, case)
    assert (
        stage.qualified
        == stage.body_reads
        == stage.model_input_refs
        == stage.result_refs
        == (owner.memory,)
    )


def assert_no_index_disclosure(payload: Any, private_atoms: tuple[str, ...]) -> None:
    assert_public_isolation(payload, private_atoms)
    if isinstance(payload, dict):
        assert not {
            "vector",
            "vectors",
            "embedding",
            "raw_vector",
            "raw_payload",
            "index_payload",
            "raw_index_hit",
        }.intersection(payload), "raw index/vector diagnostic leaked"
        for value in payload.values():
            assert_no_index_disclosure(value, private_atoms)
    elif isinstance(payload, list):
        for value in payload:
            assert_no_index_disclosure(value, private_atoms)
