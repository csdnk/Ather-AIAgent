"""RC-AUTH-11 strict lifecycle observations, separate from live acceptance."""

import json
from copy import deepcopy
from hashlib import sha256

import pytest

from aether_agent_memory.recall.contracts.models import ContextPack, RecallRecord
from aether_agent_memory.runtime.contracts.models import TaskOperationView, TrustedContext
from aether_agent_memory.runtime.foundation.common import fingerprint
from recall_shared_read_support import SavedSharedPack
from recall_shared_revoke_support import pack_hash
from recall_tenant_lifecycle_support import (
    LifecyclePhaseObservation,
    LifecycleReceipt,
    LifecycleStage,
    OriginalLifecyclePack,
    TenantLifecycleCase,
    assert_lifecycle_receipt,
    assert_phase_denial,
    transition_tenant,
)
from unit.test_recall_shared_read_support import qualification
from unit.test_recall_tenant_isolation_support import maintained_case_and_stage, pack

OLD_HASH = sha256(b"old-credential").hexdigest()
NEW_HASH = sha256(b"new-credential").hexdigest()


def lifecycle_case(tmp_path):
    original, _ = maintained_case_and_stage()
    raw = original.model_dump(mode="json")
    for index, actor in enumerate(raw["actors"]):
        actor["memory"]["memory_id"] = ("owned-memory", "foreign-memory")[index]
    principals = [
        {
            "principal_id": actor["name"],
            "home_scope": actor["memory"]["scope"],
            "permissions": ["memory:read"],
            "auth_epoch": 1,
        }
        for actor in raw["actors"]
    ]
    raw.update(
        old_principal=principals[0],
        new_principal={**principals[0], "auth_epoch": 2},
        control_principal=principals[1],
        new_credential_env="NEW_TOKEN",
        initial_identity_revision=1,
        identity_instances=["http-1", "worker-1"],
        q18_capability_id="owned-lifecycle-controller",
    )
    for field in (
        "control_directory",
        "receipt_export",
        "phase_export",
        "success_directory",
        "maintenance_export",
    ):
        raw[field] = str(tmp_path / field)
    return TenantLifecycleCase.model_validate(raw)


def old_context(case):
    return TrustedContext(
        principal=case.old_principal,
        request_id="original-request",
        operation_id="original-op",
        trace_id="1" * 32,
        span_id="2" * 16,
        deadline_at="2026-10-09T00:10:00.000Z",
    )


def receipt_data(case, phase, previous=None):
    revision = {"enabled": 1, "disabled": 2, "reenabled": 3}[phase]
    timestamp = f"2026-10-09T00:00:0{revision}.000Z"
    principal = case.new_principal if phase == "reenabled" else case.old_principal
    identity = {
        "principal": principal.model_dump(mode="json"),
        "credential_sha256": NEW_HASH if phase == "reenabled" else OLD_HASH,
    }
    tenant = {"tenant_id": case.old_principal.home_scope.tenant_id, "enabled": phase != "disabled"}
    data = {
        "run_id": case.run_id,
        "phase": phase,
        "control_operation_id": f"tenant-{case.run_id}-{phase}",
        "previous_evidence_id": previous.evidence_id if previous else None,
        "q18_capability_id": case.q18_capability_id,
        "entrypoint": "Service.reload_identity",
        "evidence_id": "receipt-" + phase,
        "observer_principal_id": "operator",
        "source_sha": case.source_sha,
        "config_hash": case.configuration.config_hash,
        "image_digest": case.image_digest,
        "backend_binding": case.backend_binding,
        "model_binding": case.model_binding,
        "submitted_at": timestamp,
        "committed_at": timestamp,
        "effective_at": timestamp,
        "observed_at": timestamp,
        "identity_revision": revision,
        "identity_document_hash": str(revision) * 64,
        "tenant": tenant,
        "current_identity": identity,
        "control_identity": case.control_principal.model_dump(mode="json"),
        "control_tenant": {
            "tenant_id": case.control_principal.home_scope.tenant_id,
            "enabled": True,
        },
        "tenant_epoch_fence": 0 if phase == "enabled" else 1,
        "reloads": [
            {
                "instance_id": instance,
                "identity_revision": revision,
                "identity_document_hash": str(revision) * 64,
                "applied_at": timestamp,
                "tenant": tenant,
                "current_identity": identity,
            }
            for instance in case.identity_instances
        ],
        "old_context": old_context(case).model_dump(mode="json"),
        "old_context_checked_at": timestamp,
        "old_context_allowed": phase == "enabled",
        "old_context_error": None if phase == "enabled" else "FORBIDDEN",
    }
    return deepcopy(data)


def lifecycle_receipts(case):
    enabled = LifecycleReceipt.model_validate(receipt_data(case, "enabled"))
    disabled = LifecycleReceipt.model_validate(receipt_data(case, "disabled", enabled))
    reenabled = LifecycleReceipt.model_validate(receipt_data(case, "reenabled", disabled))
    return enabled, disabled, reenabled


def test_actual_lifecycle_advances_one_identity_and_fences_old_context_after_reactivation(tmp_path):
    case = lifecycle_case(tmp_path)
    previous = None
    for receipt in lifecycle_receipts(case):
        assert_lifecycle_receipt(
            receipt,
            case,
            receipt.phase,
            previous,
            old_context(case),
            NEW_HASH if receipt.phase == "reenabled" else OLD_HASH,
            "operator",
        )
        previous = receipt


@pytest.mark.parametrize(
    "fault",
    [
        "tenant-still-enabled",
        "stale-revision",
        "unchanged-document",
        "missing-reload",
        "old-worker",
        "wrong-tenant",
        "context-revives",
        "timeout",
        "two-static-identities",
        "wrong-credential",
        "unfenced",
        "foreign-observer",
        "missing-chain",
    ],
)
def test_lifecycle_cannot_pass_on_static_config_or_incomplete_reload(tmp_path, fault):
    case = lifecycle_case(tmp_path)
    enabled, disabled, reenabled = lifecycle_receipts(case)
    target = disabled if fault == "tenant-still-enabled" else reenabled
    previous = enabled if target.phase == "disabled" else disabled
    raw = target.model_dump(mode="json")
    if fault == "tenant-still-enabled":
        raw["tenant"]["enabled"] = True
    elif fault == "stale-revision":
        raw["identity_revision"] = previous.identity_revision
    elif fault == "unchanged-document":
        raw["identity_document_hash"] = previous.identity_document_hash
    elif fault == "missing-reload":
        raw["reloads"].pop()
    elif fault == "old-worker":
        raw["reloads"][1]["identity_revision"] = 1
    elif fault == "wrong-tenant":
        raw["tenant"]["tenant_id"] = "foreign"
    elif fault == "context-revives":
        raw["old_context_allowed"] = True
    elif fault == "timeout":
        raw["old_context_checked_at"] = "2026-10-09T00:11:00.000Z"
        raw["observed_at"] = raw["old_context_checked_at"]
    elif fault == "two-static-identities":
        raw["current_identity"]["principal"]["principal_id"] = "U-new"
    elif fault == "wrong-credential":
        raw["current_identity"]["credential_sha256"] = OLD_HASH
    elif fault == "unfenced":
        raw["tenant_epoch_fence"] = 0
    elif fault == "foreign-observer":
        raw["observer_principal_id"] = "foreign"
    else:
        raw["previous_evidence_id"] = "unrelated-transition"
    with pytest.raises((ValueError, AssertionError)):
        assert_lifecycle_receipt(
            LifecycleReceipt.model_validate(raw),
            case,
            target.phase,
            previous,
            old_context(case),
            OLD_HASH if target.phase == "disabled" else NEW_HASH,
            "operator",
        )


def test_lifecycle_requests_are_chained_single_writes_and_wait_for_actual_reload(tmp_path):
    case = lifecycle_case(tmp_path)
    receipts = lifecycle_receipts(case)
    case.receipt_export.write_text(json.dumps([r.model_dump(mode="json") for r in receipts]))
    previous = None
    for receipt in receipts:
        actual = transition_tenant(
            case,
            receipt.phase,
            previous,
            old_context(case),
            NEW_HASH if receipt.phase == "reenabled" else OLD_HASH,
            "operator",
            timeout=0,
        )
        assert actual == receipt
        payload = json.loads((case.control_directory / (receipt.phase + ".json")).read_text())
        assert payload["principal"]["principal_id"] == "U01"
        assert payload["action"] == ("observe" if receipt.phase == "enabled" else "apply")
        assert payload["previous_evidence_id"] == (previous.evidence_id if previous else None)
        previous = actual


def test_missing_authoritative_reload_is_blocked_not_assumed_effective(tmp_path):
    case = lifecycle_case(tmp_path)
    with pytest.raises(RuntimeError, match="blocked_fixture.*receipt"):
        transition_tenant(case, "enabled", None, old_context(case), OLD_HASH, "operator", timeout=0)


def test_missing_q18_does_not_publish_a_fake_lifecycle(tmp_path):
    case = lifecycle_case(tmp_path).model_copy(update={"q18_capability_id": None})
    with pytest.raises(RuntimeError, match="blocked_fixture.*Q18"):
        transition_tenant(case, "enabled", None, old_context(case), OLD_HASH, "operator", timeout=0)
    assert not case.control_directory.exists()


@pytest.mark.parametrize(
    "fault", ["second-identity", "same-epoch", "same-token-reference", "no-read"]
)
def test_new_epoch_fixture_must_advance_the_same_legal_subject(tmp_path, fault):
    raw = lifecycle_case(tmp_path).model_dump(mode="json")
    if fault == "second-identity":
        raw["new_principal"]["principal_id"] = "another-reader"
    elif fault == "same-epoch":
        raw["new_principal"]["auth_epoch"] = 1
    elif fault == "same-token-reference":
        raw["new_credential_env"] = raw["actors"][0]["credential_env"]
    else:
        raw["old_principal"]["permissions"] = ["memory:write"]
    with pytest.raises(ValueError):
        TenantLifecycleCase.model_validate(raw)


def original_pack(case):
    actor = case.actors[0]
    payload = pack(content=actor.text, refs=[actor.memory.model_dump(mode="json")])
    payload["groups"][0]["items"][0]["sources"] = [s.model_dump(mode="json") for s in actor.sources]
    saved = SavedSharedPack(
        operation_id="original-op",
        job_id="original-job",
        trace_id="1" * 32,
        grant_evidence_id=None,
        pack=ContextPack.model_validate(payload),
    )
    record = RecallRecord(
        recall_id=saved.pack.recall_id,
        scope=saved.pack.scope,
        state="completed",
        stage="finalize",
        revision=2,
        deadline_at="2026-10-09T00:10:00.000Z",
        result_available=True,
    )
    ref = {
        "owner": "recall",
        "object_type": "context_pack",
        "object_id": saved.pack.recall_id,
        "scope": saved.pack.scope.model_dump(mode="json"),
        "version": 1,
    }
    job = TaskOperationView.model_validate(
        {
            "task_id": saved.job_id,
            "owner_flow": "recall",
            "kind": "recall.execute",
            "subject": ref,
            "input_ref": ref,
            "result_ref": ref,
            "idempotency_key": "original-op",
            "input_hash": "b" * 64,
            "initiator_id": "U01",
            "initiator_auth_epoch": 1,
            "deadline_at": record.deadline_at,
            "state": "succeeded",
            "revision": 2,
            "attempt": 1,
            "effect_status": "confirmed",
            "temporal": {"workflow_id": None, "binding": None, "diagnostic": None},
        }
    )
    _, stage = maintained_case_and_stage()
    stage.update(
        operation_id=saved.operation_id,
        job_id=saved.job_id,
        recall_id=saved.pack.recall_id,
        observer_principal_id="operator",
        principal=case.old_principal,
        trusted_context=old_context(case),
        identity_revision=1,
        qualification=qualification(actor, case),
    )
    for field in ("qualified", "body_reads", "result_refs"):
        stage[field] = [actor.memory.model_dump(mode="json")]
    stage["candidates"][0]["target"] = stage["qualification"]["target"]
    for event in stage["events"]:
        event["subject"].update(object_id=actor.memory.memory_id)
        event["payload"].update(
            recall_id=saved.pack.recall_id, memory=actor.memory.model_dump(mode="json")
        )
        event["payload_hash"] = fingerprint(event["payload"])
    return OriginalLifecyclePack(
        saved=saved, record=record, job=job, stage=LifecycleStage.model_validate(stage)
    )


def denial_data(case, receipt, original):
    data = {
        field: getattr(receipt, field)
        for field in (
            "run_id",
            "control_operation_id",
            "observer_principal_id",
            "source_sha",
            "config_hash",
            "image_digest",
            "backend_binding",
            "model_binding",
        )
    }
    status, code = (403, "FORBIDDEN") if receipt.phase == "disabled" else (401, "UNAUTHENTICATED")
    data.update(
        evidence_id="phase-evidence",
        receipt_evidence_id=receipt.evidence_id,
        observed_at="2026-10-09T00:00:05.000Z",
        original_record=original.record,
        original_job=original.job,
        original_guard=original.stage.qualification.guard,
        original_pack_hash=pack_hash(original.saved),
        original_events=original.stage.events,
        denied_jobs=[],
        requests=[
            {
                "request_id": "denied-request",
                "method": "POST",
                "path": "/p3/recall",
                "operation_id": "denied-op",
                "credential_sha256": OLD_HASH,
                "checked_at": "2026-10-09T00:00:04.000Z",
                "status": status,
                "code": code,
                "body_reads": [],
                "model_input_refs": [],
                "result_refs": [],
                "search_invocations": 0,
                "events": [],
            }
        ],
    )
    return LifecyclePhaseObservation.model_validate(data).model_dump(mode="json")


@pytest.mark.parametrize("phase", ["disabled", "reenabled"])
@pytest.mark.parametrize(
    "fault",
    [
        None,
        "pack-replaced",
        "read-erased",
        "wrong-request",
        "wrong-token",
        "early-check",
        "body-read",
        "model-input",
        "result-present",
        "re-search",
        "successful-delivery",
        "stale-receipt",
    ],
)
def test_denial_requires_persisted_original_and_job_bound_content_event_evidence(
    tmp_path, phase, fault
):
    case = lifecycle_case(tmp_path)
    receipts = lifecycle_receipts(case)
    receipt = receipts[1 if phase == "disabled" else 2]
    original = original_pack(case)
    raw = denial_data(case, receipt, original)
    row = raw["requests"][0]
    if fault == "pack-replaced":
        raw["original_pack_hash"] = "f" * 64
    elif fault == "read-erased":
        raw["original_events"] = []
    elif fault == "wrong-request":
        row["request_id"] = "foreign-request"
    elif fault == "wrong-token":
        row["credential_sha256"] = NEW_HASH
    elif fault == "early-check":
        row["checked_at"] = "2026-10-09T00:00:00.000Z"
    elif fault in {"body-read", "model-input", "result-present"}:
        field = {
            "body-read": "body_reads",
            "model-input": "model_input_refs",
            "result-present": "result_refs",
        }[fault]
        row[field] = [case.actors[0].memory.model_dump(mode="json")]
    elif fault == "re-search":
        row["search_invocations"] = 1
    elif fault == "successful-delivery":
        row["events"] = [original.stage.events[1].model_dump(mode="json")]
    elif fault == "stale-receipt":
        raw["receipt_evidence_id"] = "unrelated-lifecycle"

    def verify():
        assert_phase_denial(
            LifecyclePhaseObservation.model_validate(raw),
            case,
            receipt,
            original,
            {"denied-request": ("POST", "/p3/recall", "denied-op")},
            OLD_HASH,
            set(),
            "operator",
        )

    if fault is None:
        verify()
    else:
        with pytest.raises((AssertionError, ValueError)):
            verify()


def test_a_denial_cannot_hide_a_persisted_successful_job(tmp_path):
    case = lifecycle_case(tmp_path)
    original = original_pack(case)
    receipt = lifecycle_receipts(case)[1]
    raw = denial_data(case, receipt, original)
    job = original.job.model_dump(mode="json")
    job.update(task_id="denied-job", idempotency_key="denied-op")
    raw["denied_jobs"] = [job]
    with pytest.raises(AssertionError):
        assert_phase_denial(
            LifecyclePhaseObservation.model_validate(raw),
            case,
            receipt,
            original,
            {"denied-request": ("POST", "/p3/recall", "denied-op")},
            OLD_HASH,
            {"denied-job"},
            "operator",
        )
