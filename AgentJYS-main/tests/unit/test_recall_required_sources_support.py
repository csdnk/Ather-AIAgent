"""Strict evidence guard and transaction-witness regressions, not live acceptance."""

import json
from contextlib import contextmanager
from copy import deepcopy
from types import SimpleNamespace

import pytest

from aether_agent_memory.runtime.foundation.transactions import StorageTransaction
from recall_required_sources_support import (
    AccessEventWitness,
    assert_failed_consistently,
    strict_policy,
)
from unit.test_recall_tenant_isolation_support import ref


def failed_evidence():
    subject = {
        "owner": "recall",
        "object_type": "recall",
        "object_id": "recall-id",
        "scope": ref("T-A")["scope"],
    }
    task = {
        "task_id": "job",
        "owner_flow": "recall",
        "kind": "recall.execute",
        "subject": subject,
        "input_ref": subject,
        "idempotency_key": "operation",
        "input_hash": "b" * 64,
        "initiator_id": "U01",
        "initiator_auth_epoch": 1,
        "deadline_at": "2026-10-10T00:00:00.000Z",
        "state": "failed",
        "revision": 2,
        "attempt": 1,
        "effect_status": "no_effect",
        "error_code": "DEPENDENCY_UNAVAILABLE",
        "result_ref": None,
        "temporal": {"workflow_id": None, "binding": None, "diagnostic": None},
    }
    record = {
        "recall_id": "recall-id",
        "scope": subject["scope"],
        "state": "failed",
        "stage": "assemble",
        "revision": 2,
        "deadline_at": task["deadline_at"],
        "result_available": False,
        "reason": "required source unavailable",
    }
    events = {
        "append_attempts": [],
        "committed_events": [],
        "packed_attempt_count": 0,
        "packed_committed_count": 0,
    }
    return task, record, events


@pytest.mark.parametrize(
    "fault",
    [
        None,
        "success",
        "persisted-ref",
        "record-running",
        "record-available",
        "packed-attempt",
        "packed-commit",
        "delivered",
        "wrong-recall",
    ],
)
def test_failure_requires_agreement_between_task_record_result_and_events(fault):
    task, record, events = failed_evidence()
    if fault == "success":
        task.update(state="succeeded", error_code=None, result_ref=task["subject"])
    elif fault == "persisted-ref":
        task["result_ref"] = task["subject"]
    elif fault == "record-running":
        record["state"] = "running"
    elif fault == "record-available":
        record.update(state="completed", result_available=True)
    elif fault == "packed-attempt":
        events["packed_attempt_count"] = 1
    elif fault == "packed-commit":
        events["packed_committed_count"] = 1
    elif fault == "delivered":
        events["committed_events"] = [
            {"stage": "delivered", "outcome": "succeeded", "recall_id": "recall-id"}
        ]
    elif fault == "wrong-recall":
        record["recall_id"] = "unrelated-recall"
    if fault is None:
        assert_failed_consistently(task, record, events, "job")
    else:
        with pytest.raises((AssertionError, ValueError)):
            assert_failed_consistently(task, record, events, "job")


def test_strict_profile_is_independent_and_rejects_guessed_server_fields(tmp_path):
    profile = {
        "settings": {"max_items": 2},
        "policy_version": "approved-version",
        "decision_reference": "strict-decision",
        "partial_delivery": "forbid",
        "required_sources": ["working", "long_term"],
    }
    path = tmp_path / "strict.json"
    path.write_text(json.dumps(profile))
    settings, evidence = strict_policy(path)
    assert settings == {"max_items": 2} and evidence["confirmed"]
    assert evidence["expected_policy_version"] == "approved-version"
    assert len(evidence["manifest_sha256"]) == 64
    profile["settings"]["allow_partial"] = False
    path.write_text(json.dumps(profile))
    with pytest.raises(ValueError, match="allow_partial"):
        strict_policy(path)


def test_missing_strict_configuration_is_explicit_not_implicit_approval():
    settings, evidence = strict_policy(None)
    assert settings == {} and not evidence["confirmed"]
    assert "strict" in evidence["restriction"]


class Records:
    def __init__(self, values):
        self.values = values

    def get(self, namespace, tenant, key):
        return self.values.get((namespace, tenant, key))

    def put(self, namespace, tenant, key, value):
        self.values[(namespace, tenant, key)] = value

    def scan(self, namespace):
        return [
            (tenant, key, value)
            for (ns, tenant, key), value in self.values.items()
            if ns == namespace
        ]


class AtomicWitnessStore:
    """Real StorageTransaction over a tiny atomic record-port test implementation."""

    def __init__(self):
        self.values = {}

    @contextmanager
    def transaction(self):
        pending = deepcopy(self.values)
        tx = StorageTransaction(Records(pending), "test-cursor-key")
        yield tx
        self.values = pending


def test_event_witness_distinguishes_rollback_from_commit_and_forwards_delegate(monkeypatch):
    store = AtomicWitnessStore()
    delegated = []

    def append(tx, ctx, event):
        delegated.append(event.event_id)
        tx.write("delegate-events", event.event_id, {"event_id": event.event_id})

    events = SimpleNamespace(append=append)
    runtime = SimpleNamespace(foundation=SimpleNamespace(uow=store, events=events))
    witness = AccessEventWitness(runtime, monkeypatch)
    ctx = SimpleNamespace(operation_id="original-operation")
    event = SimpleNamespace(
        event_type="recall.access",
        event_id="packed-event",
        trace_id="a" * 32,
        payload={
            "recall_id": "recall-id",
            "memory": ref("T-A"),
            "stage": "packed",
            "outcome": "succeeded",
            "access_key": "key",
            "representation": "original",
            "elapsed_ms": 0,
        },
    )
    with pytest.raises(RuntimeError, match="rollback"), store.transaction() as tx:
        events.append(tx, ctx, event)
        raise RuntimeError("rollback")
    rolled_back = witness.observe(ctx.operation_id)
    assert rolled_back["packed_attempt_count"] == 1
    assert rolled_back["packed_committed_count"] == 0
    assert not rolled_back["committed_events"]
    with store.transaction() as tx:
        events.append(tx, ctx, event)
    committed = witness.observe(ctx.operation_id)
    assert committed["packed_attempt_count"] == 2
    assert committed["packed_committed_count"] == 1
    assert committed["committed_events"][0]["event_id"] == "packed-event"
    assert delegated == ["packed-event", "packed-event"]
    assert witness.observe("other-operation")["packed_committed_count"] == 0
