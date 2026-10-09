"""Strict peer assertions for RC-AUTH-03; these are not live authorization tests."""

from copy import deepcopy
from hashlib import sha256

import pytest

from aether_agent_memory.runtime.foundation.common import fingerprint
from recall_tenant_isolation_support import (
    F4Case,
    F4StageObservation,
    assert_isolated_pack,
    assert_public_isolation,
    assert_stage_isolation,
    assert_top1_pressure,
)


def ref(tenant):
    return {
        "scope": {
            "tenant_id": tenant,
            "application_id": "App",
            "user_id": "same-user",
            "agent_id": "same-agent",
            "session_id": "same-session",
            "task_id": None,
        },
        "memory_id": "same-memory",
        "version": 1,
    }


def pack(tenant="T-A", content="A_PRIVATE", refs=None):
    memory = ref(tenant)
    return {
        "recall_id": "recall-a",
        "scope": memory["scope"],
        "outcome": "available",
        "selected_sources": ["working"],
        "coverage": {
            "working": "complete",
            "long_term": "not_requested",
        },
        "rendered_context": content,
        "token_budget": 1000,
        "tokens_used": 8,
        "policy_version": "test",
        "committed_at": "2026-10-09T00:00:00.000Z",
        "tokenizer_id": "o200k_base",
        "degradation_reasons": [],
        "groups": [
            {
                "group_id": "g",
                "items": [
                    {
                        "memory": item,
                        "content": content,
                        "representation": "original",
                        "sources": [
                            {
                                "source_id": "source",
                                "source_version": 1,
                                "content_hash": "a" * 64,
                                "locator": "fixture",
                            }
                        ],
                    }
                    for item in (refs or [memory])
                ],
            }
        ],
    }


def test_same_literal_memory_id_does_not_authorize_the_other_tenant():
    with pytest.raises(AssertionError, match="Ref|scope"):
        assert_isolated_pack(pack("T-B", "B_PRIVATE"), ref("T-A"), "A_PRIVATE", ("B_PRIVATE",))


def test_legal_nonempty_control_requires_exact_body_and_ref():
    assert_isolated_pack(pack(), ref("T-A"), "A_PRIVATE", ("B_PRIVATE",))


@pytest.mark.parametrize("change", ["empty", "foreign-text", "duplicate", "debug-count"])
def test_safe_refs_alone_do_not_establish_isolation(change):
    value = pack()
    if change == "empty":
        value.update(outcome="empty", rendered_context="", tokens_used=0, groups=[])
    elif change == "foreign-text":
        value["rendered_context"] += " B_PRIVATE"
    elif change == "duplicate":
        value["groups"][0]["items"].append(value["groups"][0]["items"][0])
    else:
        value["foreign_candidate_count"] = 4
    with pytest.raises((AssertionError, ValueError)):
        assert_isolated_pack(value, ref("T-A"), "A_PRIVATE", ("B_PRIVATE",))


def hit(tenant, score, rank):
    return {
        "target": {
            "memory": ref(tenant),
            "generation": "g1",
            "body_hash": "a" * 64,
            "model_space": "native-space",
            "chunk_index": 0,
            "vector_id": str(rank) * 64,
            "input_hash": "b" * 64,
            "memory_source": "working",
        },
        "score": score,
        "rank": rank,
    }


def test_high_scoring_foreign_top1_is_a_real_precondition():
    assert_top1_pressure([hit("T-B", 0.95, 1), hit("T-A", 0.6, 2)], ref("T-A"), ref("T-B"))


@pytest.mark.parametrize("fault", ["tie", "local-first", "missing-local", "wrong-version"])
def test_favorable_or_unbound_index_probes_cannot_prove_adverse_top1(fault):
    hits = [hit("T-B", 0.95, 1), hit("T-A", 0.6, 2)]
    if fault == "tie":
        hits[1]["score"] = 0.95
    elif fault == "local-first":
        hits.reverse()
    elif fault == "missing-local":
        hits.pop()
    else:
        hits[0]["target"]["memory"]["version"] = 2
    with pytest.raises(AssertionError):
        assert_top1_pressure(hits, ref("T-A"), ref("T-B"))


@pytest.mark.parametrize(
    "payload",
    [
        {"state": "accepted", "debug": {"body": "B_PRIVATE"}},
        {"temporal": {"diagnostic": {"scope": "T-B"}}},
        {"code": "FORBIDDEN", "restricted_count": 0},
    ],
)
def test_every_ordinary_response_including_admission_and_job_metadata_is_checked(payload):
    with pytest.raises(AssertionError, match="leak"):
        assert_public_isolation(payload, ("B_PRIVATE", "T-B"))


def maintained_case_and_stage():
    actors = []
    for name, tenant, text in (("U01", "T-A", "A_PRIVATE"), ("U04", "T-B", "B_PRIVATE")):
        digest = sha256(text.encode()).hexdigest()
        actors.append(
            {
                "name": name,
                "credential_env": name + "_TOKEN",
                "maintainer_env": name + "_MAINT",
                "memory": ref(tenant),
                "text": text,
                "body_hash": digest,
                "body_generation": "body1",
                "projection_generation": "g1",
                "sources": [
                    {
                        "source_id": name + "_source",
                        "source_version": 1,
                        "content_hash": digest,
                        "locator": "fixture",
                    }
                ],
            }
        )
    case = F4Case.model_validate(
        {
            "run_id": "owned-run",
            "request": {
                "query": "private",
                "sources": "working",
                "selection": {"session_id": "same-session"},
            },
            "actors": actors,
            "maintenance_export": "/external/maintained-observations.json",
            "operator_env": "PLATFORM_OPERATOR_TOKEN",
            "configuration": {
                "version": "v1",
                "config_hash": "c" * 64,
                "deployment_id": "owned",
                "provider_ids": ["native"],
                "policy_versions": ["recall"],
                "activated_at": "2026-10-09T00:00:00.000Z",
            },
            "server_settings": {"candidate_limit": 1},
            "source_sha": "d" * 40,
            "image_digest": "sha256:" + "e" * 64,
            "backend_binding": "f" * 64,
            "model_binding": "a" * 64,
            "model_space": "native-space",
        }
    )
    events = []
    for stage in ("read", "packed"):
        payload = {
            "recall_id": "job-a",
            "memory": ref("T-A"),
            "stage": stage,
            "outcome": "succeeded",
            "access_key": "access-a",
            "representation": "original",
            "elapsed_ms": 1,
        }
        events.append(
            {
                "event_id": "event-" + stage,
                "event_type": "recall.access",
                "producer": "recall",
                "subject": {
                    "owner": "remember",
                    "object_type": "memory",
                    "object_id": "same-memory",
                    "scope": ref("T-A")["scope"],
                    "version": 1,
                },
                "subject_revision": 1,
                "occurred_at": "2026-10-09T00:00:00.000Z",
                "request_id": "request-a",
                "trace_id": "1" * 32,
                "initiator_id": "U01",
                "initiator_auth_epoch": 1,
                "payload": payload,
                "payload_hash": fingerprint(payload),
            }
        )
    candidate = hit("T-A", 0.6, 1)
    candidate["target"]["body_hash"] = actors[0]["body_hash"]
    raw = {
        "run_id": "owned-run",
        "operation_id": "original",
        "job_id": "job-a",
        "recall_id": "job-a",
        "trace_id": "1" * 32,
        "query_hash": sha256(b"private").hexdigest(),
        "config_hash": "c" * 64,
        "source_sha": "d" * 40,
        "image_digest": "sha256:" + "e" * 64,
        "backend_binding": "f" * 64,
        "model_binding": "a" * 64,
        "evidence_id": "receipt-a",
        "observer_principal_id": "legal-operator",
        "candidate_limit": 1,
        "unfiltered_probe": [],
        "candidates": [candidate],
        "qualified": [ref("T-A")],
        "body_reads": [ref("T-A")],
        "body_paths": ["authority"],
        "model_input_refs": [],
        "query_embedding_input_hash": sha256(b"private").hexdigest(),
        "result_refs": [ref("T-A")],
        "events": events,
    }
    return case, raw


def test_authorized_stage_receipt_binds_the_original_job_and_every_content_boundary():
    case, raw = maintained_case_and_stage()
    assert assert_stage_isolation(
        F4StageObservation.model_validate(raw),
        case,
        case.actors[0],
        "original",
        "job-a",
        "job-a",
        "1" * 32,
        "legal-operator",
    ) == {"event-read", "event-packed"}


@pytest.mark.parametrize(
    "fault",
    [
        "job_id",
        "query_hash",
        "backend_binding",
        "model_binding",
        "observer_principal_id",
        "qualified",
        "body_reads",
        "model_input_refs",
        "result_refs",
        "events",
        "generation",
    ],
)
def test_stale_or_foreign_maintenance_evidence_is_rejected(fault):
    case, original = maintained_case_and_stage()
    raw = deepcopy(original)
    if fault in {"qualified", "body_reads", "model_input_refs", "result_refs"}:
        raw[fault] = [ref("T-B")]
    elif fault == "events":
        raw["events"][0]["subject"]["scope"] = ref("T-B")["scope"]
    elif fault == "generation":
        raw["candidates"][0]["target"]["generation"] = "stale-generation"
    elif fault in {"query_hash", "backend_binding", "model_binding"}:
        raw[fault] = "9" * 64
    else:
        raw[fault] = "other-execution"
    with pytest.raises(AssertionError):
        assert_stage_isolation(
            F4StageObservation.model_validate(raw),
            case,
            case.actors[0],
            "original",
            "job-a",
            "job-a",
            "1" * 32,
            "legal-operator",
        )
