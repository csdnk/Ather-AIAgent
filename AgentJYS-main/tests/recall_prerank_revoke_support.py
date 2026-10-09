"""RC-AUTH-13 B4 and actual model invocation evidence, separate from product APIs."""

import json
import os
from hashlib import sha256
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator

from aether_agent_memory.recall.contracts.models import AccessObserved, ContextPack, RecallRecord
from aether_agent_memory.remember.contracts.foundation import (
    CandidateQualificationResult,
    FullBodyReadResult,
)
from aether_agent_memory.runtime.contracts.models import (
    AuthorizationGrant,
    ContractModel,
    Digest,
    EventEnvelope,
    Identifier,
    Principal,
    TaskOperationView,
    Timestamp,
    TrustedContext,
)
from recall_forged_authorization_support import assert_no_index_disclosure
from recall_identity_reload_support import RequestEvidence, assert_request
from recall_shared_read_support import GrantReceipt, SavedSharedPack, SharedReadCase, external_path
from recall_shared_revoke_support import wait_receipt

IPC_PATH_FIELDS = (
    "control_request",
    "control_receipts",
    "success_directory",
    "maintenance_export",
    "barrier_directory",
    "barrier_receipts",
    "b4_export",
    "revoke_export",
    "model_export",
    "final_export",
)


class PrerankRevokeCase(SharedReadCase):
    initial_configuration_revision: int = Field(gt=0)
    b4_capability_id: Identifier | None = None
    model_probe_id: Identifier | None = None
    q06_mapping_id: Identifier | None = None
    barrier_directory: Path
    barrier_receipts: Path
    b4_export: Path
    revoke_export: Path
    model_export: Path
    final_export: Path

    @model_validator(mode="after")
    def prerank_fixture(self) -> "PrerankRevokeCase":
        if (
            self.server_settings.rerank_policy != "required"
            or not self.server_settings.reranker_revision
        ):
            raise ValueError("required actual reranker with pinned revision required")
        if self.grant.expires_at is not None:
            raise ValueError("nonexpiring grant required; expiry must not explain revocation")
        paths = [external_path(getattr(self, key)) for key in IPC_PATH_FIELDS]
        if any(
            a == b or a in b.parents or b in a.parents
            for index, a in enumerate(paths)
            for b in paths[index + 1 :]
        ):
            raise ValueError("independent, nonnested IPC paths required")
        return self


class ModelDocument(ContractModel):
    """Digest of the full actual string at the model boundary, never desired input."""

    memory_keys: tuple[str, ...]
    body_hashes: tuple[Digest, ...]
    document_hash: Digest


class ModelCall(ContractModel):
    call_id: Identifier
    operation_id: Identifier
    job_id: Identifier
    recall_id: Identifier
    trace_id: str
    query_hash: Digest
    started_at: Timestamp
    completed_at: Timestamp
    outcome: Literal["succeeded", "failed"]
    documents: tuple[ModelDocument, ...] = Field(min_length=1)


class ModelObservation(RequestEvidence):
    model_probe_id: Identifier
    q06_mapping_id: Identifier
    boundary: Literal["reranker.model.invoke"]
    model_id: str
    model_revision: str
    window_opened_at: Timestamp
    terminal_at: Timestamp
    sealed_at: Timestamp
    complete: bool
    task_terminal: bool
    calls: tuple[ModelCall, ...]


class B4Observation(RequestEvidence):
    b4_capability_id: Identifier
    barrier_id: Identifier
    position: Literal["after_full_body_before_prerank_authorization"]
    held: bool
    admitted_at: Timestamp
    context: TrustedContext
    consumed_grants: tuple[AuthorizationGrant, ...]
    qualification: CandidateQualificationResult
    bodies: tuple[FullBodyReadResult, ...]
    events: tuple[EventEnvelope, ...]
    model_invocations: int = Field(ge=0)
    model_probe_id: Identifier


class GrantRevocation(RequestEvidence):
    control_operation_id: Identifier
    b4_evidence_id: Identifier
    barrier_id: Identifier
    q18_capability_id: Identifier
    entrypoint: Literal["Identity.provision"]
    configuration_revision: int = Field(gt=0)
    submitted_at: Timestamp
    committed_at: Timestamp
    effective_at: Timestamp
    revoked_grant: AuthorizationGrant
    principals: dict[str, Principal]
    grants_for_u08: tuple[AuthorizationGrant, ...]
    grants_for_u09: tuple[AuthorizationGrant, ...]
    qualification: CandidateQualificationResult
    checked_principal: Principal
    qualification_checked_at: Timestamp


class PrerankFinal(RequestEvidence):
    q06_mapping_id: Identifier
    barrier_id: Identifier
    revoke_evidence_id: Identifier
    released_at: Timestamp
    prerank_checked_at: Timestamp
    checked_principal: Principal
    qualification: CandidateQualificationResult
    job: TaskOperationView
    record: RecallRecord
    pack: ContextPack | None
    events: tuple[EventEnvelope, ...]
    outbox_events: tuple[EventEnvelope, ...]


class BarrierReceipt(ContractModel):
    run_id: Identifier
    control_operation_id: Identifier
    operation_id: Identifier
    b4_capability_id: Identifier
    model_probe_id: Identifier
    armed: bool
    auto_release_seconds: Literal[120]


class PrerankCleanup(ContractModel):
    run_id: Identifier
    control_operation_id: Identifier
    released: bool
    request_drained: bool
    configuration_revision: int = Field(gt=0)
    principals: dict[str, Principal]
    grants_for_u08: tuple[AuthorizationGrant, ...]
    grants_for_u09: tuple[AuthorizationGrant, ...]
    evidence_id: Identifier


def publish(case: PrerankRevokeCase, action: str, **payload: object) -> None:
    directory = external_path(case.barrier_directory)
    directory.mkdir(parents=True, exist_ok=True)
    pending = directory / (action + ".pending")
    created = False
    try:
        with os.fdopen(
            os.open(pending, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w"
        ) as stream:
            created = True
            json.dump(
                {
                    "run_id": case.run_id,
                    "control_operation_id": action + "-" + case.run_id,
                    "action": action,
                    **payload,
                },
                stream,
            )
        os.link(pending, directory / (action + ".json"))
    finally:
        if created and pending.exists():
            pending.unlink()


def assert_binding(value: RequestEvidence, case: PrerankRevokeCase, observer: str) -> None:
    for key, expected in (
        ("run_id", case.run_id),
        ("observer_principal_id", observer),
        ("source_sha", case.source_sha),
        ("image_digest", case.image_digest),
        ("config_hash", case.configuration.config_hash),
        ("backend_binding", case.backend_binding),
        ("model_binding", case.model_binding),
    ):
        assert getattr(value, key) == expected, "foreign deployment/observer evidence"


def assert_events(
    events: tuple[EventEnvelope, ...], paused: B4Observation, case: PrerankRevokeCase
) -> None:
    assert len({e.event_id for e in events}) == len(events)
    reads = []
    for event in events:
        assert event.trace_id == paused.trace_id and event.initiator_id == "U08"
        if event.event_type != "recall.access":
            continue
        access = AccessObserved.model_validate(event.payload)
        assert access.recall_id == paused.recall_id
        assert event.subject.scope == access.memory.scope
        assert event.subject.object_id == access.memory.memory_id
        if access.memory == case.memories["shared"].memory:
            assert not (access.stage in {"packed", "delivered"} and access.outcome == "succeeded")
            if access.stage == "read" and access.outcome == "succeeded":
                reads.append(event)
    assert reads, "real authorized read must not be erased"


def assert_b4(
    value: B4Observation,
    case: PrerankRevokeCase,
    active: GrantReceipt,
    observer: str,
    operation_id: str,
    job_id: str,
) -> None:
    assert_binding(value, case, observer)
    assert (value.operation_id, value.job_id) == (operation_id, job_id)
    assert value.b4_capability_id == case.b4_capability_id and value.held
    assert value.model_probe_id == case.model_probe_id and value.model_invocations == 0
    assert value.context.principal == case.principals["U08"]
    assert value.context.operation_id == operation_id and value.context.trace_id == value.trace_id
    assert active.observed_at <= value.admitted_at <= value.observed_at < value.context.deadline_at
    assert value.consumed_grants == (case.grant,)
    q = value.qualification
    assert q.target == active.qualification.target and q.decision == "allowed"
    assert q.guard is not None and q.manifest is not None
    assert len(value.bodies) == 1
    body, memory = value.bodies[0], case.memories["shared"]
    assert body.memory == memory.memory and body.content == memory.text
    assert body.sources == memory.sources and body.guard == q.guard
    assert body.guard.body_hash == memory.body_hash
    assert body.location is not None and body.location.generation == memory.body_generation
    assert body.guard.checked_at <= value.observed_at
    assert_events(value.events, value, case)


def assert_revoked(
    value: GrantRevocation,
    paused: B4Observation,
    case: PrerankRevokeCase,
    active: GrantReceipt,
    observer: str,
) -> None:
    assert_binding(value, case, observer)
    assert_request(value, paused)
    assert value.control_operation_id == "revoke-" + case.run_id
    assert value.barrier_id == paused.barrier_id and value.b4_evidence_id == paused.evidence_id
    assert value.q18_capability_id == case.q18_capability_id and value.revoked_grant == case.grant
    assert value.configuration_revision > active.configuration_revision
    assert paused.observed_at <= value.submitted_at <= value.committed_at
    assert (
        value.committed_at <= value.effective_at <= value.observed_at < paused.context.deadline_at
    )
    assert value.principals == case.principals
    assert not value.grants_for_u08 and not value.grants_for_u09
    assert value.checked_principal == case.principals["U08"]
    assert value.effective_at <= value.qualification_checked_at <= value.observed_at
    assert_excluded(value.qualification, paused)


def assert_excluded(qualification: CandidateQualificationResult, paused: B4Observation) -> None:
    assert qualification.target == paused.qualification.target
    assert qualification.decision == "excluded"
    assert qualification.guard is None and qualification.manifest is None


def assert_model(
    value: ModelObservation,
    case: PrerankRevokeCase,
    observer: str,
    original: B4Observation | SavedSharedPack,
    *,
    control: bool,
) -> None:
    assert_binding(value, case, observer)
    if isinstance(original, B4Observation):
        assert_request(value, original)
        assert value.window_opened_at <= original.admitted_at
        assert original.observed_at < value.terminal_at
    else:
        assert (value.operation_id, value.job_id, value.recall_id, value.trace_id) == (
            original.operation_id,
            original.job_id,
            original.pack.recall_id,
            original.trace_id,
        )
    assert (
        value.model_probe_id == case.model_probe_id and value.q06_mapping_id == case.q06_mapping_id
    )
    assert value.model_id == case.server_settings.reranker_model
    assert value.model_revision == case.server_settings.reranker_revision
    assert value.complete and value.task_terminal, (
        "an empty, unsealed probe is not negative evidence"
    )
    assert value.window_opened_at <= value.terminal_at <= value.sealed_at <= value.observed_at
    assert len({c.call_id for c in value.calls}) == len(value.calls)
    allowed = case.memories["shared" if control else "u08-local"]
    if control:
        assert value.calls and any(c.outcome == "succeeded" for c in value.calls)
    for call in value.calls:
        assert (call.operation_id, call.job_id, call.recall_id, call.trace_id) == (
            value.operation_id,
            value.job_id,
            value.recall_id,
            value.trace_id,
        )
        assert call.query_hash == sha256(case.query.encode()).hexdigest()
        assert value.window_opened_at <= call.started_at <= call.completed_at <= value.sealed_at
        for document in call.documents:
            # Canonical full Ref; observe actual model strings before tokenization.
            assert document.memory_keys == (allowed.memory.model_dump_json(),)
            assert document.body_hashes == (allowed.body_hash,)
            assert document.document_hash == sha256(allowed.text.encode()).hexdigest()


def assert_final(
    value: PrerankFinal,
    model: ModelObservation,
    paused: B4Observation,
    revoked: GrantRevocation,
    case: PrerankRevokeCase,
    observer: str,
) -> None:
    assert_binding(value, case, observer)
    assert_request(value, paused)
    assert value.q06_mapping_id == case.q06_mapping_id and value.barrier_id == paused.barrier_id
    assert value.revoke_evidence_id == revoked.evidence_id
    assert revoked.observed_at <= value.released_at <= value.prerank_checked_at <= value.observed_at
    assert value.prerank_checked_at < paused.context.deadline_at
    assert value.checked_principal == case.principals["U08"]
    assert_excluded(value.qualification, paused)
    assert_model(model, case, observer, paused, control=False)
    assert value.prerank_checked_at <= model.sealed_at and model.observed_at <= value.observed_at
    job, record = value.job, value.record
    assert job.task_id == paused.job_id and job.idempotency_key == paused.operation_id
    assert (
        job.initiator_id == "U08" and job.initiator_auth_epoch == case.principals["U08"].auth_epoch
    )
    assert (
        record.recall_id == paused.recall_id and record.scope == case.principals["U08"].home_scope
    )
    if job.state == "failed":
        assert job.error_code is not None and job.error_code.value in {
            "FORBIDDEN",
            "RESULT_INVALIDATED",
        }
        assert job.result_ref is None and value.pack is None
        assert record.state == "failed" and not record.result_available
    else:
        assert job.state == "succeeded" and job.result_ref is not None and value.pack is not None
        assert record.state == "completed" and record.result_available
        assert value.pack.recall_id == paused.recall_id and value.pack.scope == record.scope
        assert value.pack.outcome in {"available", "empty"}
        assert value.pack.coverage.working == "complete" and not value.pack.degradation_reasons
        private = tuple(
            atom
            for key, memory in case.memories.items()
            if key != "u08-local"
            for atom in (memory.memory.memory_id, memory.text)
        )
        assert_no_index_disclosure(value.pack.model_dump(mode="json"), private)
    assert_events(value.events, paused, case)
    after = {e.event_id: e for e in value.events}
    assert all(after.get(e.event_id) == e for e in paused.events), "read history rewritten"
    assert_events(value.outbox_events, paused, case)
    assert {e.event_id: e for e in value.outbox_events} == after


def cleanup(case: PrerankRevokeCase, revision: int) -> PrerankCleanup:
    publish(case, "cleanup", release_all=True, drain_or_cancel=True, remove_test_grant=True)
    receipt = wait_receipt(
        case.barrier_receipts, "control_operation_id", "cleanup-" + case.run_id, PrerankCleanup
    )
    assert receipt.run_id == case.run_id and receipt.released and receipt.request_drained
    assert receipt.configuration_revision >= revision and receipt.principals == case.principals
    assert not receipt.grants_for_u08 and not receipt.grants_for_u09
    return receipt
