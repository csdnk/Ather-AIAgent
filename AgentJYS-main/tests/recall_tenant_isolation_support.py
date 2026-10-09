"""RC-AUTH-03 observations through existing HTTP and maintained F4 evidence.

Fixture/evidence file schemas belong to tests, not to the product HTTP API.
"""

import json
import math
import time
from hashlib import sha256
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, model_validator

from aether_agent_memory.recall.basic.config import RecallSettings
from aether_agent_memory.recall.contracts.models import (
    AccessObserved,
    ContextPack,
    RecallRequest,
    VectorCandidate,
)
from aether_agent_memory.remember.contracts.models import MemoryRef, SourceRef
from aether_agent_memory.runtime.contracts.foundation import ConfigurationSnapshot
from aether_agent_memory.runtime.contracts.models import (
    ContractModel,
    Digest,
    EventEnvelope,
    Identifier,
    TraceId,
)


class F4Actor(ContractModel):
    name: Literal["U01", "U04"]
    credential_env: Identifier
    maintainer_env: Identifier
    memory: MemoryRef
    text: str = Field(min_length=1)
    body_hash: Digest
    body_generation: Identifier
    projection_generation: Identifier
    sources: tuple[SourceRef, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def body_binding(self) -> "F4Actor":
        if sha256(self.text.encode()).hexdigest() != self.body_hash:
            raise ValueError("F4 body hash differs from the exact text")
        return self


class F4Case(ContractModel):
    """Maintainer-prepared data; never invent a memory-ID override route."""

    run_id: Identifier
    request: RecallRequest
    actors: tuple[F4Actor, F4Actor]
    maintenance_export: Path
    operator_env: Identifier
    configuration: ConfigurationSnapshot
    server_settings: RecallSettings
    source_sha: str = Field(pattern=r"^[0-9a-f]{40}$")
    image_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    backend_binding: Digest
    model_binding: Digest
    model_space: Identifier

    @model_validator(mode="after")
    def same_named_identifiers(self) -> "F4Case":
        a, b = self.actors
        if {a.name, b.name} != {"U01", "U04"}:
            raise ValueError("F4 requires U01 and U04")
        if a.memory.scope.tenant_id == b.memory.scope.tenant_id:
            raise ValueError("F4 tenants must differ")
        if a.memory.memory_id != b.memory.memory_id or a.memory.version != b.memory.version:
            raise ValueError("F4 requires same literal memory_id and version")
        if a.memory.scope.model_dump(exclude={"tenant_id"}) != b.memory.scope.model_dump(
            exclude={"tenant_id"}
        ):
            raise ValueError("F4 user/session/application/agent/task IDs must match")
        if not a.memory.scope.session_id or self.request.sources != "working":
            raise ValueError("F4 requires bound Working memories")
        if self.request.selection.session_id != a.memory.scope.session_id:
            raise ValueError("F4 request must select the prepared session")
        if self.server_settings.candidate_limit != 1:
            raise ValueError("F4 requires server-owned candidate_limit=1")
        return self


class F4StageObservation(ContractModel):
    """Exported by a legal maintenance view, bound to the actual original job.

    This is a test-side observation format, not a claim that a new product route exists.
    """

    run_id: Identifier
    operation_id: Identifier
    job_id: Identifier
    recall_id: Identifier
    trace_id: TraceId
    query_hash: Digest
    config_hash: Digest
    source_sha: str
    image_digest: str
    backend_binding: Digest
    model_binding: Digest
    evidence_id: Identifier
    observer_principal_id: Identifier
    candidate_limit: Literal[1]
    unfiltered_probe: tuple[VectorCandidate, ...]
    candidates: tuple[VectorCandidate, ...]
    qualified: tuple[MemoryRef, ...]
    body_reads: tuple[MemoryRef, ...]
    body_paths: tuple[Literal["cache", "authority", "p2"], ...]
    model_input_refs: tuple[MemoryRef, ...]
    query_embedding_input_hash: Digest
    result_refs: tuple[MemoryRef, ...]
    events: tuple[EventEnvelope, ...]


def load_stage_observation(
    path: Path,
    job_id: str,
    timeout: float = 10,
) -> F4StageObservation:
    """Poll an exporter receipt for this job; never reuse a different execution."""
    deadline = time.monotonic() + timeout
    while True:
        if path.is_file():
            # Exporters publish atomically. Malformed complete evidence is a failure.
            rows = json.loads(path.read_text(encoding="utf-8"))
            matched = [row for row in rows if row.get("job_id") == job_id]
            assert len(matched) <= 1, "duplicate maintenance observations for original job"
            if matched:
                return F4StageObservation.model_validate(matched[0])
        if time.monotonic() >= deadline:
            raise RuntimeError("blocked_fixture: authorized stage receipt for original job missing")
        time.sleep(min(0.05, max(0, deadline - time.monotonic())))


def assert_stage_isolation(
    observation: F4StageObservation,
    case: F4Case,
    actor: F4Actor,
    operation_id: str,
    job_id: str,
    recall_id: str,
    trace_id: str,
    observer_principal_id: str,
) -> set[str]:
    assert (
        observation.run_id,
        observation.operation_id,
        observation.job_id,
        observation.recall_id,
        observation.trace_id,
    ) == (
        case.run_id,
        operation_id,
        job_id,
        recall_id,
        trace_id,
    ), "maintenance receipt belongs to another execution"
    assert observation.observer_principal_id == observer_principal_id, "wrong maintenance observer"
    assert (
        observation.config_hash,
        observation.source_sha,
        observation.image_digest,
        observation.backend_binding,
        observation.model_binding,
    ) == (
        case.configuration.config_hash,
        case.source_sha,
        case.image_digest,
        case.backend_binding,
        case.model_binding,
    ), "maintenance receipt belongs to another deployment/model/configuration"
    query_hash = sha256(case.request.query.encode()).hexdigest()
    assert observation.query_hash == observation.query_embedding_input_hash == query_hash
    assert len(observation.candidates) == 1, "K=1 must yield the legal candidate"
    candidate = observation.candidates[0]
    assert candidate.rank == 1 and candidate.target.memory == actor.memory
    assert candidate.target.generation == actor.projection_generation
    assert candidate.target.body_hash == actor.body_hash
    assert candidate.target.memory_source == "working"
    assert candidate.target.model_space == case.model_space
    assert candidate.score is not None and math.isfinite(candidate.score) and candidate.score > 0
    assert (
        observation.qualified
        == observation.body_reads
        == observation.result_refs
        == (actor.memory,)
    )
    assert len(observation.body_paths) == len(observation.body_reads)
    expected_inputs = () if case.server_settings.rerank_policy == "disabled" else (actor.memory,)
    assert observation.model_input_refs == expected_inputs, "foreign/missing model input"
    assert observation.events, "missing tenant-scoped access event evidence"
    stages = set()
    event_ids = set()
    for event in observation.events:
        assert event.event_type == "recall.access" and event.producer == "recall"
        payload = AccessObserved.model_validate(event.payload)
        assert payload.memory == actor.memory and payload.recall_id == recall_id
        assert event.subject.scope == actor.memory.scope
        assert event.subject.owner == "remember" and event.subject.object_type == "memory"
        assert event.subject.object_id == actor.memory.memory_id
        assert event.subject.version == actor.memory.version
        assert event.subject_revision == actor.memory.version
        assert event.initiator_id == actor.name and event.trace_id == trace_id
        assert payload.outcome == "succeeded"
        assert event.event_id not in event_ids, "duplicate tenant event"
        stages.add(payload.stage)
        event_ids.add(event.event_id)
    assert {"read", "packed"}.issubset(stages), "missing body/result access stages"
    return event_ids


def assert_isolated_pack(
    payload: dict[str, Any],
    expected_ref: dict[str, Any],
    expected_body: str,
    foreign_bodies: tuple[str, ...],
) -> ContextPack:
    pack = ContextPack.model_validate(payload)
    ref = MemoryRef.model_validate(expected_ref)
    assert pack.scope == ref.scope, "Pack scope differs from authorized scope"
    assert pack.outcome == "available", "legal READ control must be nonempty and complete"
    items = [item for group in pack.groups for item in group.items]
    assert len(items) == 1 and items[0].memory == ref, "Pack must contain only the exact local Ref"
    assert items[0].content == expected_body, "Pack body differs from the local fixture"
    assert expected_body in pack.rendered_context, "local body absent from rendered context"
    serialized = pack.model_dump_json()
    assert not any(body in serialized for body in foreign_bodies), "foreign body leaked"
    return pack


def assert_top1_pressure(
    raw_hits: list[dict[str, Any]],
    local_ref: dict[str, Any],
    foreign_ref: dict[str, Any],
) -> None:
    hits = [VectorCandidate.model_validate(hit) for hit in raw_hits]
    local, foreign = MemoryRef.model_validate(local_ref), MemoryRef.model_validate(foreign_ref)
    assert local.scope.tenant_id != foreign.scope.tenant_id, "F4 requires different tenants"
    assert len(hits) == 2, "F4 probe must contain the exact two competing memories"
    assert hits[0].target.memory == foreign and hits[0].rank == 1, "foreign Ref must rank first"
    assert hits[1].target.memory == local and hits[1].rank == 2, "local Ref must rank second"
    assert hits[0].score is not None and hits[1].score is not None
    assert math.isfinite(hits[0].score) and math.isfinite(hits[1].score), "nonfinite F4 score"
    assert hits[0].score > hits[1].score > 0, "F4 requires strictly adverse positive scores"
    assert all(h.target.generation and h.target.body_hash for h in hits), "unbound F4 probe"


def assert_public_isolation(payload: Any, foreign_atoms: tuple[str, ...]) -> None:
    """Inspect raw ordinary responses, including 202 and nested job diagnostics."""
    if isinstance(payload, dict):
        assert not {
            "foreign_candidate_count",
            "restricted_count",
            "unauthorized_count",
            "filtered_count",
        }.intersection(payload), "restricted count leak"
        for value in payload.values():
            assert_public_isolation(value, foreign_atoms)
    elif isinstance(payload, list):
        for value in payload:
            assert_public_isolation(value, foreign_atoms)
    elif isinstance(payload, str):
        assert not any(atom and atom in payload for atom in foreign_atoms), (
            "foreign identity/body leak"
        )
