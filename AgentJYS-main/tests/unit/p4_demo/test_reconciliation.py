"""Protocol faults must not become permission to replay an original effect."""

from copy import deepcopy
from dataclasses import replace
from hashlib import sha256
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest

from aether_agent_memory.runtime.contracts.client_runs import ClientOperation
from aether_agent_memory.runtime.contracts.http_evidence import HTTP_EFFECT_ROUTES
from aether_agent_memory.runtime.foundation.common import fingerprint
from aether_p4_simulator.demo.service import DemoService
from aether_p4_simulator.demo.state import ExecutionData, RestoredExecution
from aether_p4_simulator.validation.client import P3ValidationClient
from aether_p4_simulator.validation.errors import ValidationError

from .support import NOW, fixed_definition
from .test_execution_state import owned


@pytest.fixture
def world(monkeypatch):
    scope, _, _, receipt, record = owned()
    payload = b'{"original":"private input"}'
    intent = ClientOperation.prepare(
        method="POST",
        path="/p3/remember",
        operation_id="save",
        request_hash=sha256(payload).hexdigest(),
        target="/p3/remember",
        content_type="application/json",
    )
    intent = intent.model_copy(update={"phase": "observed", "status_code": 200, "job_id": "job"})
    record = record.model_copy(
        update={"snapshot": {"state": "running", "operations": [intent.model_dump(mode="json")]}}
    )
    data = ExecutionData()
    data.parsed("save", receipt)
    data.consume("save")
    restored = RestoredExecution(
        record, fixed_definition("library-full"), data.envelope(record), data, True
    )
    ref = {
        "owner": "remember",
        "object_type": "command",
        "object_id": "save",
        "scope": scope.model_dump(mode="json"),
    }
    workflow = "p3/deployment/remember.save/job"
    lookup = {
        "operation_id": "save",
        "kind": "remember.save",
        "state": "found",
        "job_id": "job",
        "task_state": "succeeded",
        "input_hash": "a" * 64,
        "workflow_id": workflow,
        "http_request": {
            "method": "POST",
            "route": "/p3/remember",
            "target": "/p3/remember",
            "body_hash": intent.request_hash,
            "content_type": "application/json",
            "client_run": {
                "run_id": str(record.run_id),
                "owner_id": record.owner_id,
                "revision": 2,
            },
        },
    }
    task = {
        "task_id": "job",
        "kind": "remember.save",
        "owner_flow": "remember",
        "subject": ref,
        "input_ref": ref,
        "idempotency_key": "save",
        "input_hash": "a" * 64,
        "initiator_id": "u",
        "initiator_auth_epoch": 1,
        "deadline_at": NOW,
        "state": "succeeded",
        "revision": 2,
        "attempt": 1,
        "effect_status": "confirmed",
        "result_ref": ref,
        "temporal": {
            "workflow_id": workflow,
            "binding": {
                "namespace": "default",
                "workflow_id": workflow,
                "first_run_id": "first",
                "current_run_id": "first",
                "input_hash": "a" * 64,
                "plan_version": "v1",
            },
            "diagnostic": None,
        },
    }
    result = receipt.model_dump(mode="json")
    w = SimpleNamespace(
        record=record,
        restored=restored,
        payload=payload,
        input_status=200,
        input_hash=intent.request_hash,
        lookup=lookup,
        task=task,
        result=result,
        result_status=200,
        calls=[],
        final_record=record,
    )

    def reply(request):
        w.calls.append((request.method, request.url.path))
        path = request.url.path
        if "/inputs/" in path:
            return httpx.Response(
                w.input_status, content=w.payload, headers={"X-P3-Request-Hash": w.input_hash}
            )
        if path == "/p3/operation-requests/save":
            return httpx.Response(200, json=w.lookup)
        if path == "/p3/mutation-receipts/save":
            return httpx.Response(200, json=w.lookup)
        if path == "/p3/mutation-receipts/save/result":
            return httpx.Response(w.result_status, json=w.result)
        if path == "/p3/operations/job":
            return httpx.Response(200, json=w.task)
        if path == "/p3/operations/job/result":
            return httpx.Response(w.result_status, json=w.result)
        pytest.fail(f"unexpected reconciliation request: {request.method} {path}")

    client = P3ValidationClient("http://p3.test", "secret", transport=httpx.MockTransport(reply))
    service = DemoService(client, registry=SimpleNamespace(get=lambda _: w.final_record))
    monkeypatch.setattr(service, "restore_execution", lambda _: w.restored)
    w.service = service
    yield w
    service.close()


def test_original_result_matches_consumed_descriptor_without_changing_it(world):
    original = deepcopy(world.restored.data.model_dump(mode="json"))
    report = world.service.reconcile_execution(str(world.record.run_id))
    (item,) = report.findings
    assert item.state == "result_available" and item.result_match == "matched"
    assert item.input_state == "matched" and item.admission_state == "matched"
    assert item.saved_result.consumed and not item.observed_result.consumed
    assert item.task.task_id == "job" and item.confirmation.lookup.job_id == "job"
    assert world.restored.data.model_dump(mode="json") == original
    assert all(method == "GET" for method, _ in world.calls) and len(world.calls) == 4
    assert "private input" not in item.model_dump_json()


@pytest.mark.parametrize(
    "fault",
    ["owner", "run", "revision", "missing_admission", "body", "target", "content_type", "job"],
)
def test_original_admission_mismatch_never_reads_a_result(world, fault):
    evidence = world.lookup["http_request"]
    if fault == "owner":
        evidence["client_run"]["owner_id"] = "other"
    elif fault == "run":
        evidence["client_run"]["run_id"] = str(uuid4())
    elif fault == "revision":
        evidence["client_run"]["revision"] = world.record.revision + 1
    elif fault == "missing_admission":
        evidence["client_run"] = None
    elif fault == "body":
        evidence["body_hash"] = "f" * 64
    elif fault == "target":
        # Correction has a variable resource target; wrong route is invalid evidence as well.
        evidence["target"] = "/p3/remember/foreign/correct"
    elif fault == "content_type":
        evidence["content_type"] = "text/plain"
    else:
        world.lookup["job_id"] = "other"
    (item,) = world.service.reconcile_execution(str(world.record.run_id)).findings
    assert item.state in {"mismatch", "unconfirmed"}
    assert not any(path.endswith("/result") for _, path in world.calls)


@pytest.mark.parametrize("field", ["kind", "input_hash", "workflow", "binding_input"])
def test_different_job_identity_is_not_accepted(world, field):
    if field == "kind":
        world.task["kind"] = "recall.execute"
    elif field == "input_hash":
        world.task[field] = "b" * 64
    elif field == "binding_input":
        world.task["temporal"]["binding"]["input_hash"] = "b" * 64
    else:
        world.task["temporal"]["workflow_id"] = "other"
        world.task["temporal"]["binding"]["workflow_id"] = "other"
    (item,) = world.service.reconcile_execution(str(world.record.run_id)).findings
    assert item.state == "mismatch"
    assert not any(path.endswith("/result") for _, path in world.calls)


@pytest.mark.parametrize(
    ("state", "effect", "expected"),
    [
        ("pending", "not_started", "pending"),
        ("failed", "no_effect", "failed"),
        ("cancelled", "no_effect", "failed"),
        ("recovery_wait", "unknown", "attention_required"),
        ("attention_required", "unknown", "attention_required"),
    ],
)
def test_unfinished_or_unknown_task_is_not_result_success(world, state, effect, expected):
    world.task.update(state=state, effect_status=effect, result_ref=None)
    (item,) = world.service.reconcile_execution(str(world.record.run_id)).findings
    assert item.state == expected and item.observed_result is None
    assert item.confirmation.lookup.task_state == "succeeded"  # Preserve both observations.
    assert not any(path.endswith("/result") for _, path in world.calls)


@pytest.mark.parametrize(
    "fault", ["result", "input", "missing_input", "invalidated", "unconfirmed"]
)
def test_missing_or_changed_original_evidence_stays_explicit(world, fault):
    if fault == "result":
        world.result["phase"] = "ready"
    elif fault == "input":
        world.payload = b"changed"
    elif fault == "missing_input":
        world.input_status = 404
    elif fault == "invalidated":
        world.result_status, world.result = (
            410,
            {"code": "RESULT_INVALIDATED", "message": "private server detail"},
        )
    else:
        world.lookup = {"operation_id": "save", "kind": "remember.save", "state": "unconfirmed"}
    (item,) = world.service.reconcile_execution(str(world.record.run_id)).findings
    expected = {
        "result": "mismatch",
        "input": "mismatch",
        "missing_input": "result_unavailable",
        "invalidated": "result_unavailable",
        "unconfirmed": "unconfirmed",
    }[fault]
    assert item.state == expected
    if fault == "result":
        assert item.result_match == "mismatch" and item.saved_result.consumed
    if fault == "invalidated":
        assert item.errors[0].code == "recall_invalidated"
    assert "private server detail" not in item.model_dump_json()


def test_run_changed_during_reads_rejects_whole_report(world):
    world.final_record = world.record.model_copy(update={"revision": world.record.revision + 1})
    with pytest.raises(ValidationError) as error:
        world.service.reconcile_execution(str(world.record.run_id))
    assert error.value.code == "execution_state_changed"


def test_missing_admission_cannot_hide_a_known_input_mismatch(world):
    world.payload = b"changed"
    world.lookup["http_request"]["client_run"] = None
    (item,) = world.service.reconcile_execution(str(world.record.run_id)).findings
    assert item.state == "mismatch" and item.input_state == "mismatch"
    assert item.admission_state == "unconfirmed"


def test_total_deadline_includes_restore_and_refuses_any_later_lookup(world, monkeypatch):
    current = [1.0]
    monkeypatch.setattr("aether_p4_simulator.validation.client.time.monotonic", lambda: current[0])

    def restore(_):
        current[0] += 6
        return world.restored

    monkeypatch.setattr(world.service, "restore_execution", restore)
    with pytest.raises(ValidationError) as error:
        world.service.reconcile_execution(str(world.record.run_id), timeout_seconds=5)
    assert error.value.code == "reconciliation_timeout" and world.calls == []


@pytest.mark.parametrize(
    ("owner", "revision", "expected"),
    [
        ("owner", 2, "matched"),
        ("owner", 3, "mismatch"),
        ("replacement", 2, "mismatch"),
        ("replacement", 3, "matched"),
    ],
)
def test_admission_owner_must_match_its_original_revision_interval(
    world, owner, revision, expected
):
    from aether_agent_memory.runtime.contracts.client_runs import ClientRunRecord

    record = ClientRunRecord.model_validate(
        {
            **world.record.model_dump(mode="json"),
            "owner_id": "replacement",
            "ownership": {
                "epochs": [
                    {"owner_id": "owner", "first_revision": 1},
                    {"owner_id": "replacement", "first_revision": 3, "transfer_id": "transfer"},
                ],
                "recovery_transfer_id": "transfer",
            },
        }
    )
    world.record = world.final_record = record
    world.restored = replace(world.restored, record=record)
    world.lookup["http_request"]["client_run"].update(owner_id=owner, revision=revision)
    (item,) = world.service.reconcile_execution(str(record.run_id)).findings
    assert item.admission_state == expected
    assert item.state == ("result_available" if expected == "matched" else "mismatch")


@pytest.mark.parametrize("fault", [None, "document_id", "document_version", "expected_hash"])
def test_uncaptured_document_result_must_match_original_target_and_bytes(world, fault):
    intent = ClientOperation.prepare(
        method="PUT",
        path="/p3/documents/{document_id}",
        operation_id="save",
        request_hash=world.input_hash,
        target="/p3/documents/original?version=v1",
        content_type="text/plain",
    )
    world.record = world.record.model_copy(
        update={"snapshot": {"state": "running", "operations": [intent.model_dump(mode="json")]}}
    )
    world.final_record = world.record
    data = ExecutionData()
    world.restored = RestoredExecution(
        world.record, world.restored.definition, data.envelope(world.record), data, True
    )
    world.lookup["kind"] = world.task["kind"] = "remember.document"
    world.lookup["http_request"].update(
        method="PUT", route=intent.path, target=intent.binding.target, content_type="text/plain"
    )
    world.result = {
        "kind": "document",
        "provider_id": "uploads",
        "document_id": "original",
        "document_version": "v1",
        "expected_hash": world.input_hash,
    }
    if fault:
        world.result[fault] = "f" * 64 if fault == "expected_hash" else "other"
    (item,) = world.service.reconcile_execution(str(world.record.run_id)).findings
    assert item.state == ("mismatch" if fault else "result_available")
    if fault:
        assert item.observed_result is None


def use_mutation(world, kind):
    method, route = HTTP_EFFECT_ROUTES[kind]
    target = route.replace("{memory_id}", "m1").replace("{source_id}", "s1")
    intent = ClientOperation.prepare(
        method=method,
        path=route,
        operation_id="save",
        request_hash=sha256(world.payload).hexdigest(),
        target=target,
        content_type="application/json",
    )
    intent = intent.model_copy(update={"phase": "observed", "status_code": 200})
    world.record = world.record.model_copy(
        update={"snapshot": {"state": "running", "operations": [intent.model_dump(mode="json")]}}
    )
    world.final_record = world.record
    data = ExecutionData()
    world.restored = RestoredExecution(
        world.record, world.restored.definition, data.envelope(world.record), data, True
    )
    if kind == "remember.consolidate":
        response = {"task_ids": ["child"]}
    elif kind in {"remember.distill", "remember.reprocess", "remember.reindex"}:
        response = {"task_id": "child"}
    elif kind in {"remember.retention", "remember.reflection"}:
        response = {"enabled": False}
    elif kind == "remember.lifecycle":
        # This is an immutable metadata witness, not a hydrated MemorySnapshot.
        response = {"status": "archived", "content_hash": "a" * 64}
    else:
        response = {
            "operation_id": "save",
            "blocked": True,
            "cleanup_state": "pending",
            "task_ids": ["child"],
            "remaining_targets": ["m1"],
        }
    evidence = deepcopy(world.lookup["http_request"])
    evidence.update(method=method, route=route, target=target)
    receipt = {
        "operation_id": "save",
        "kind": kind,
        "targets": [world.task["subject"]],
        "intent_hash": "a" * 64,
        "result_hash": fingerprint(response),
        "result_basis": "metadata_without_content" if kind == "remember.lifecycle" else "response",
        "task_ids": ["child"],
        "committed_at": NOW,
        "http_request": evidence,
    }
    world.lookup = {"operation_id": "save", "kind": kind, "state": "committed", "receipt": receipt}
    world.result = {"receipt": deepcopy(receipt), "response": response}


@pytest.mark.parametrize(
    ("kind", "model"),
    [
        ("remember.consolidate", "ConsolidateData"),
        ("remember.distill", "TaskData"),
        ("remember.reprocess", "TaskData"),
        ("remember.reindex", "TaskData"),
        ("remember.retention", "ObjectData"),
        ("remember.reflection", "ObjectData"),
        ("remember.lifecycle", "MemorySnapshot"),
        ("remember.delete", "DeleteReceipt"),
        ("source.delete", "DeleteReceipt"),
        ("source.revoke", "DeleteReceipt"),
    ],
)
def test_all_original_mutation_results_preserve_commit_and_task_distinction(world, kind, model):
    use_mutation(world, kind)
    (item,) = world.service.reconcile_execution(str(world.record.run_id)).findings
    assert item.state == "result_available" and item.result_match == "not_captured"
    assert item.observed_result.result_type == model and not item.observed_result.consumed
    assert item.observed_result.parsed_hash == world.lookup["receipt"]["result_hash"]
    assert item.confirmation.lookup.receipt.task_ids == ("child",) and item.task is None
    assert all(method == "GET" for method, _ in world.calls) and len(world.calls) == 3
    if kind == "remember.lifecycle":
        assert item.result_validation == "committed_metadata"
        assert item.observed_result.basis == "metadata_without_content"


@pytest.mark.parametrize("fault", ["receipt", "response", "task_reference"])
def test_changed_mutation_result_or_commit_receipt_is_rejected(world, fault):
    use_mutation(world, "remember.distill")
    if fault == "receipt":
        world.result["receipt"]["intent_hash"] = "b" * 64
    elif fault == "response":
        world.result["response"]["task_id"] = "other"
    else:
        world.result["response"]["task_id"] = "other"
        digest = fingerprint(world.result["response"])
        world.result["receipt"]["result_hash"] = digest
        world.lookup["receipt"]["result_hash"] = digest
    (item,) = world.service.reconcile_execution(str(world.record.run_id)).findings
    assert item.state == "mismatch" and item.observed_result is None
