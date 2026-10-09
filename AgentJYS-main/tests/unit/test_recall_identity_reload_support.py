"""Strict RC-AUTH-12 evidence mutations; counted separately from live acceptance."""

import json
from copy import deepcopy
from hashlib import sha256

import pytest

from aether_agent_memory.runtime.foundation.common import fingerprint
from recall_authorization_support import F1_TEXTS
from recall_identity_reload_support import (
    AssemblyObservation,
    FinalObservation,
    IdentityReloadCase,
    ReadRevocationReceipt,
    assert_assembly,
    assert_final,
    assert_revocation,
    cleanup,
    publish,
)
from recall_shared_revoke_support import wait_receipt
from unit.test_recall_shared_read_support import qualification
from unit.test_recall_tenant_lifecycle_support import (
    lifecycle_case,
    old_context,
    original_pack,
    receipt_data,
)


def evidence_chain(tmp_path):
    lifecycle = lifecycle_case(tmp_path)
    raw = lifecycle.model_dump(mode="json")
    for key in (
        "old_principal",
        "new_principal",
        "control_principal",
        "new_credential_env",
        "phase_export",
        "success_directory",
    ):
        raw.pop(key)
    raw.update(
        principal=lifecycle.old_principal.model_dump(mode="json"),
        initial_identity_hash="1" * 64,
        unchanged_identity_state_hash="a" * 64,
        b5_capability_id="B5-controller",
        q06_capability_id="Q06-view",
        final_export=str(tmp_path / "final"),
    )
    for index, actor in enumerate(raw["actors"]):
        actor.update(
            name="U01",
            credential_env="U01_TOKEN",
            text=F1_TEXTS[index],
            body_hash=sha256(F1_TEXTS[index].encode()).hexdigest(),
        )
        actor["memory"]["scope"] = raw["principal"]["home_scope"]
    case = IdentityReloadCase.model_validate(raw)
    original = original_pack(lifecycle)
    pack = original.saved.pack.model_dump(mode="json")
    item = pack["groups"][0]["items"][0]
    pack["groups"] = [
        {
            "group_id": f"g-{index}",
            "items": [
                {
                    **item,
                    "memory": a.memory.model_dump(mode="json"),
                    "content": a.text,
                    "sources": [s.model_dump(mode="json") for s in a.sources],
                }
            ],
        }
        for index, a in enumerate(case.actors)
    ]
    pack["rendered_context"] = "\n".join(F1_TEXTS)
    units = []
    for index, actor in enumerate(case.actors):
        guard = qualification(actor, case)["guard"]
        units.append(
            {
                "group_id": f"g-{index}",
                "rank": index + 1,
                "primary_memories": [actor.memory.model_dump(mode="json")],
                "bodies": [
                    {
                        "memory": actor.memory.model_dump(mode="json"),
                        "outcome": "read",
                        "content": actor.text,
                        "sources": actor.sources,
                        "guard": guard,
                        "path": "authority",
                        "reason_code": "read",
                        "location": {
                            "kind": "body",
                            "provider_id": "ceph",
                            "provider_instance_id": "owned",
                            "namespace": "owned",
                            "object_key": f"body-{index}",
                            "generation": actor.body_generation,
                            "content_hash": actor.body_hash,
                        },
                    }
                ],
            }
        )
    plan = {
        "request": {
            "recall_id": pack["recall_id"],
            "query": case.request.query,
            "selection": case.request.selection,
            "sources": ["working"],
            "token_budget": case.request.token_budget,
            "context_tokenizer": "o200k_base",
            "policy_version": "test",
            "deadline_at": old_context(lifecycle).deadline_at,
            "working_search": {
                "operation_id": "original-op",
                "purpose": "recall",
                "query": case.request.query,
                "selection": case.request.selection,
                "memory_source": "working",
                "model_space": case.model_space,
                "memory_top_k": 2,
                "chunk_page_size": 2,
                "max_chunk_hits": 2,
                "max_rounds": 1,
                "deadline_at": old_context(lifecycle).deadline_at,
            },
        },
        "scope": case.principal.home_scope,
        "units": units,
        "skipped_group_ids": [],
        "rendered_context": pack["rendered_context"],
        "tokens_used": 8,
        "rank_evidence": [],
        "degradation_reasons": [],
    }
    events = []
    for index, actor in enumerate(case.actors):
        event = deepcopy(original.stage.events[0].model_dump(mode="json"))
        event.update(event_id=f"read-{index}")
        event["subject"].update(
            object_id=actor.memory.memory_id, scope=actor.memory.scope.model_dump(mode="json")
        )
        event["payload"].update(stage="read", memory=actor.memory.model_dump(mode="json"))
        event["payload_hash"] = fingerprint(event["payload"])
        events.append(event)
    binding = {
        key: getattr(case, key)
        for key in ("run_id", "source_sha", "image_digest", "backend_binding", "model_binding")
    }
    binding.update(
        config_hash=case.configuration.config_hash,
        observer_principal_id="operator",
        operation_id="original-op",
        job_id="original-job",
        recall_id=pack["recall_id"],
        trace_id="1" * 32,
        observed_at="2026-10-09T00:00:01.000Z",
    )
    paused = AssemblyObservation.model_validate(
        {
            **binding,
            "evidence_id": "b5-evidence",
            "phase": "B5",
            "barrier_id": "barrier",
            "b5_capability_id": case.b5_capability_id,
            "held": True,
            "position": "after_assemble_before_final_guard",
            "context": old_context(lifecycle),
            "identity_revision": 1,
            "pack": None,
            "plan": plan,
            "body_reads": [a.memory.memory_id for a in case.actors],
            "events": events,
            "final_guard_invocations": 0,
            "successful_commits": 0,
        }
    )
    authority = receipt_data(lifecycle, "reenabled")
    authority["current_identity"]["principal"] = case.revoked_principal().model_dump(mode="json")
    authority["current_identity"]["credential_sha256"] = sha256(b"original-token").hexdigest()
    for reload in authority["reloads"]:
        reload["current_identity"] = authority["current_identity"]
    receipt = ReadRevocationReceipt.model_validate(
        {
            **binding,
            "evidence_id": "revoke-evidence",
            "control_operation_id": "revoke-" + case.run_id,
            "barrier_id": "barrier",
            "b5_evidence_id": paused.evidence_id,
            "q18_capability_id": case.q18_capability_id,
            "entrypoint": "Service.reload_identity",
            "submitted_at": authority["submitted_at"],
            "committed_at": authority["committed_at"],
            "effective_at": authority["effective_at"],
            "observed_at": authority["observed_at"],
            "identity_revision": 3,
            "identity_document_hash": "3" * 64,
            "current_identity": authority["current_identity"],
            "reloads": authority["reloads"],
            "unchanged_identity_state_hash": case.unchanged_identity_state_hash,
        }
    )
    job = original.job.model_dump(mode="json")
    job.update(state="failed", result_ref=None, error_code="FORBIDDEN")
    record = original.record.model_dump(mode="json")
    record.update(state="failed", result_available=False)
    final = FinalObservation.model_validate(
        {
            **binding,
            "observed_at": "2026-10-09T00:00:05.000Z",
            "evidence_id": "final-evidence",
            "q06_capability_id": case.q06_capability_id,
            "barrier_id": "barrier",
            "revoke_evidence_id": receipt.evidence_id,
            "released_at": receipt.observed_at,
            "final_checked_at": "2026-10-09T00:00:04.000Z",
            "current_principal": case.revoked_principal(),
            "identity_revision": 3,
            "final_check": "RF",
            "final_error": "FORBIDDEN",
            "conditional_commit_attempts": 1,
            "successful_commits": 0,
            "persisted_pack": None,
            "result_refs": [],
            "record": record,
            "job": job,
            "events": events,
            "outbox_events": events,
        }
    )
    return case, paused, receipt, final


def test_witnessed_b5_reload_then_original_forbidden_preserves_real_reads(tmp_path):
    case, paused, receipt, final = evidence_chain(tmp_path)
    assert_assembly(paused, case, "operator", "original-op", "original-job", paused=True)
    assert_revocation(receipt, paused, case, "operator", "original-token")
    assert_final(final, paused, receipt, case, "operator")


@pytest.mark.parametrize(
    "fault",
    [
        "not-held",
        "already-guarded",
        "already-committed",
        "wrong-position",
        "empty-pack",
        "wrong-job",
        "missing-read",
    ],
)
def test_admission_or_earlier_stage_is_not_a_b5_witness(tmp_path, fault):
    case, paused, _, _ = evidence_chain(tmp_path)
    raw = paused.model_dump(mode="json")
    if fault == "not-held":
        raw["held"] = False
    elif fault == "already-guarded":
        raw["final_guard_invocations"] = 1
    elif fault == "already-committed":
        raw["successful_commits"] = 1
    elif fault == "wrong-position":
        raw["position"] = "completed"
    elif fault == "empty-pack":
        raw["plan"]["units"] = []
    elif fault == "wrong-job":
        raw["job_id"] = "another-job"
    else:
        raw["events"] = []
    with pytest.raises((AssertionError, ValueError)):
        assert_assembly(
            AssemblyObservation.model_validate(raw),
            case,
            "operator",
            "original-op",
            "original-job",
            paused=True,
        )


@pytest.mark.parametrize(
    "fault",
    [
        "old-revision",
        "old-document",
        "old-worker",
        "missing-worker",
        "read-remains",
        "credential-removed",
        "wrong-barrier",
        "other-config-changed",
        "expired-context",
    ],
)
def test_authoritative_reload_is_required_before_release(tmp_path, fault):
    case, paused, receipt, _ = evidence_chain(tmp_path)
    raw = receipt.model_dump(mode="json")
    if fault == "old-revision":
        raw["identity_revision"] = 1
    elif fault == "old-document":
        raw["identity_document_hash"] = case.initial_identity_hash
    elif fault == "old-worker":
        raw["reloads"][0]["identity_revision"] = 1
    elif fault == "missing-worker":
        raw["reloads"].pop()
    elif fault == "read-remains":
        raw["current_identity"]["principal"] = case.principal.model_dump(mode="json")
    elif fault == "credential-removed":
        raw["current_identity"]["credential_sha256"] = None
    elif fault == "wrong-barrier":
        raw["barrier_id"] = "another-barrier"
    elif fault == "other-config-changed":
        raw["unchanged_identity_state_hash"] = "b" * 64
    else:
        raw["observed_at"] = "2026-10-09T00:11:00.000Z"
    with pytest.raises((AssertionError, ValueError)):
        assert_revocation(
            ReadRevocationReceipt.model_validate(raw), paused, case, "operator", "original-token"
        )


@pytest.mark.parametrize(
    "fault",
    [
        "old-principal",
        "early-release",
        "successful-commit",
        "saved-pack",
        "delivered-ref",
        "wrong-operation",
        "read-erased",
        "outbox-packed",
        "foreign-observer",
    ],
)
def test_denial_requires_original_terminal_commit_result_and_outbox_evidence(tmp_path, fault):
    case, paused, receipt, final = evidence_chain(tmp_path)
    raw = final.model_dump(mode="json")
    if fault == "old-principal":
        raw["current_principal"] = case.principal.model_dump(mode="json")
    elif fault == "early-release":
        raw["released_at"] = paused.observed_at
    elif fault == "successful-commit":
        raw["successful_commits"] = 1
    elif fault == "saved-pack":
        raw["persisted_pack"] = original_pack(lifecycle_case(tmp_path)).saved.pack.model_dump(
            mode="json"
        )
    elif fault == "delivered-ref":
        raw["result_refs"] = [case.actors[0].memory.memory_id]
    elif fault == "wrong-operation":
        raw["job"]["idempotency_key"] = "another-op"
    elif fault == "read-erased":
        raw["events"].pop()
    elif fault == "outbox-packed":
        raw["outbox_events"][0]["payload"]["stage"] = "packed"
        raw["outbox_events"][0]["payload_hash"] = fingerprint(raw["outbox_events"][0]["payload"])
    else:
        raw["observer_principal_id"] = "foreign-observer"
    with pytest.raises((AssertionError, ValueError)):
        assert_final(FinalObservation.model_validate(raw), paused, receipt, case, "operator")


def test_control_commands_refuse_overwrite_and_cleanup_has_authoritative_ack(tmp_path):
    case, _, _, _ = evidence_chain(tmp_path)
    publish(case, "arm", operation_id="original-op")
    with pytest.raises(FileExistsError):
        publish(case, "arm", operation_id="replacement-op")
    assert (
        json.loads((case.control_directory / "arm.json").read_text())["operation_id"]
        == "original-op"
    )
    restored = case.principal.model_dump(mode="json")
    restored["auth_epoch"] = 3
    identity = {"principal": restored, "credential_sha256": sha256(b"original-token").hexdigest()}
    reloads = [
        {
            "instance_id": instance,
            "identity_revision": 4,
            "identity_document_hash": "4" * 64,
            "current_identity": identity,
            "tenant": {"tenant_id": case.principal.home_scope.tenant_id, "enabled": True},
            "applied_at": "2026-10-09T00:00:06.000Z",
        }
        for instance in case.identity_instances
    ]
    case.receipt_export.write_text(
        json.dumps(
            [
                {
                    "run_id": case.run_id,
                    "control_operation_id": "cleanup-" + case.run_id,
                    "evidence_id": "cleanup-evidence",
                    "released": True,
                    "request_drained": True,
                    "identity_restored": True,
                    "identity_revision": 4,
                    "identity_document_hash": "4" * 64,
                    "restored_identity": identity,
                    "reloads": reloads,
                }
            ]
        )
    )
    assert cleanup(case, previous_revision=3).identity_restored


@pytest.mark.parametrize("fault", ["stale-revision", "stale-epoch", "missing-worker", "no-read"])
def test_cleanup_boolean_cannot_hide_stale_or_incomplete_restoration(tmp_path, fault):
    test_control_commands_refuse_overwrite_and_cleanup_has_authoritative_ack(tmp_path)
    case, _, _, _ = evidence_chain(tmp_path)
    rows = json.loads(case.receipt_export.read_text())
    if fault == "stale-revision":
        rows[0]["identity_revision"] = 3
    elif fault == "stale-epoch":
        rows[0]["restored_identity"]["principal"]["auth_epoch"] = 1
    elif fault == "missing-worker":
        rows[0]["reloads"].pop()
    else:
        rows[0]["restored_identity"]["principal"]["permissions"] = []
    case.receipt_export.write_text(json.dumps(rows))
    (case.control_directory / "cleanup.json").unlink()
    with pytest.raises((AssertionError, ValueError)):
        cleanup(case, previous_revision=3)


def test_missing_b5_receipt_is_bounded_and_blocked(tmp_path):
    with pytest.raises(RuntimeError, match="blocked_fixture"):
        wait_receipt(tmp_path / "missing", "job_id", "original-job", AssemblyObservation, timeout=0)
