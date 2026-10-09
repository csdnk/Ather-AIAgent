"""Strict RC-AUTH-10 evidence checks, never live security acceptance."""

import json
from copy import deepcopy

import httpx
import pytest

from aether_agent_memory.recall.contracts.models import ContextPack, RecallRecord
from aether_agent_memory.runtime.contracts.models import TaskOperationView
from aether_agent_memory.runtime.foundation.common import fingerprint
from recall_shared_read_support import GrantReceipt, SavedSharedPack
from recall_shared_revoke_support import (
    RevokeCase,
    RevokeFinalObservation,
    RevokeReceipt,
    assert_invalidated,
    assert_original_preserved,
    assert_revoked,
    pack_hash,
    revoke_shared_grant,
)
from unit.test_recall_shared_read_support import grant_receipt, qualification, sharing_case
from unit.test_recall_tenant_isolation_support import maintained_case_and_stage, pack


def revoke_case(tmp_path):
    data = sharing_case()
    for name in (
        "control_request",
        "control_receipts",
        "maintenance_export",
        "success_directory",
        "revoke_request",
        "revoke_export",
        "final_export",
    ):
        data[name] = str(tmp_path / name)
    return RevokeCase.model_validate(data)


def revocation(case):
    active = grant_receipt(case)
    receipt = {
        key: value
        for key, value in active.items()
        if key
        not in {
            "no_grant_qualification",
            "unshared_qualification",
            "permission_checks",
            "qualification_principals",
        }
    }
    receipt.update(
        control_operation_id="revoke-" + case.run_id,
        evidence_id="revoked-authority",
        configuration_revision=3,
        submitted_at="2026-10-09T00:00:01.000Z",
        committed_at="2026-10-09T00:00:02.000Z",
        effective_at="2026-10-09T00:00:02.000Z",
        observed_at="2026-10-09T00:00:03.000Z",
        qualification_checked_at="2026-10-09T00:00:03.000Z",
        revoked_grant=case.grant.model_dump(mode="json"),
        grants_for_u08=[],
        principals={k: v.model_dump(mode="json") for k, v in case.principals.items()},
        qualification_principal=case.principals["U08"].model_dump(mode="json"),
        qualification=qualification(case.memories["shared"], case, "excluded"),
    )
    memory = case.memories["shared"]
    receipt["replicas"] = {
        "observed_at": receipt["observed_at"],
        "vector_present": True,
        "vector": active["qualification"]["target"],
        "redis_present": True,
        "redis_memory": memory.memory.model_dump(mode="json"),
        "redis_generation": memory.body_generation,
        "redis_content": memory.text,
        "redis_body_hash": memory.body_hash,
        "redis_provider": "redis",
        "redis_instance": "owned-redis",
        "vector_provider": "milvus",
        "vector_instance": "owned-milvus",
    }
    return receipt


def test_authority_barrier_requires_removed_grant_with_real_replicas_retained(tmp_path):
    case = revoke_case(tmp_path)
    assert_revoked(
        RevokeReceipt.model_validate(revocation(case)),
        case,
        GrantReceipt.model_validate(grant_receipt(case)),
        "operator",
    )


@pytest.mark.parametrize(
    "fault",
    [
        "intent-only",
        "stale-revision",
        "old-time",
        "principal-revoked",
        "foreign-observer",
        "foreign-operation",
        "wrong-principal",
        "allowed",
        "vector-cleaned",
        "redis-cleaned",
        "stale-vector",
        "stale-cache",
        "changed-body",
        "different-grant",
    ],
)
def test_revocation_evidence_cannot_pass_by_cleanup_or_unrelated_authority_change(tmp_path, fault):
    case = revoke_case(tmp_path)
    raw = deepcopy(revocation(case))
    if fault == "intent-only":
        raw["grants_for_u08"] = [case.grant.model_dump(mode="json")]
    elif fault == "stale-revision":
        raw["configuration_revision"] = 2
    elif fault == "old-time":
        raw["qualification_checked_at"] = "2026-10-09T00:00:00.000Z"
    elif fault == "principal-revoked":
        raw["principals"]["U08"]["auth_epoch"] = 2
    elif fault == "foreign-observer":
        raw["observer_principal_id"] = "foreign"
    elif fault == "foreign-operation":
        raw["control_operation_id"] = "other-run"
    elif fault == "wrong-principal":
        raw["qualification_principal"] = case.principals["U01"].model_dump(mode="json")
    elif fault == "allowed":
        raw["qualification"] = qualification(case.memories["shared"], case)
    elif fault == "vector-cleaned":
        raw["replicas"]["vector_present"] = False
    elif fault == "redis-cleaned":
        raw["replicas"]["redis_present"] = False
    elif fault == "stale-vector":
        raw["replicas"]["vector"]["generation"] = "other-generation"
    elif fault == "stale-cache":
        raw["replicas"]["redis_memory"]["version"] = 2
    elif fault == "changed-body":
        raw["replicas"]["redis_content"] = "different body"
    else:
        raw["revoked_grant"]["grant_id"] = "other-grant"
    with pytest.raises((ValueError, AssertionError)):
        assert_revoked(
            RevokeReceipt.model_validate(raw),
            case,
            GrantReceipt.model_validate(grant_receipt(case)),
            "operator",
        )


def test_revocation_request_is_single_target_and_waits_for_actual_authority(tmp_path):
    case = revoke_case(tmp_path)
    case.revoke_export.write_text(json.dumps([revocation(case)]))
    receipt = revoke_shared_grant(
        case, GrantReceipt.model_validate(grant_receipt(case)), "operator", timeout=0
    )
    request = json.loads(case.revoke_request.read_text())
    assert request["remove_grant"]["grant_id"] == "one-read-grant"
    assert request["retain_vector_and_redis"] is True
    assert request["preserve_principals"] is True
    assert receipt.evidence_id == "revoked-authority"


def test_missing_authority_receipt_is_blocked_not_success(tmp_path):
    case = revoke_case(tmp_path)
    with pytest.raises(RuntimeError, match="blocked_fixture.*receipt"):
        revoke_shared_grant(
            case, GrantReceipt.model_validate(grant_receipt(case)), "operator", timeout=0
        )
    assert case.revoke_request.is_file() and not case.revoke_export.exists()


@pytest.mark.parametrize(
    "status,body",
    [
        (200, {"groups": []}),
        (304, {}),
        (404, {"code": "NOT_FOUND"}),
        (410, {"code": "RESULT_INVALIDATED", "content": "leak"}),
        (403, {"code": "FORBIDDEN", "nested": {"result": {}}}),
    ],
)
def test_old_result_cannot_be_replayed_cropped_or_disguised_as_unrelated_error(status, body):
    with pytest.raises(AssertionError):
        assert_invalidated(httpx.Response(status, json=body))


@pytest.mark.parametrize("status,code", [(403, "FORBIDDEN"), (410, "RESULT_INVALIDATED")])
def test_existing_permission_or_invalidation_errors_are_body_free(status, code):
    assert_invalidated(httpx.Response(status, json={"code": code, "message": "denied"}))


def original_and_final(case):
    active = GrantReceipt.model_validate(grant_receipt(case))
    receipt = RevokeReceipt.model_validate(revocation(case))
    memory = case.memories["shared"]
    value = pack(content=memory.text, refs=[memory.memory.model_dump(mode="json")])
    value["scope"] = case.principals["U08"].home_scope.model_dump(mode="json")
    value["groups"][0]["items"][0]["sources"] = [s.model_dump(mode="json") for s in memory.sources]
    saved = SavedSharedPack(
        operation_id="original-op",
        job_id="original-job",
        trace_id="1" * 32,
        grant_evidence_id=active.evidence_id,
        pack=ContextPack.model_validate(value),
    )
    new_saved = saved.model_copy(
        update={
            "operation_id": "new-op",
            "job_id": "new-job",
            "trace_id": "2" * 32,
            "pack": saved.pack.model_copy(update={"recall_id": "new-recall"}),
        }
    )
    record = RecallRecord(
        recall_id=saved.pack.recall_id,
        scope=saved.pack.scope,
        state="completed",
        stage="finalize",
        revision=2,
        deadline_at="2026-10-09T00:01:00.000Z",
        result_available=True,
    )
    ref = {
        "owner": "recall",
        "object_type": "context_pack",
        "object_id": saved.pack.recall_id,
        "scope": value["scope"],
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
            "initiator_id": "U08",
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
    events = deepcopy(stage["events"])
    for event in events:
        event["subject"].update(
            object_id=memory.memory.memory_id, scope=memory.memory.scope.model_dump(mode="json")
        )
        event["initiator_id"] = "U08"
        event["payload"].update(
            recall_id=saved.pack.recall_id, memory=memory.memory.model_dump(mode="json")
        )
        event["payload_hash"] = fingerprint(event["payload"])
    raw = {
        key: getattr(receipt, key)
        for key in (
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
    raw.update(
        evidence_id="final-evidence",
        observed_at="2026-10-09T00:00:05.000Z",
        original_record=record,
        original_job=job,
        original_guard=active.qualification.guard,
        original_pack_hash=pack_hash(saved),
        original_events=events,
        new_operation_id=new_saved.operation_id,
        new_job_id=new_saved.job_id,
        new_recall_id=new_saved.pack.recall_id,
        new_trace_id=new_saved.trace_id,
        new_qualification=receipt.qualification,
        new_qualification_principal=case.principals["U08"],
        new_qualification_checked_at="2026-10-09T00:00:04.000Z",
        new_events=[],
        historical_reads=[
            {
                "request_id": "old-read",
                "path": "/p3/recalls/recall-a/result",
                "checked_at": "2026-10-09T00:00:04.000Z",
                "principal": case.principals["U08"],
                "qualification": receipt.qualification,
                "search_invocations": 0,
                "original_pack_hash_before": pack_hash(saved),
                "original_pack_hash_after": pack_hash(saved),
                "events": [],
            }
        ],
        replicas=receipt.replicas.model_copy(update={"observed_at": "2026-10-09T00:00:05.000Z"}),
    )
    final = RevokeFinalObservation.model_validate(raw)
    return active, receipt, saved, new_saved, record, job, final


@pytest.mark.parametrize(
    "fault",
    [
        None,
        "rewrite-read",
        "replace-pack",
        "re-search",
        "cropped-pack",
        "foreign-get",
        "old-check",
        "owner-check",
        "foreign-new-job",
        "cleanup-after",
        "false-delivery",
        "changed-guard",
    ],
)
def test_original_pack_and_historical_facts_survive_while_current_reads_are_denied(tmp_path, fault):
    case = revoke_case(tmp_path)
    active, receipt, saved, new_saved, record, job, original = original_and_final(case)
    raw = original.model_dump(mode="json")
    if fault == "rewrite-read":
        raw["original_events"] = []
    elif fault == "replace-pack":
        raw["original_pack_hash"] = "f" * 64
    elif fault == "re-search":
        raw["historical_reads"][0]["search_invocations"] = 1
    elif fault == "cropped-pack":
        raw["historical_reads"][0]["original_pack_hash_after"] = "f" * 64
    elif fault == "foreign-get":
        raw["historical_reads"][0]["request_id"] = "another-get"
    elif fault == "old-check":
        raw["historical_reads"][0]["checked_at"] = "2026-10-09T00:00:00.000Z"
    elif fault == "owner-check":
        raw["historical_reads"][0]["principal"] = case.principals["U01"].model_dump(mode="json")
    elif fault == "foreign-new-job":
        raw["new_job_id"] = "other-job"
    elif fault == "cleanup-after":
        raw["replicas"]["redis_present"] = False
    elif fault == "false-delivery":
        event = deepcopy(raw["original_events"][1])
        event.update(trace_id=new_saved.trace_id, event_id="false-packed")
        event["payload"]["recall_id"] = new_saved.pack.recall_id
        event["payload_hash"] = fingerprint(event["payload"])
        raw["new_events"] = [event]
    elif fault == "changed-guard":
        raw["original_guard"]["relations_revision"] = 2

    def verify():
        assert_original_preserved(
            RevokeFinalObservation.model_validate(raw),
            case,
            receipt,
            active,
            saved,
            original.original_events,
            job,
            record,
            original.original_guard,
            new_saved,
            {"old-read": "/p3/recalls/recall-a/result"},
            "operator",
        )

    if fault is None:
        verify()
    else:
        with pytest.raises((AssertionError, ValueError)):
            verify()
