"""RC-AUTH-10 authority barriers and maintained evidence; no grant storage writes."""

import json
import os
import time
from hashlib import sha256
from pathlib import Path
from typing import Literal

import httpx
from pydantic import Field, model_validator

from aether_agent_memory.recall.contracts.models import AccessObserved, RecallRecord
from aether_agent_memory.remember.contracts.foundation import (
    CandidateQualificationResult,
    CandidateQualificationTarget,
    GuardStamp,
)
from aether_agent_memory.remember.contracts.models import MemoryRef
from aether_agent_memory.runtime.contracts.models import (
    AuthorizationGrant,
    ContractModel,
    Digest,
    EventEnvelope,
    Identifier,
    Principal,
    TaskOperationView,
    Timestamp,
)
from recall_authorization_support import assert_denied
from recall_shared_read_support import GrantReceipt, SavedSharedPack, SharedReadCase, external_path

VARIANTS = ("recall-result", "operation-result", "http-cache")


class RevokeCase(SharedReadCase):
    revoke_request: Path
    revoke_export: Path
    final_export: Path

    @model_validator(mode="after")
    def independent_paths(self) -> "RevokeCase":
        if self.grant.expires_at is not None:
            raise ValueError(
                "revocation fixture requires a nonexpiring grant; expiry confounds denial"
            )
        paths = [
            external_path(p)
            for p in (
                self.control_request,
                self.control_receipts,
                self.maintenance_export,
                self.success_directory,
                self.revoke_request,
                self.revoke_export,
                self.final_export,
            )
        ]
        if len(set(paths)) != len(paths):
            raise ValueError("grant, revocation and observation paths must be independent")
        return self


class RetainedReplicas(ContractModel):
    """Legal exporter observes real Milvus and Redis, not merely desired retention."""

    observed_at: Timestamp
    vector_present: bool
    vector: CandidateQualificationTarget
    redis_present: bool
    redis_memory: MemoryRef
    redis_generation: Identifier
    redis_content: str
    redis_body_hash: Digest
    redis_provider: Literal["redis"]
    redis_instance: Identifier
    vector_provider: Literal["milvus"]
    vector_instance: Identifier


class RevokeReceipt(ContractModel):
    run_id: Identifier
    control_operation_id: Identifier
    q18_capability_id: Identifier
    entrypoint: Literal["Identity.provision"]
    evidence_id: Identifier
    observer_principal_id: Identifier
    source_sha: str
    config_hash: Digest
    image_digest: str
    backend_binding: Digest
    model_binding: Digest
    configuration_revision: int = Field(gt=0)
    submitted_at: Timestamp
    committed_at: Timestamp
    effective_at: Timestamp
    observed_at: Timestamp
    revoked_grant: AuthorizationGrant
    grants_for_u08: tuple[AuthorizationGrant, ...]
    grants_for_u09: tuple[AuthorizationGrant, ...]
    principals: dict[str, Principal]
    qualification: CandidateQualificationResult
    qualification_principal: Principal
    qualification_checked_at: Timestamp
    replicas: RetainedReplicas


def assert_deployment(receipt: ContractModel, case: RevokeCase, observer: str) -> None:
    for key, expected in (
        ("run_id", case.run_id),
        ("observer_principal_id", observer),
        ("source_sha", case.source_sha),
        ("config_hash", case.configuration.config_hash),
        ("image_digest", case.image_digest),
        ("backend_binding", case.backend_binding),
        ("model_binding", case.model_binding),
    ):
        assert getattr(receipt, key) == expected, "foreign execution/deployment evidence"


def assert_retained(replicas: RetainedReplicas, case: RevokeCase, after: Timestamp) -> None:
    memory = case.memories["shared"]
    assert replicas.observed_at >= after
    assert replicas.vector_present and replicas.redis_present, (
        "physical cleanup confounds revocation"
    )
    assert replicas.vector.memory == replicas.redis_memory == memory.memory
    assert replicas.vector.generation == memory.projection_generation
    assert replicas.vector.model_space == case.model_space
    assert replicas.vector.memory_source == "working"
    assert replicas.vector.body_hash == replicas.redis_body_hash == memory.body_hash
    assert replicas.redis_generation == memory.body_generation
    assert replicas.redis_content == memory.text
    assert sha256(replicas.redis_content.encode()).hexdigest() == memory.body_hash


def assert_revoked(
    receipt: RevokeReceipt, case: RevokeCase, active: GrantReceipt, observer: str
) -> None:
    assert_deployment(receipt, case, observer)
    assert receipt.control_operation_id == "revoke-" + case.run_id
    assert receipt.q18_capability_id == case.q18_capability_id
    assert receipt.revoked_grant == case.grant
    assert receipt.configuration_revision > active.configuration_revision
    assert active.observed_at <= receipt.submitted_at <= receipt.committed_at
    assert receipt.committed_at <= receipt.effective_at <= receipt.observed_at
    # This is grant revocation, not credential/tenant revocation or scope removal.
    assert receipt.principals == case.principals
    assert not receipt.grants_for_u08 and not receipt.grants_for_u09
    assert receipt.qualification_principal == case.principals["U08"]
    assert receipt.effective_at <= receipt.qualification_checked_at <= receipt.observed_at
    q = receipt.qualification
    assert q.target == active.qualification.target and q.decision == "excluded"
    assert q.guard is None and q.manifest is None
    assert_retained(receipt.replicas, case, receipt.effective_at)
    assert receipt.replicas.observed_at <= receipt.observed_at


def wait_receipt[ReceiptType: ContractModel](
    path: Path, key: str, value: str, model: type[ReceiptType], timeout: float = 30
) -> ReceiptType:
    deadline = time.monotonic() + timeout
    while True:
        if external_path(path).is_file():
            rows = json.loads(path.read_text(encoding="utf-8"))
            matches = [row for row in rows if row.get(key) == value]
            assert len(matches) <= 1, "duplicate authoritative observation"
            if matches:
                return model.model_validate(matches[0])
        if time.monotonic() >= deadline:
            raise RuntimeError(
                "blocked_fixture: authoritative revocation/observation receipt missing"
            )
        time.sleep(min(0.05, max(0, deadline - time.monotonic())))


def revoke_shared_grant(
    case: RevokeCase, active: GrantReceipt, observer: str, timeout: float = 30
) -> RevokeReceipt:
    if not case.q18_capability_id:
        raise RuntimeError("blocked_fixture: Q18 controlled grant revocation unavailable")
    path = external_path(case.revoke_request)
    path.parent.mkdir(parents=True, exist_ok=True)
    pending = path.with_name(path.name + "." + case.run_id + ".pending")
    with pending.open("x", encoding="utf-8") as stream:
        json.dump(
            {
                "run_id": case.run_id,
                "control_operation_id": "revoke-" + case.run_id,
                "q18_capability_id": case.q18_capability_id,
                "entrypoint": "Identity.provision",
                "remove_grant": case.grant.model_dump(mode="json"),
                "active_evidence_id": active.evidence_id,
                "retain_vector_and_redis": True,
                "preserve_principals": True,
            },
            stream,
        )
    os.link(pending, path)
    pending.unlink()
    receipt = wait_receipt(
        case.revoke_export, "control_operation_id", "revoke-" + case.run_id, RevokeReceipt, timeout
    )
    assert_revoked(receipt, case, active, observer)
    return receipt


class HistoricalReadObservation(ContractModel):
    request_id: Identifier
    path: str
    checked_at: Timestamp
    principal: Principal
    qualification: CandidateQualificationResult
    search_invocations: int = Field(ge=0)
    original_pack_hash_before: Digest
    original_pack_hash_after: Digest
    events: tuple[EventEnvelope, ...]


class RevokeFinalObservation(ContractModel):
    run_id: Identifier
    control_operation_id: Identifier
    evidence_id: Identifier
    observer_principal_id: Identifier
    source_sha: str
    config_hash: Digest
    image_digest: str
    backend_binding: Digest
    model_binding: Digest
    observed_at: Timestamp
    original_record: RecallRecord
    original_job: TaskOperationView
    original_guard: GuardStamp
    original_pack_hash: Digest
    original_events: tuple[EventEnvelope, ...]
    new_operation_id: Identifier
    new_job_id: Identifier
    new_recall_id: Identifier
    new_trace_id: str
    new_qualification: CandidateQualificationResult
    new_qualification_principal: Principal
    new_qualification_checked_at: Timestamp
    new_events: tuple[EventEnvelope, ...]
    historical_reads: tuple[HistoricalReadObservation, ...]
    replicas: RetainedReplicas


def pack_hash(saved: SavedSharedPack) -> str:
    """Digest of canonical typed original Pack, never a newly searched/cropped Pack."""
    return sha256(saved.pack.model_dump_json().encode()).hexdigest()


def assert_no_revoked_delivery(events: tuple[EventEnvelope, ...], case: RevokeCase) -> None:
    for event in events:
        if event.event_type == "recall.access":
            payload = AccessObserved.model_validate(event.payload)
            assert not (
                payload.memory == case.memories["shared"].memory
                and payload.stage in {"packed", "delivered"}
                and payload.outcome == "succeeded"
            ), "blocked path falsely reports revoked material packed/delivered"


def assert_original_preserved(
    final: RevokeFinalObservation,
    case: RevokeCase,
    receipt: RevokeReceipt,
    active: GrantReceipt,
    saved: SavedSharedPack,
    original_events: tuple[EventEnvelope, ...],
    original_job: TaskOperationView,
    original_record: RecallRecord,
    original_guard: GuardStamp,
    new_saved: SavedSharedPack,
    reads: dict[str, str],
    observer: str,
) -> None:
    assert_deployment(final, case, observer)
    assert final.control_operation_id == receipt.control_operation_id
    assert final.original_record == original_record and final.original_job == original_job
    assert final.original_guard == original_guard
    assert final.original_pack_hash == pack_hash(saved)
    assert final.original_events == original_events, "historical read facts rewritten"
    assert any(
        AccessObserved.model_validate(event.payload).stage == "read" for event in original_events
    )
    assert_no_revoked_delivery(final.new_events, case)
    assert (final.new_operation_id, final.new_job_id, final.new_recall_id, final.new_trace_id) == (
        new_saved.operation_id,
        new_saved.job_id,
        new_saved.pack.recall_id,
        new_saved.trace_id,
    )
    assert final.new_qualification_principal == case.principals["U08"]
    assert receipt.effective_at <= final.new_qualification_checked_at <= final.observed_at
    assert final.new_qualification.target == active.qualification.target
    assert final.new_qualification.decision == "excluded"
    assert final.new_qualification.guard is None and final.new_qualification.manifest is None
    for event in final.new_events:
        assert event.initiator_id == "U08" and event.trace_id == new_saved.trace_id
        if event.event_type == "recall.access":
            assert (
                AccessObserved.model_validate(event.payload).recall_id == new_saved.pack.recall_id
            )
    assert {row.request_id: row.path for row in final.historical_reads} == reads
    assert len(final.historical_reads) == len(reads), "duplicate historical read observation"
    for row in final.historical_reads:
        assert receipt.effective_at <= row.checked_at <= final.observed_at
        assert row.principal == case.principals["U08"]
        assert row.qualification.target == active.qualification.target
        assert row.qualification.decision == "excluded"
        assert row.qualification.guard is None and row.qualification.manifest is None
        assert row.search_invocations == 0, "old result was replaced by a new search"
        assert row.original_pack_hash_before == row.original_pack_hash_after == pack_hash(saved)
        assert_no_revoked_delivery(row.events, case)
    assert_retained(
        final.replicas,
        case,
        max(
            receipt.effective_at,
            final.new_qualification_checked_at,
            *(row.checked_at for row in final.historical_reads),
        ),
    )
    assert final.replicas.observed_at <= final.observed_at


def assert_invalidated(response: httpx.Response) -> None:
    # Existing HTTP error mapping: FORBIDDEN=403, RESULT_INVALIDATED=410.
    assert response.status_code in {403, 410}, "historical result must deny, not replay/crop/304"
    assert_denied(
        response,
        response.status_code,
        "FORBIDDEN" if response.status_code == 403 else "RESULT_INVALIDATED",
    )
