"""RC-AUTH-11 live lifecycle IPC and authoritative evidence contracts."""

import json
import os
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator

from aether_agent_memory.recall.contracts.models import AccessObserved, RecallRecord
from aether_agent_memory.remember.contracts.foundation import (
    CandidateQualificationResult,
    GuardStamp,
)
from aether_agent_memory.remember.contracts.models import MemoryRef
from aether_agent_memory.runtime.contracts.models import (
    ContractModel,
    Digest,
    EventEnvelope,
    Identifier,
    Permission,
    Principal,
    TaskOperationView,
    Timestamp,
    TrustedContext,
)
from aether_agent_memory.runtime.flows.config import IdentityEntry, Tenant
from recall_shared_read_support import SavedSharedPack, external_path
from recall_shared_revoke_support import pack_hash, wait_receipt
from recall_tenant_isolation_support import F4CaseData, F4StageObservation

Phase = Literal["enabled", "disabled", "reenabled"]
VARIANTS = ("recall-result", "operation-result")


class TenantLifecycleCase(F4CaseData):
    old_principal: Principal
    new_principal: Principal
    control_principal: Principal
    new_credential_env: Identifier
    initial_identity_revision: int = Field(gt=0)
    identity_instances: tuple[Identifier, ...] = Field(min_length=1)
    q18_capability_id: Identifier | None = None
    control_directory: Path
    receipt_export: Path
    phase_export: Path
    success_directory: Path

    @model_validator(mode="after")
    def lifecycle_subject(self) -> "TenantLifecycleCase":
        own, control = self.actors
        if (own.name, control.name) != ("U01", "U04"):
            raise ValueError("ordered lifecycle U01 and unaffected U04 controls required")
        if own.memory.scope.tenant_id == control.memory.scope.tenant_id:
            raise ValueError("unaffected control must be a separate tenant")
        if own.text == control.text or own.memory.memory_id == control.memory.memory_id:
            raise ValueError("independent private body and ID canaries required")
        for principal, actor in ((self.old_principal, own), (self.control_principal, control)):
            if principal.principal_id != actor.name or principal.home_scope != actor.memory.scope:
                raise ValueError("successful controls must bind actual identities/home scopes")
            if principal.permissions != (Permission.READ,):
                raise ValueError("ordinary READ-only identities required")
        if (
            self.new_principal.model_dump(exclude={"auth_epoch"})
            != self.old_principal.model_dump(exclude={"auth_epoch"})
            or self.new_principal.auth_epoch <= self.old_principal.auth_epoch
        ):
            raise ValueError("one principal must advance epoch, not two static identities")
        if self.new_credential_env in {own.credential_env, control.credential_env}:
            raise ValueError("old/new/control credential references must differ")
        if (
            not own.memory.scope.session_id
            or self.request.selection.model_dump(exclude_none=True)
            != {"session_id": own.memory.scope.session_id}
            or control.memory.scope.session_id != own.memory.scope.session_id
        ):
            raise ValueError("one shared query/session keeps selection from explaining isolation")
        if self.request.sources != "working" or self.server_settings.candidate_limit != 1:
            raise ValueError("Working K=1 successful controls required")
        if len(set(self.identity_instances)) != len(self.identity_instances):
            raise ValueError("duplicate identity instance")
        paths = [
            external_path(p)
            for p in (
                self.control_directory,
                self.receipt_export,
                self.phase_export,
                self.success_directory,
                self.maintenance_export,
            )
        ]
        if len(set(paths)) != len(paths):
            raise ValueError("independent control and observation paths required")
        return self


class LifecycleStage(F4StageObservation):
    principal: Principal
    trusted_context: TrustedContext
    qualification: CandidateQualificationResult
    identity_revision: int = Field(gt=0)


class IdentityReload(ContractModel):
    instance_id: Identifier
    identity_revision: int = Field(gt=0)
    identity_document_hash: Digest
    applied_at: Timestamp
    tenant: Tenant
    current_identity: IdentityEntry


class LifecycleReceipt(ContractModel):
    run_id: Identifier
    phase: Phase
    control_operation_id: Identifier
    previous_evidence_id: Identifier | None
    q18_capability_id: Identifier
    entrypoint: Literal["Service.reload_identity"]
    evidence_id: Identifier
    observer_principal_id: Identifier
    source_sha: str
    config_hash: Digest
    image_digest: str
    backend_binding: Digest
    model_binding: Digest
    submitted_at: Timestamp
    committed_at: Timestamp
    effective_at: Timestamp
    observed_at: Timestamp
    identity_revision: int = Field(gt=0)
    identity_document_hash: Digest
    tenant: Tenant
    current_identity: IdentityEntry
    control_identity: Principal
    control_tenant: Tenant
    tenant_epoch_fence: int = Field(ge=0)
    reloads: tuple[IdentityReload, ...] = Field(min_length=1)
    old_context: TrustedContext
    old_context_checked_at: Timestamp
    old_context_allowed: bool
    old_context_error: Literal["FORBIDDEN"] | None


def assert_binding(value: ContractModel, case: TenantLifecycleCase, observer: str) -> None:
    for field, expected in (
        ("run_id", case.run_id),
        ("observer_principal_id", observer),
        ("source_sha", case.source_sha),
        ("config_hash", case.configuration.config_hash),
        ("image_digest", case.image_digest),
        ("backend_binding", case.backend_binding),
        ("model_binding", case.model_binding),
    ):
        assert getattr(value, field) == expected, "foreign lifecycle/deployment observation"


def assert_lifecycle_receipt(
    receipt: LifecycleReceipt,
    case: TenantLifecycleCase,
    phase: Phase,
    previous: LifecycleReceipt | None,
    old_context: TrustedContext,
    credential_hash: str,
    observer: str,
) -> None:
    assert_binding(receipt, case, observer)
    assert (
        receipt.phase == phase and receipt.control_operation_id == f"tenant-{case.run_id}-{phase}"
    )
    assert receipt.q18_capability_id == case.q18_capability_id
    assert receipt.previous_evidence_id == (previous.evidence_id if previous else None)
    assert receipt.tenant.tenant_id == case.old_principal.home_scope.tenant_id
    assert receipt.tenant.enabled == (phase != "disabled")
    expected = case.new_principal if phase == "reenabled" else case.old_principal
    assert receipt.current_identity.principal == expected
    assert receipt.current_identity.credential_sha256 == credential_hash
    assert receipt.control_identity == case.control_principal
    assert receipt.control_tenant.tenant_id == case.control_principal.home_scope.tenant_id
    assert receipt.control_tenant.enabled
    assert (
        receipt.submitted_at <= receipt.committed_at <= receipt.effective_at <= receipt.observed_at
    )
    if previous is None:
        assert phase == "enabled" and receipt.identity_revision == case.initial_identity_revision
        assert receipt.tenant_epoch_fence < case.old_principal.auth_epoch
    else:
        assert (previous.phase, phase) in {("enabled", "disabled"), ("disabled", "reenabled")}
        assert receipt.identity_revision > previous.identity_revision
        assert previous.observed_at <= receipt.submitted_at
        assert receipt.identity_document_hash != previous.identity_document_hash
        assert receipt.tenant_epoch_fence >= case.old_principal.auth_epoch
        if phase == "reenabled":
            assert receipt.tenant_epoch_fence == previous.tenant_epoch_fence
            assert case.new_principal.auth_epoch > receipt.tenant_epoch_fence
    assert {r.instance_id for r in receipt.reloads} == set(case.identity_instances)
    assert len(receipt.reloads) == len(case.identity_instances), "duplicate/missing reload proof"
    for reload in receipt.reloads:
        assert reload.identity_revision == receipt.identity_revision
        assert reload.identity_document_hash == receipt.identity_document_hash
        assert (
            reload.tenant == receipt.tenant and reload.current_identity == receipt.current_identity
        )
        assert receipt.effective_at <= reload.applied_at <= receipt.observed_at
    assert receipt.old_context == old_context and old_context.principal == case.old_principal
    assert receipt.effective_at <= receipt.old_context_checked_at <= receipt.observed_at
    assert receipt.old_context_checked_at < old_context.deadline_at, (
        "timeout confounds epoch rejection"
    )
    assert receipt.old_context_allowed == (phase == "enabled")
    assert receipt.old_context_error == (None if phase == "enabled" else "FORBIDDEN")


def transition_tenant(
    case: TenantLifecycleCase,
    phase: Phase,
    previous: LifecycleReceipt | None,
    old_context: TrustedContext,
    credential_hash: str,
    observer: str,
    timeout: float = 30,
) -> LifecycleReceipt:
    if not case.q18_capability_id:
        raise RuntimeError("blocked_fixture: Q18 tenant lifecycle/identity reload unavailable")
    directory = external_path(case.control_directory)
    directory.mkdir(parents=True, exist_ok=True)
    operation_id = f"tenant-{case.run_id}-{phase}"
    path = directory / (phase + ".json")
    pending = directory / (phase + "." + case.run_id + ".pending")
    with pending.open("x", encoding="utf-8") as stream:
        json.dump(
            {
                "run_id": case.run_id,
                "phase": phase,
                "control_operation_id": operation_id,
                "previous_evidence_id": previous.evidence_id if previous else None,
                "q18_capability_id": case.q18_capability_id,
                "entrypoint": "Service.reload_identity",
                "action": "observe" if phase == "enabled" else "apply",
                "tenant_id": case.old_principal.home_scope.tenant_id,
                "enabled": phase != "disabled",
                "principal": (
                    case.new_principal if phase == "reenabled" else case.old_principal
                ).model_dump(mode="json"),
                "credential_env": case.new_credential_env
                if phase == "reenabled"
                else case.actors[0].credential_env,
                "old_context": old_context.model_dump(mode="json"),
            },
            stream,
        )
    os.link(pending, path)
    pending.unlink()
    receipt = wait_receipt(
        case.receipt_export, "control_operation_id", operation_id, LifecycleReceipt, timeout
    )
    assert_lifecycle_receipt(receipt, case, phase, previous, old_context, credential_hash, observer)
    return receipt


class DeniedRequestObservation(ContractModel):
    request_id: Identifier
    method: Literal["POST", "GET"]
    path: str
    operation_id: Identifier | None
    credential_sha256: Digest
    checked_at: Timestamp
    status: Literal[401, 403]
    code: Literal["UNAUTHENTICATED", "FORBIDDEN"]
    body_reads: tuple[MemoryRef, ...]
    model_input_refs: tuple[MemoryRef, ...]
    result_refs: tuple[MemoryRef, ...]
    search_invocations: int = Field(ge=0)
    events: tuple[EventEnvelope, ...]


class LifecyclePhaseObservation(ContractModel):
    run_id: Identifier
    control_operation_id: Identifier
    receipt_evidence_id: Identifier
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
    requests: tuple[DeniedRequestObservation, ...]
    denied_jobs: tuple[TaskOperationView, ...]


class OriginalLifecyclePack(ContractModel):
    saved: SavedSharedPack
    record: RecallRecord
    job: TaskOperationView
    stage: LifecycleStage


def assert_phase_denial(
    observation: LifecyclePhaseObservation,
    case: TenantLifecycleCase,
    receipt: LifecycleReceipt,
    original: OriginalLifecyclePack,
    requests: dict[str, tuple[str, str, str | None]],
    credential_hash: str,
    denied_job_ids: set[str],
    observer: str,
) -> None:
    assert_binding(observation, case, observer)
    assert observation.control_operation_id == receipt.control_operation_id
    assert observation.receipt_evidence_id == receipt.evidence_id
    assert (
        observation.original_record == original.record and observation.original_job == original.job
    )
    assert observation.original_guard == original.stage.qualification.guard
    assert observation.original_pack_hash == pack_hash(original.saved)
    assert observation.original_events == original.stage.events, "historical access facts rewritten"
    assert len(observation.requests) == len(requests)
    assert {
        r.request_id: (r.method, r.path, r.operation_id) for r in observation.requests
    } == requests
    expected_status, expected_code = (
        (403, "FORBIDDEN") if receipt.phase == "disabled" else (401, "UNAUTHENTICATED")
    )
    for row in observation.requests:
        assert row.credential_sha256 == credential_hash
        assert receipt.observed_at <= row.checked_at <= observation.observed_at
        assert (row.status, row.code) == (expected_status, expected_code)
        assert not row.body_reads and not row.model_input_refs and not row.result_refs
        assert row.search_invocations == 0, "denied path searched or replaced a saved result"
        for event in row.events:
            if event.event_type == "recall.access":
                payload = AccessObserved.model_validate(event.payload)
                assert not (
                    payload.outcome == "succeeded"
                    and payload.stage in {"read", "packed", "delivered"}
                )
    assert {job.task_id for job in observation.denied_jobs} == denied_job_ids
    assert len(observation.denied_jobs) == len(denied_job_ids)
    for job in observation.denied_jobs:
        assert job.state in {"failed", "cancelled"} and job.result_ref is None
        assert job.initiator_id == case.old_principal.principal_id
        assert job.initiator_auth_epoch == case.old_principal.auth_epoch
        assert job.error_code is not None and job.error_code.value == expected_code
        assert job.idempotency_key in {
            operation for method, _, operation in requests.values() if method == "POST"
        }, "denied job belongs to another operation"
