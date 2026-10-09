"""RC-AUTH-06: maintained 2x2 fixtures and selection/authorization observations."""

import math
from hashlib import sha256
from typing import Any, Literal

from pydantic import Field, model_validator

from aether_agent_memory.recall.contracts.models import (
    AccessObserved,
    ContextPack,
    RecallRequest,
    VectorCandidate,
)
from aether_agent_memory.remember.contracts.models import MemoryRef
from aether_agent_memory.runtime.contracts.models import (
    ContractModel,
    Identifier,
    Scope,
    ScopeSelector,
)
from recall_tenant_isolation_support import (
    F4Actor,
    F4CaseData,
    F4StageObservation,
    assert_stage_isolation,
)

SelectionKey = Literal["s1", "j1", "s1j1", "s2", "j2"]
VARIANT_STEPS = {
    "s1": ("s1",),
    "j1": ("j1",),
    "s1j1": ("s1j1",),
    "change-session": ("s1", "s2", "s1"),
    "change-task": ("j1", "j2", "j1"),
    "empty": ("empty",),
    "omitted": ("omitted",),
}


class EmptySelectionContract(ContractModel):
    """Authoritative Q01/target contract, never inferred from an empty response."""

    evidence_id: Identifier
    source_sha: str = Field(pattern=r"^[0-9a-f]{40}$")
    eligible_refs: tuple[MemoryRef, ...] = Field(min_length=1)
    expected_ref: MemoryRef


class SelectionIntersectionCase(ContractModel):
    deployment: F4CaseData
    principal_scope: Scope
    memories: tuple[F4Actor, F4Actor, F4Actor, F4Actor]
    authorization_evidence_id: Identifier
    expected_refs: dict[SelectionKey, MemoryRef]
    empty_contract: EmptySelectionContract | None = None

    def selector(self, key: str) -> ScopeSelector:
        a, _, _, d = self.memories
        selectors = {
            "s1": {"session_id": a.memory.scope.session_id},
            "j1": {"task_id": a.memory.scope.task_id},
            "s1j1": {"session_id": a.memory.scope.session_id, "task_id": a.memory.scope.task_id},
            "s2": {"session_id": d.memory.scope.session_id},
            "j2": {"task_id": d.memory.scope.task_id},
            "empty": {},
        }
        return ScopeSelector.model_validate(selectors[key])

    def eligible(self, selection: ScopeSelector) -> tuple[MemoryRef, ...]:
        return tuple(
            actor.memory
            for actor in self.memories
            if all(
                getattr(actor.memory.scope, key) == value
                for key, value in selection.model_dump(exclude_none=True).items()
            )
        )

    def result_scope(self, selection: ScopeSelector) -> Scope:
        return Scope.model_validate(
            {**self.principal_scope.model_dump(), **selection.model_dump(exclude_none=True)}
        )

    @model_validator(mode="after")
    def grid_and_ownership(self) -> "SelectionIntersectionCase":
        home = self.principal_scope
        if home.session_id is not None or home.task_id is not None:
            raise ValueError("U01 must own both sessions/tasks, not be bound to one")
        a, b, c, d = self.memories
        base = home.model_dump(exclude={"session_id", "task_id"})
        for actor in self.memories:
            if (
                actor.name != "U01"
                or actor.memory.scope.model_dump(exclude={"session_id", "task_id"}) != base
            ):
                raise ValueError("all four grid memories must belong to the same U01 subject")
            if actor.credential_env != a.credential_env:
                raise ValueError("one ordinary subject credential across the grid required")
        pairs = [(x.memory.scope.session_id, x.memory.scope.task_id) for x in self.memories]
        if (
            not all(session and task for session, task in pairs)
            or pairs
            != [
                (a.memory.scope.session_id, a.memory.scope.task_id),
                (a.memory.scope.session_id, d.memory.scope.task_id),
                (d.memory.scope.session_id, a.memory.scope.task_id),
                (d.memory.scope.session_id, d.memory.scope.task_id),
            ]
            or len(set(pairs)) != 4
        ):
            raise ValueError("ordered S1J1/S1J2/S2J1/S2J2 grid required")
        if self.deployment.actors[0] != a:
            raise ValueError("deployment subject must bind first grid receipt")
        foreign = self.deployment.actors[1]
        foreign_scope = foreign.memory.scope
        differences = {
            key
            for key, value in a.memory.scope.model_dump().items()
            if value != getattr(foreign_scope, key)
        }
        if foreign.name != "U04" or differences != {"user_id"}:
            raise ValueError("restricted same-session/task memory must differ only by owner user")
        all_memories = (*self.memories, foreign)
        if (
            len({x.memory.memory_id for x in all_memories}) != 5
            or len({x.text for x in all_memories}) != 5
        ):
            raise ValueError("distinct private IDs and body canaries required")
        if (
            self.deployment.request.sources != "working"
            or self.deployment.request.selection.model_dump(exclude_none=True)
        ):
            raise ValueError("Working request template with empty selection required")
        if self.deployment.server_settings.candidate_limit != 1:
            raise ValueError("server-owned K=1 required")
        if set(self.expected_refs) != {"s1", "j1", "s1j1", "s2", "j2"}:
            raise ValueError("exact expected Ref required for every explicit selector")
        for key, ref in self.expected_refs.items():
            if ref not in self.eligible(self.selector(key)):
                raise ValueError("expected Ref lies outside authorization/selection intersection")
        if self.empty_contract:
            contract = self.empty_contract
            if contract.source_sha != self.deployment.source_sha:
                raise ValueError("Q01 contract must bind the target source SHA")
            if any(ref not in self.eligible(ScopeSelector()) for ref in contract.eligible_refs):
                raise ValueError("empty selection cannot grant access to a foreign owner")
            if len(set(ref.model_dump_json() for ref in contract.eligible_refs)) != len(
                contract.eligible_refs
            ):
                raise ValueError("duplicate empty-selection eligibility Ref")
            if contract.expected_ref not in contract.eligible_refs:
                raise ValueError("Q01 expected Ref must be eligible")
        return self


class SelectionStageObservation(F4StageObservation):
    selection: ScopeSelector
    authorized_refs: tuple[MemoryRef, ...]
    eligible_refs: tuple[MemoryRef, ...]
    authorization_evidence_id: Identifier
    selection_contract_id: Identifier | None = None


def assert_selection_pressure(
    raw_hits: list[dict[str, Any]],
    case: SelectionIntersectionCase,
    selection: ScopeSelector,
    expected: MemoryRef,
) -> None:
    hits = [VectorCandidate.model_validate(value) for value in raw_hits]
    owners = (*case.memories, case.deployment.actors[1])
    assert len(hits) == len(owners) and len(
        {h.target.memory.model_dump_json() for h in hits}
    ) == len(owners)
    for rank, hit in enumerate(hits, 1):
        owner = next((a for a in owners if a.memory == hit.target.memory), None)
        assert owner is not None and hit.rank == rank
        assert (
            hit.target.body_hash == owner.body_hash
            and hit.target.generation == owner.projection_generation
        )
        assert (
            hit.target.memory_source == "working"
            and hit.target.model_space == case.deployment.model_space
        )
        assert hit.score is not None and math.isfinite(hit.score) and hit.score > 0
    scores = [float(h.score) for h in hits if h.score is not None]
    assert scores == sorted(scores, reverse=True), "probe ranks disagree with actual scores"
    local = next(h for h in hits if h.target.memory == expected)
    foreign = next(h for h in hits if h.target.memory == case.deployment.actors[1].memory)
    assert local.score is not None and foreign.score is not None
    assert scores[0] > local.score and foreign.score > local.score, (
        "no adverse authorization Top1 pressure"
    )
    if selection.model_dump(exclude_none=True):
        assert hits[0].target.memory in tuple(a.memory for a in case.memories)
        assert hits[0].target.memory not in case.eligible(selection), (
            "global Top1 must be outside selection"
        )
    else:
        assert hits[0].target.memory == foreign.target.memory


def assert_selection_pack(
    payload: dict[str, Any],
    case: SelectionIntersectionCase,
    selection: ScopeSelector,
    expected: MemoryRef | None,
) -> ContextPack:
    pack = ContextPack.model_validate(payload)
    assert pack.scope == case.result_scope(selection), "Pack scope differs from effective selection"
    items = [item for group in pack.groups for item in group.items]
    eligible = case.eligible(selection)
    if not selection.model_dump(exclude_none=True) and case.empty_contract:
        eligible = case.empty_contract.eligible_refs
    if expected is not None:
        assert pack.outcome == "available" and len(items) == 1
        assert items[0].memory == expected, "Pack differs from exact expected intersection Ref"
    for item in items:
        assert item.memory in eligible, "Pack contains a Ref outside selection/authorization"
        owner = next(a for a in case.memories if a.memory == item.memory)
        assert item.content == owner.text and item.sources == owner.sources
        assert owner.text in pack.rendered_context
    excluded = [a for a in (*case.memories, case.deployment.actors[1]) if a.memory not in eligible]
    serialized = pack.model_dump_json()
    assert not any(a.text in serialized or a.memory.memory_id in serialized for a in excluded)
    return pack


def assert_selection_stage(
    stage: SelectionStageObservation,
    case: SelectionIntersectionCase,
    request: RecallRequest,
    expected: MemoryRef | None,
    operation_id: str,
    job_id: str,
    recall_id: str,
    trace_id: str,
    observer: str,
) -> None:
    selection = request.selection
    owned = tuple(a.memory for a in case.memories)
    eligible = case.eligible(selection)
    contract_id = None
    if not selection.model_dump(exclude_none=True) and case.empty_contract:
        eligible, contract_id = case.empty_contract.eligible_refs, case.empty_contract.evidence_id
    assert stage.selection == selection, "stage belongs to another selection"
    assert stage.authorization_evidence_id == case.authorization_evidence_id
    assert stage.selection_contract_id == contract_id
    assert set(r.model_dump_json() for r in stage.authorized_refs) == set(
        r.model_dump_json() for r in owned
    )
    assert len(stage.authorized_refs) == len(owned)
    if expected is not None:
        assert set(r.model_dump_json() for r in stage.eligible_refs) == set(
            r.model_dump_json() for r in eligible
        )
        assert len(stage.eligible_refs) == len(eligible)
    else:
        assert all(ref in owned for ref in stage.eligible_refs), (
            "unknown Q01 still forbids foreign eligibility"
        )
    if expected is not None:
        owner = next(a for a in case.memories if a.memory == expected)
        assert_stage_isolation(
            stage,
            case.deployment.model_copy(update={"request": request}),
            owner,
            operation_id,
            job_id,
            recall_id,
            trace_id,
            observer,
        )
    else:
        # Q01 unknown: verify safety, never infer a complete result or report acceptance.
        assert (
            stage.run_id,
            stage.operation_id,
            stage.job_id,
            stage.recall_id,
            stage.trace_id,
        ) == (case.deployment.run_id, operation_id, job_id, recall_id, trace_id)
        assert stage.observer_principal_id == observer
        query_hash = sha256(request.query.encode()).hexdigest()
        assert stage.query_hash == stage.query_embedding_input_hash == query_hash
        for field, value in (
            ("config_hash", case.deployment.configuration.config_hash),
            ("source_sha", case.deployment.source_sha),
            ("image_digest", case.deployment.image_digest),
            ("backend_binding", case.deployment.backend_binding),
            ("model_binding", case.deployment.model_binding),
        ):
            assert getattr(stage, field) == value
        for refs in (
            tuple(h.target.memory for h in stage.candidates),
            stage.qualified,
            stage.body_reads,
            stage.model_input_refs,
            stage.result_refs,
        ):
            assert all(ref in owned for ref in refs), "unknown Q01 still forbids foreign content"
        assert len(stage.candidates) <= 1
        for hit in stage.candidates:
            owner = next(a for a in case.memories if a.memory == hit.target.memory)
            assert hit.rank == 1 and hit.target.body_hash == owner.body_hash
            assert hit.target.generation == owner.projection_generation
            assert (
                hit.target.model_space == case.deployment.model_space
                and hit.target.memory_source == "working"
            )
        if case.deployment.server_settings.rerank_policy == "disabled":
            assert not stage.model_input_refs
        for event in stage.events:
            payload = AccessObserved.model_validate(event.payload)
            assert payload.memory in owned and payload.recall_id == recall_id
            assert event.subject.scope == payload.memory.scope and event.trace_id == trace_id
            assert event.initiator_id == "U01"
