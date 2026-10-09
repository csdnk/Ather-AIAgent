"""RC-AUTH-12 test-side B5/Q18 IPC; never an invented product control API."""

import json
import os
from hashlib import sha256
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator

from aether_agent_memory.recall.contracts.foundation import ContextAssemblyPlan
from aether_agent_memory.recall.contracts.models import AccessObserved, ContextPack, RecallRecord
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
from aether_agent_memory.runtime.flows.config import IdentityEntry
from recall_authorization_support import F1_TEXTS
from recall_shared_read_support import external_path
from recall_shared_revoke_support import wait_receipt
from recall_tenant_isolation_support import F4CaseData
from recall_tenant_lifecycle_support import IdentityReload


class IdentityReloadCase(F4CaseData):
    principal: Principal
    initial_identity_revision: int = Field(gt=0)
    initial_identity_hash: Digest
    unchanged_identity_state_hash: Digest
    identity_instances: tuple[Identifier, ...] = Field(min_length=1)
    b5_capability_id: Identifier | None = None
    q18_capability_id: Identifier | None = None
    q06_capability_id: Identifier | None = None
    control_directory: Path
    receipt_export: Path
    final_export: Path

    @model_validator(mode="after")
    def f1_and_paths(self) -> "IdentityReloadCase":
        if (
            self.principal.principal_id != "U01"
            or Permission.READ not in self.principal.permissions
        ):
            raise ValueError("U01 READ identity required")
        if tuple(a.text for a in self.actors) != F1_TEXTS:
            raise ValueError("exact F1 coffee and tea memories required")
        for actor in self.actors:
            if actor.name != "U01" or actor.memory.scope != self.principal.home_scope:
                raise ValueError("both F1 memories must belong to U01's exact scope")
        if len({a.memory.memory_id for a in self.actors}) != 2:
            raise ValueError("two independent F1 memories required")
        if len({a.credential_env for a in self.actors}) != 1:
            raise ValueError("one original U01 credential required")
        if (
            self.request.sources != "working"
            or not self.principal.home_scope.session_id
            or self.request.selection.model_dump(exclude_none=True)
            != {"session_id": self.principal.home_scope.session_id}
        ):
            raise ValueError("F1 Working request must select the exact session")
        paths = [
            external_path(getattr(self, key))
            for key in ("control_directory", "receipt_export", "final_export", "maintenance_export")
        ]
        if len(set(paths)) != len(paths):
            raise ValueError("independent IPC/export paths required")
        if len(set(self.identity_instances)) != len(self.identity_instances):
            raise ValueError("duplicate reload instance")
        return self

    def revoked_principal(self) -> Principal:
        return self.principal.model_copy(
            update={
                "permissions": tuple(p for p in self.principal.permissions if p != Permission.READ),
                "auth_epoch": self.principal.auth_epoch + 1,
            }
        )


class RequestEvidence(ContractModel):
    run_id: Identifier
    evidence_id: Identifier
    observer_principal_id: Identifier
    source_sha: str
    config_hash: Digest
    image_digest: str
    backend_binding: Digest
    model_binding: Digest
    operation_id: Identifier
    job_id: Identifier
    recall_id: Identifier
    trace_id: str
    observed_at: Timestamp


class AssemblyObservation(RequestEvidence):
    phase: Literal["control", "B5"]
    b5_capability_id: Identifier | None
    barrier_id: Identifier | None
    held: bool
    position: Literal["after_assemble_before_final_guard", "completed"]
    context: TrustedContext
    identity_revision: int = Field(gt=0)
    pack: ContextPack | None
    plan: ContextAssemblyPlan | None
    body_reads: tuple[Identifier, ...]
    events: tuple[EventEnvelope, ...]
    final_guard_invocations: int = Field(ge=0)
    successful_commits: int = Field(ge=0)


class ReadRevocationReceipt(RequestEvidence):
    control_operation_id: Identifier
    barrier_id: Identifier
    b5_evidence_id: Identifier
    q18_capability_id: Identifier
    entrypoint: Literal["Service.reload_identity"]
    submitted_at: Timestamp
    committed_at: Timestamp
    effective_at: Timestamp
    identity_revision: int = Field(gt=0)
    identity_document_hash: Digest
    current_identity: IdentityEntry
    unchanged_identity_state_hash: Digest
    reloads: tuple[IdentityReload, ...] = Field(min_length=1)


class FinalObservation(RequestEvidence):
    q06_capability_id: Identifier
    barrier_id: Identifier
    revoke_evidence_id: Identifier
    released_at: Timestamp
    final_checked_at: Timestamp
    current_principal: Principal
    identity_revision: int = Field(gt=0)
    final_check: Literal["final_guard", "RF"]
    final_error: Literal["FORBIDDEN"]
    conditional_commit_attempts: int = Field(ge=0)
    successful_commits: int = Field(ge=0)
    persisted_pack: ContextPack | None
    result_refs: tuple[Identifier, ...]
    record: RecallRecord
    job: TaskOperationView
    events: tuple[EventEnvelope, ...]
    outbox_events: tuple[EventEnvelope, ...]


class CleanupReceipt(ContractModel):
    run_id: Identifier
    control_operation_id: Identifier
    evidence_id: Identifier
    released: bool
    request_drained: bool
    identity_restored: bool
    identity_revision: int = Field(gt=0)
    identity_document_hash: Digest
    restored_identity: IdentityEntry
    reloads: tuple[IdentityReload, ...] = Field(min_length=1)


class ArmedReceipt(ContractModel):
    run_id: Identifier
    control_operation_id: Identifier
    operation_id: Identifier
    b5_capability_id: Identifier
    armed: bool
    position: Literal["after_assemble_before_final_guard"]


def publish(case: IdentityReloadCase, action: str, **payload: object) -> None:
    directory = external_path(case.control_directory)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / (action + ".json")
    pending = path.with_suffix(".pending")
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
        os.link(pending, path)  # Refuse stale fixture reuse/overwriting another execution.
    finally:
        if created and pending.exists():
            pending.unlink()


def assert_binding(value: RequestEvidence, case: IdentityReloadCase, observer: str) -> None:
    for key, expected in (
        ("run_id", case.run_id),
        ("observer_principal_id", observer),
        ("source_sha", case.source_sha),
        ("config_hash", case.configuration.config_hash),
        ("image_digest", case.image_digest),
        ("backend_binding", case.backend_binding),
        ("model_binding", case.model_binding),
    ):
        assert getattr(value, key) == expected, "foreign deployment/observer evidence"


def assert_request(value: RequestEvidence, original: RequestEvidence) -> None:
    for key in ("operation_id", "job_id", "recall_id", "trace_id"):
        assert getattr(value, key) == getattr(original, key), "original request binding differs"


def assert_pack(pack: ContextPack, case: IdentityReloadCase) -> None:
    assert pack.outcome == "available" and pack.scope == case.principal.home_scope
    items = [item for group in pack.groups for item in group.items]
    assert len(items) == 2
    for actor in case.actors:
        matches = [item for item in items if item.memory == actor.memory]
        assert len(matches) == 1 and matches[0].content == actor.text
        assert matches[0].sources == actor.sources and actor.text in pack.rendered_context


def assert_events(
    events: tuple[EventEnvelope, ...],
    original: AssemblyObservation,
    case: IdentityReloadCase,
    *,
    blocked: bool,
) -> None:
    assert len({e.event_id for e in events}) == len(events), "duplicate access events"
    reads = set()
    for event in events:
        assert event.trace_id == original.trace_id and event.initiator_id == "U01"
        if event.event_type != "recall.access":
            continue
        access = AccessObserved.model_validate(event.payload)
        assert access.recall_id == original.recall_id
        assert access.memory in tuple(a.memory for a in case.actors)
        assert event.subject.scope == access.memory.scope
        if access.stage == "read" and access.outcome == "succeeded":
            reads.add(access.memory.memory_id)
        if blocked:
            assert not (access.stage in {"packed", "delivered"} and access.outcome == "succeeded")
    assert reads == {a.memory.memory_id for a in case.actors}, "missing real F1 read evidence"


def assert_assembly(
    value: AssemblyObservation,
    case: IdentityReloadCase,
    observer: str,
    operation_id: str,
    job_id: str,
    *,
    paused: bool,
) -> None:
    assert_binding(value, case, observer)
    assert (value.operation_id, value.job_id) == (operation_id, job_id)
    assert value.context.principal == case.principal
    assert value.context.operation_id == operation_id and value.context.trace_id == value.trace_id
    assert value.context.deadline_at > value.observed_at
    assert value.identity_revision == case.initial_identity_revision
    assert set(value.body_reads) == {a.memory.memory_id for a in case.actors}
    assert value.held == paused and value.phase == ("B5" if paused else "control")
    assert value.position == ("after_assemble_before_final_guard" if paused else "completed")
    if paused:
        assert value.pack is None, "B5 precedes persisted ContextPack creation"
        plan = value.plan
        assert plan is not None and plan.request.recall_id == value.recall_id
        assert plan.scope == case.principal.home_scope
        assert plan.request.query == case.request.query
        assert plan.request.selection == case.request.selection
        assert plan.request.sources == ("working",)
        assert plan.request.deadline_at == value.context.deadline_at
        bodies = [body for unit in plan.units for body in unit.bodies]
        assert len(bodies) == 2
        for actor in case.actors:
            matches = [body for body in bodies if body.memory == actor.memory]
            assert len(matches) == 1 and matches[0].content == actor.text
            body = matches[0]
            assert body.sources == actor.sources and body.guard is not None
            assert body.guard.authorization_epoch == case.principal.auth_epoch
            assert body.guard.body_hash == actor.body_hash
            assert body.location is not None and body.location.generation == actor.body_generation
        assert value.barrier_id and value.b5_capability_id == case.b5_capability_id
        assert value.final_guard_invocations == value.successful_commits == 0
    else:
        assert value.pack is not None and value.pack.recall_id == value.recall_id
        assert_pack(value.pack, case)
        assert value.final_guard_invocations > 0 and value.successful_commits == 1
        packed = {
            AccessObserved.model_validate(e.payload).memory.memory_id
            for e in value.events
            if e.event_type == "recall.access"
            and e.payload["stage"] == "packed"
            and e.payload["outcome"] == "succeeded"
        }
        assert packed == {a.memory.memory_id for a in case.actors}
    assert_events(value.events, value, case, blocked=paused)


def assert_revocation(
    receipt: ReadRevocationReceipt,
    original: AssemblyObservation,
    case: IdentityReloadCase,
    observer: str,
    token: str,
) -> None:
    assert_binding(receipt, case, observer)
    assert_request(receipt, original)
    assert receipt.control_operation_id == "revoke-" + case.run_id
    assert receipt.barrier_id == original.barrier_id
    assert receipt.b5_evidence_id == original.evidence_id
    assert receipt.q18_capability_id == case.q18_capability_id
    assert original.observed_at <= receipt.submitted_at <= receipt.committed_at
    assert receipt.committed_at <= receipt.effective_at <= receipt.observed_at
    assert receipt.observed_at < original.context.deadline_at, "timeout confounds revocation"
    assert receipt.identity_revision > case.initial_identity_revision
    assert receipt.identity_document_hash != case.initial_identity_hash
    assert receipt.current_identity.principal == case.revoked_principal()
    assert receipt.unchanged_identity_state_hash == case.unchanged_identity_state_hash
    assert receipt.current_identity.credential_sha256 == sha256(token.encode()).hexdigest()
    assert {r.instance_id for r in receipt.reloads} == set(case.identity_instances)
    assert len(receipt.reloads) == len(case.identity_instances)
    for reload in receipt.reloads:
        assert reload.identity_revision == receipt.identity_revision
        assert reload.identity_document_hash == receipt.identity_document_hash
        assert reload.current_identity == receipt.current_identity
        assert (
            reload.tenant.enabled and reload.tenant.tenant_id == case.principal.home_scope.tenant_id
        )
        assert receipt.effective_at <= reload.applied_at <= receipt.observed_at


def assert_final(
    value: FinalObservation,
    original: AssemblyObservation,
    receipt: ReadRevocationReceipt,
    case: IdentityReloadCase,
    observer: str,
) -> None:
    assert_binding(value, case, observer)
    assert_request(value, original)
    assert value.q06_capability_id == case.q06_capability_id
    assert (
        value.barrier_id == original.barrier_id and value.revoke_evidence_id == receipt.evidence_id
    )
    assert receipt.observed_at <= value.released_at <= value.final_checked_at <= value.observed_at
    assert value.final_checked_at < original.context.deadline_at, "timeout is not a READ rejection"
    assert value.current_principal == case.revoked_principal()
    assert value.identity_revision == receipt.identity_revision
    assert value.successful_commits == 0 and value.persisted_pack is None and not value.result_refs
    assert value.record.recall_id == original.recall_id
    assert value.record.scope == case.principal.home_scope
    assert value.record.state == "failed" and not value.record.result_available
    job = value.job
    assert job.task_id == original.job_id and job.idempotency_key == original.operation_id
    assert job.initiator_id == "U01" and job.initiator_auth_epoch == case.principal.auth_epoch
    assert job.state == "failed" and job.result_ref is None
    assert job.error_code is not None and job.error_code.value == "FORBIDDEN"
    assert_events(value.events, original, case, blocked=True)
    before = {e.event_id: e for e in original.events}
    after = {e.event_id: e for e in value.events}
    assert all(after.get(key) == event for key, event in before.items()), (
        "real read history rewritten"
    )
    assert_events(value.outbox_events, original, case, blocked=True)
    assert {e.event_id: e for e in value.outbox_events} == after, "Outbox/event evidence differs"


def cleanup(case: IdentityReloadCase, previous_revision: int | None = None) -> CleanupReceipt:
    publish(case, "cleanup", release_all=True, drain_or_cancel=True, restore_identity=True)
    receipt = wait_receipt(
        case.receipt_export,
        "control_operation_id",
        "cleanup-" + case.run_id,
        CleanupReceipt,
        timeout=30,
    )
    assert receipt.run_id == case.run_id
    assert receipt.released and receipt.request_drained and receipt.identity_restored
    assert receipt.identity_revision > (previous_revision or case.initial_identity_revision)
    restored = receipt.restored_identity.principal
    assert restored.model_dump(exclude={"auth_epoch"}) == case.principal.model_dump(
        exclude={"auth_epoch"}
    )
    assert restored.auth_epoch > case.revoked_principal().auth_epoch
    assert receipt.restored_identity.credential_sha256 is not None
    assert len(receipt.reloads) == len(case.identity_instances)
    assert {r.instance_id for r in receipt.reloads} == set(case.identity_instances)
    for reload in receipt.reloads:
        assert reload.identity_revision == receipt.identity_revision
        assert reload.identity_document_hash == receipt.identity_document_hash
        assert reload.current_identity == receipt.restored_identity
        assert reload.tenant.enabled
        assert reload.tenant.tenant_id == case.principal.home_scope.tenant_id
    return receipt
