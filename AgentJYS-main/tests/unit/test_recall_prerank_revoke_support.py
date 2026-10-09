"""RC-AUTH-13 strict evidence faults; no claim of real model/backend acceptance."""

import json
from hashlib import sha256

import pytest

from recall_prerank_revoke_support import (
    IPC_PATH_FIELDS,
    B4Observation,
    GrantRevocation,
    ModelObservation,
    PrerankFinal,
    PrerankRevokeCase,
    assert_b4,
    assert_final,
    assert_model,
    assert_revoked,
    cleanup,
    publish,
)
from recall_shared_revoke_support import wait_receipt
from unit.test_recall_shared_read_support import sharing_case
from unit.test_recall_shared_revoke_support import original_and_final


def prerank_case(tmp_path):
    raw = sharing_case()
    raw["server_settings"].update(
        rerank_policy="required", reranker_model="actual-model", reranker_revision="pinned-revision"
    )
    for field in IPC_PATH_FIELDS:
        raw[field] = str(tmp_path / field)
    raw.update(
        initial_configuration_revision=1,
        b4_capability_id="B4-controller",
        model_probe_id="actual-model-probe",
        q06_mapping_id="prerank-mapping",
    )
    return PrerankRevokeCase.model_validate(raw)


def chain(tmp_path):
    case = prerank_case(tmp_path)
    active, revoked, saved, _, record, job, observed = original_and_final(case)
    binding = {
        key: getattr(observed, key)
        for key in (
            "run_id",
            "observer_principal_id",
            "source_sha",
            "image_digest",
            "config_hash",
            "backend_binding",
            "model_binding",
        )
    }
    binding.update(
        operation_id=saved.operation_id,
        job_id=saved.job_id,
        recall_id=saved.pack.recall_id,
        trace_id=saved.trace_id,
    )
    memory = case.memories["shared"]
    body = {
        "memory": memory.memory,
        "outcome": "read",
        "content": memory.text,
        "sources": memory.sources,
        "guard": active.qualification.guard,
        "path": "authority",
        "reason_code": "read",
        "location": {
            "kind": "body",
            "provider_id": "ceph",
            "provider_instance_id": "owned",
            "namespace": "owned",
            "object_key": "shared-body",
            "generation": memory.body_generation,
            "content_hash": memory.body_hash,
        },
    }
    reads = [e for e in observed.original_events if e.payload["stage"] == "read"]
    paused = B4Observation.model_validate(
        {
            **binding,
            "evidence_id": "B4-evidence",
            "observed_at": "2026-10-09T00:00:01.000Z",
            "b4_capability_id": case.b4_capability_id,
            "barrier_id": "held-B4",
            "held": True,
            "admitted_at": "2026-10-09T00:00:00.500Z",
            "position": "after_full_body_before_prerank_authorization",
            "context": {
                "principal": case.principals["U08"],
                "request_id": "original-request",
                "operation_id": saved.operation_id,
                "trace_id": saved.trace_id,
                "span_id": "2" * 16,
                "deadline_at": record.deadline_at,
            },
            "consumed_grants": [case.grant],
            "qualification": active.qualification,
            "bodies": [body],
            "events": reads,
            "model_invocations": 0,
            "model_probe_id": case.model_probe_id,
        }
    )
    raw = revoked.model_dump(mode="json")
    raw.pop("replicas")
    raw.pop("qualification_principal")
    raw.update(
        binding,
        b4_evidence_id=paused.evidence_id,
        barrier_id=paused.barrier_id,
        checked_principal=case.principals["U08"],
    )
    authority = GrantRevocation.model_validate(raw)
    model = ModelObservation.model_validate(
        {
            **binding,
            "evidence_id": "model-evidence",
            "model_probe_id": case.model_probe_id,
            "q06_mapping_id": case.q06_mapping_id,
            "boundary": "reranker.model.invoke",
            "model_id": case.server_settings.reranker_model,
            "model_revision": case.server_settings.reranker_revision,
            "window_opened_at": "2026-10-09T00:00:00.000Z",
            "terminal_at": "2026-10-09T00:00:04.000Z",
            "sealed_at": "2026-10-09T00:00:05.000Z",
            "observed_at": "2026-10-09T00:00:05.000Z",
            "complete": True,
            "task_terminal": True,
            "calls": [],
        }
    )
    final = PrerankFinal.model_validate(
        {
            **binding,
            "evidence_id": "final-evidence",
            "q06_mapping_id": case.q06_mapping_id,
            "observed_at": "2026-10-09T00:00:06.000Z",
            "barrier_id": paused.barrier_id,
            "revoke_evidence_id": authority.evidence_id,
            "released_at": authority.observed_at,
            "prerank_checked_at": "2026-10-09T00:00:03.500Z",
            "checked_principal": case.principals["U08"],
            "qualification": authority.qualification,
            "job": {
                **job.model_dump(mode="json"),
                "state": "failed",
                "result_ref": None,
                "error_code": "RESULT_INVALIDATED",
            },
            "record": {
                **record.model_dump(mode="json"),
                "state": "failed",
                "result_available": False,
            },
            "pack": None,
            "events": reads,
            "outbox_events": reads,
        }
    )
    return case, active, saved, paused, authority, model, final


def call_data(case, model, key="shared"):
    memory = case.memories[key]
    return {
        "call_id": "actual-call",
        "operation_id": model.operation_id,
        "job_id": model.job_id,
        "recall_id": model.recall_id,
        "trace_id": model.trace_id,
        "query_hash": sha256(case.query.encode()).hexdigest(),
        "started_at": "2026-10-09T00:00:03.600Z",
        "completed_at": "2026-10-09T00:00:03.700Z",
        "outcome": "succeeded",
        "documents": [
            {
                "memory_keys": [memory.memory.model_dump_json()],
                "body_hashes": [memory.body_hash],
                "document_hash": memory.body_hash,
            }
        ],
    }


def test_authorized_read_survives_but_revoked_model_input_and_pack_are_blocked(tmp_path):
    case, active, _, paused, revoked, model, final = chain(tmp_path)
    assert_b4(paused, case, active, "operator", paused.operation_id, paused.job_id)
    assert_revoked(revoked, paused, case, active, "operator")
    assert_final(final, model, paused, revoked, case, "operator")


def test_legitimate_shared_control_requires_actual_successful_model_call(tmp_path):
    case, _, saved, _, _, model, _ = chain(tmp_path)
    raw = model.model_dump(mode="json")
    raw["calls"] = [call_data(case, model)]
    assert_model(ModelObservation.model_validate(raw), case, "operator", saved, control=True)
    raw["calls"] = []
    with pytest.raises(AssertionError):
        assert_model(ModelObservation.model_validate(raw), case, "operator", saved, control=True)


@pytest.mark.parametrize("leak", [False, True])
def test_filtered_success_may_deliver_local_result_but_never_the_revoked_share(tmp_path, leak):
    case, _, saved, paused, revoked, model, final = chain(tmp_path)
    raw = final.model_dump(mode="json")
    memory = case.memories["shared" if leak else "u08-local"]
    pack = saved.pack.model_dump(mode="json")
    pack["rendered_context"] = memory.text
    pack["groups"][0]["items"][0].update(
        memory=memory.memory.model_dump(mode="json"),
        content=memory.text,
        sources=[s.model_dump(mode="json") for s in memory.sources],
    )
    raw["pack"] = pack
    raw["job"].update(state="succeeded", error_code=None, result_ref=raw["job"]["subject"])
    raw["record"].update(state="completed", result_available=True)
    if leak:
        with pytest.raises(AssertionError):
            assert_final(PrerankFinal.model_validate(raw), model, paused, revoked, case, "operator")
    else:
        assert_final(PrerankFinal.model_validate(raw), model, paused, revoked, case, "operator")


@pytest.mark.parametrize(
    "fault",
    [
        "not-held",
        "already-called",
        "missing-body",
        "wrong-job",
        "wrong-principal",
        "missing-read",
        "no-grant",
        "body-generation",
    ],
)
def test_b4_requires_actual_full_read_before_model_and_current_original_request(tmp_path, fault):
    case, active, _, paused, _, _, _ = chain(tmp_path)
    raw = paused.model_dump(mode="json")
    if fault == "not-held":
        raw["held"] = False
    elif fault == "already-called":
        raw["model_invocations"] = 1
    elif fault == "missing-body":
        raw["bodies"] = []
    elif fault == "wrong-job":
        raw["job_id"] = "another-job"
    elif fault == "wrong-principal":
        raw["context"]["principal"] = case.principals["U01"].model_dump(mode="json")
    elif fault == "missing-read":
        raw["events"] = []
    elif fault == "no-grant":
        raw["consumed_grants"] = []
    else:
        raw["bodies"][0]["location"]["generation"] = "wrong-generation"
    with pytest.raises((ValueError, AssertionError)):
        assert_b4(
            B4Observation.model_validate(raw),
            case,
            active,
            "operator",
            paused.operation_id,
            paused.job_id,
        )


@pytest.mark.parametrize(
    "fault",
    [
        "intent-only",
        "old-revision",
        "identity-changed",
        "wrong-barrier",
        "old-qualification",
        "expired-deadline",
    ],
)
def test_grant_only_revocation_requires_actual_authority_before_release(tmp_path, fault):
    case, active, _, paused, revoked, _, _ = chain(tmp_path)
    raw = revoked.model_dump(mode="json")
    if fault == "intent-only":
        raw["grants_for_u08"] = [case.grant.model_dump(mode="json")]
    elif fault == "old-revision":
        raw["configuration_revision"] = active.configuration_revision
    elif fault == "identity-changed":
        raw["principals"]["U08"]["auth_epoch"] += 1
    elif fault == "wrong-barrier":
        raw["barrier_id"] = "another-barrier"
    elif fault == "old-qualification":
        raw["qualification"] = active.qualification.model_dump(mode="json")
    else:
        raw["observed_at"] = paused.context.deadline_at
    with pytest.raises((ValueError, AssertionError)):
        assert_revoked(GrantRevocation.model_validate(raw), paused, case, active, "operator")


@pytest.mark.parametrize(
    "fault",
    [
        "revoked-ref",
        "revoked-hash",
        "revoked-string",
        "unsealed",
        "partial",
        "late-probe",
        "wrong-job",
        "wrong-model",
        "nonterminal",
    ],
)
def test_empty_result_cannot_hide_revoked_body_sent_to_actual_model(tmp_path, fault):
    case, _, _, paused, _, model, _ = chain(tmp_path)
    raw = model.model_dump(mode="json")
    if fault in {"revoked-ref", "revoked-hash", "revoked-string"}:
        raw["calls"] = [call_data(case, model, "u08-local")]
        document = raw["calls"][0]["documents"][0]
        if fault == "revoked-ref":
            document["memory_keys"] = [case.memories["shared"].memory.model_dump_json()]
        elif fault == "revoked-hash":
            document["body_hashes"] = [case.memories["shared"].body_hash]
        else:
            document["document_hash"] = case.memories["shared"].body_hash
    elif fault == "unsealed":
        raw["sealed_at"] = raw["window_opened_at"]
    elif fault == "partial":
        raw["complete"] = False
    elif fault == "late-probe":
        raw["window_opened_at"] = paused.observed_at
    elif fault == "wrong-job":
        raw["job_id"] = "another-job"
    elif fault == "wrong-model":
        raw["model_revision"] = "another-model"
    else:
        raw["task_terminal"] = False
    with pytest.raises((ValueError, AssertionError)):
        assert_model(ModelObservation.model_validate(raw), case, "operator", paused, control=False)


@pytest.mark.parametrize(
    "fault",
    [
        "early-release",
        "read-erased",
        "outbox-packed",
        "wrong-request",
        "timeout",
        "current-allowed",
        "pack-committed",
    ],
)
def test_original_terminal_result_and_full_events_must_match_prerank_revocation(tmp_path, fault):
    case, active, saved, paused, revoked, model, final = chain(tmp_path)
    raw = final.model_dump(mode="json")
    if fault == "early-release":
        raw["released_at"] = paused.observed_at
    elif fault == "read-erased":
        raw["events"] = []
    elif fault == "outbox-packed":
        raw["outbox_events"][0]["payload"]["stage"] = "packed"
    elif fault == "wrong-request":
        raw["job"]["idempotency_key"] = "another-operation"
    elif fault == "timeout":
        raw["job"]["error_code"] = "DEADLINE_EXCEEDED"
    elif fault == "current-allowed":
        raw["qualification"] = active.qualification.model_dump(mode="json")
    else:
        raw["pack"] = saved.pack.model_dump(mode="json")
    with pytest.raises((ValueError, AssertionError)):
        assert_final(PrerankFinal.model_validate(raw), model, paused, revoked, case, "operator")


def test_bounded_missing_model_probe_is_blocked_and_ipc_never_overwrites(tmp_path):
    case = prerank_case(tmp_path)
    publish(case, "arm", operation_id="original-op")
    with pytest.raises(FileExistsError):
        publish(case, "arm", operation_id="replacement-op")
    assert (
        json.loads((case.barrier_directory / "arm.json").read_text())["operation_id"]
        == "original-op"
    )
    with pytest.raises(RuntimeError, match="blocked_fixture"):
        wait_receipt(case.model_export, "job_id", "original-job", ModelObservation, timeout=0)


@pytest.mark.parametrize(
    "fault", [None, "held", "not-drained", "grant-remains", "identity-changed"]
)
def test_cleanup_must_release_drain_and_remove_only_owned_grant(tmp_path, fault):
    case = prerank_case(tmp_path)
    raw = {
        "run_id": case.run_id,
        "control_operation_id": "cleanup-" + case.run_id,
        "evidence_id": "cleanup-evidence",
        "released": True,
        "request_drained": True,
        "configuration_revision": 4,
        "principals": {k: p.model_dump(mode="json") for k, p in case.principals.items()},
        "grants_for_u08": [],
        "grants_for_u09": [],
    }
    if fault == "held":
        raw["released"] = False
    elif fault == "not-drained":
        raw["request_drained"] = False
    elif fault == "grant-remains":
        raw["grants_for_u08"] = [case.grant.model_dump(mode="json")]
    elif fault == "identity-changed":
        raw["principals"]["U08"]["auth_epoch"] += 1
    case.barrier_receipts.write_text(json.dumps([raw]))
    if fault:
        with pytest.raises(AssertionError):
            cleanup(case, revision=3)
    else:
        assert cleanup(case, revision=3).released
