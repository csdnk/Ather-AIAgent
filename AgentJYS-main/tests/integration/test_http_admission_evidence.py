"""Server-derived HTTP evidence belongs to the original admission transaction."""

import json
from hashlib import sha256

import httpx
import pytest
from fastapi.testclient import TestClient
from tests.integration.test_current_p2_http import command, eventually, headers
from tests.integration.test_current_p2_http import configuration as configuration
from tests.integration.test_operation_lookup import body
from tests.integration.test_p4_demo_temporal import ForwardToP3, business_writes

from aether_agent_memory.remember.contracts.models import RememberRequest
from aether_agent_memory.runtime.contracts.client_runs import ClientOperation
from aether_agent_memory.runtime.contracts.http_evidence import HttpRequestEvidence
from aether_agent_memory.runtime.contracts.models import ErrorCode, ScopeSelector, TaskSpec
from aether_agent_memory.runtime.foundation.common import FoundationError
from aether_agent_memory.runtime.foundation.postgres import (
    PostgresTransaction as PostgresTransaction,
)
from aether_p4_simulator.validation.client import P3ValidationClient
from aether_p4_simulator.validation.errors import ValidationError
from azure_component_service import Service

pytestmark = pytest.mark.integration


@pytest.mark.parametrize(
    "kind", ["remember.save", "recall.execute", "remember.correct", "remember.document"]
)
def test_original_http_bytes_and_target_survive_semantic_retry_and_restart(configuration, kind):
    service = Service(configuration)
    operation = "original-http"
    with TestClient(service.app()) as client:
        eventually(lambda: client.get("/p3/readyz").status_code == 200)
        method, path, route = "POST", "/p3/remember", "/p3/remember"
        payload = body()
        if kind == "recall.execute":
            path = route = "/p3/recall"
            payload = {"query": "coffee", "selection": body()["selection"], "sources": "working"}
        elif kind == "remember.correct":
            saved, _ = command(client, "/p3/remember", body("seed"), "seed")
            memory = saved["memories"][0]
            path = f"/p3/remember/{memory['memory_id']}/correct"
            route = "/p3/remember/{memory_id}/correct"
            payload = {
                "expected_version": memory["version"],
                "content": "I now prefer tea.",
                "reason": "correction",
                "source": body("corrected")["source"],
            }
        elif kind == "remember.document":
            method, path, route = (
                "PUT",
                "/p3/documents/original?version=1",
                "/p3/documents/{document_id}",
            )
        raw = (
            b"original document"
            if kind == "remember.document"
            else json.dumps(payload, indent=2).encode()
        )
        content_type = "text/plain" if kind == "remember.document" else "application/json"
        response = client.request(
            method,
            path,
            content=raw,
            headers={**headers(operation), "Content-Type": content_type},
        )
        assert "X-P3-Job-ID" in response.headers, response.text
        job = response.headers["X-P3-Job-ID"]
        lookup = f"/p3/operation-requests/{operation}?kind={kind}"
        original = client.get(lookup, headers=headers()).json()
        expected = {
            "version": 1,
            "method": method,
            "route": route,
            "target": path,
            "body_hash": sha256(raw).hexdigest(),
            "content_type": content_type,
            "client_run": None,
        }
        assert original.get("http_request") == expected
        assert original["job_id"] == job
        # Equivalent parsed JSON must not replace the original HTTP bytes or task hash.
        repeated = client.request(
            method,
            path,
            content=raw if kind == "remember.document" else json.dumps(payload).encode(),
            headers={**headers(operation), "Content-Type": content_type},
        )
        assert repeated.headers["X-P3-Job-ID"] == job
        again = client.get(lookup, headers=headers()).json()
        assert again["http_request"] == expected and again["input_hash"] == original["input_hash"]
    with TestClient(Service(configuration).app()) as client:
        retained = client.get(lookup, headers=headers()).json()
        assert retained["job_id"] == job and retained["http_request"] == expected


@pytest.mark.parametrize("kind", ["remember.save", "recall.execute"])
def test_http_evidence_write_failure_rolls_back_original_admission(
    configuration, monkeypatch, kind
):
    service = Service(configuration)
    original = PostgresTransaction.write

    def reject(self, table, key, value):
        if table == "tasks" and isinstance(value, dict) and "http_request" in value:
            raise FoundationError(
                ErrorCode.DEPENDENCY_UNAVAILABLE, "injected evidence write failure"
            )
        return original(self, table, key, value)

    with TestClient(service.app()) as client:
        eventually(lambda: client.get("/p3/readyz").status_code == 200)
        monkeypatch.setattr(PostgresTransaction, "write", reject)
        response = client.post(
            "/p3/remember" if kind == "remember.save" else "/p3/recall",
            json=body()
            if kind == "remember.save"
            else {"query": "coffee", "selection": body()["selection"]},
            headers=headers("failed-admission"),
        )
        assert response.status_code == 503, response.text
        found = client.get(
            f"/p3/operation-requests/failed-admission?kind={kind}", headers=headers()
        )
        assert found.status_code == 200 and found.json()["state"] == "unconfirmed"


def test_http_retry_does_not_backfill_an_existing_non_http_task(configuration):
    service = Service(configuration)
    with TestClient(service.app()) as client:
        eventually(lambda: client.get("/p3/readyz").status_code == 200)
        ctx = service.runtime.foundation.identity.context("alice", operation_id="legacy")
        job = service.execution.accept(
            ctx, "remember.save", RememberRequest.model_validate(body()).model_dump(mode="json")
        )
        response = client.post("/p3/remember", json=body(), headers=headers("legacy"))
        assert response.headers["X-P3-Job-ID"] == job.job_id
        found = client.get(
            "/p3/operation-requests/legacy?kind=remember.save", headers=headers()
        ).json()
        assert found["state"] == "found" and found.get("http_request") is None


def test_http_retry_does_not_backfill_a_non_http_mutation(configuration):
    service = Service(configuration)
    with TestClient(service.app()) as client:
        eventually(lambda: client.get("/p3/readyz").status_code == 200)
        ctx = service.runtime.foundation.identity.context("alice", operation_id="legacy-mutation")
        service.runtime.remember.consolidate(ctx, ScopeSelector.model_validate(body()["selection"]))
        response = client.post(
            "/p3/remember/consolidate", json=body()["selection"], headers=headers("legacy-mutation")
        )
        assert response.status_code == 200, response.text
        found = client.get(
            "/p3/mutation-receipts/legacy-mutation?kind=remember.consolidate", headers=headers()
        ).json()
        assert found["state"] == "committed" and found["receipt"]["http_request"] is None


def test_ledger_idempotency_hit_cannot_replace_original_http_evidence(configuration):
    service = Service(configuration)
    with TestClient(service.app()) as client:
        eventually(lambda: client.get("/p3/readyz").status_code == 200)
        response = client.post("/p3/remember", json=body(), headers=headers("original"))
        job_id = response.headers["X-P3-Job-ID"]
        lookup = "/p3/operation-requests/original?kind=remember.save"
        first = client.get(lookup, headers=headers()).json()
        ctx = service.runtime.foundation.identity.context("alice", operation_id="original")
        task = service.execution.status(ctx, job_id)
        spec = TaskSpec(**{name: getattr(task, name) for name in TaskSpec.model_fields})
        altered = HttpRequestEvidence.model_validate(first["http_request"]).model_copy(
            update={"body_hash": "b" * 64}
        )
        with service.runtime.foundation.uow.transaction() as tx:
            reused = service.execution.ledger.admit(
                tx,
                ctx,
                spec.model_copy(update={"task_id": "alternate-allocation"}),
                http_request=altered,
            )
        assert reused.job_id == job_id
        assert client.get(lookup, headers=headers()).json()["http_request"] == first["http_request"]


def test_p4_confirms_original_document_version_after_actual_response_loss(configuration):
    service = Service(configuration)
    with TestClient(service.app()) as app:
        eventually(lambda: app.get("/p3/readyz").status_code == 200)

        class LoseReply(ForwardToP3):
            response = None

            def handle_request(self, request):
                response = super().handle_request(request)
                if request.method == "PUT":
                    self.response = response
                    raise httpx.ReadError("lost document admission reply", request=request)
                return response

        bridge = LoseReply(app, service.execution)
        caller = P3ValidationClient("http://testserver", "alice", transport=bridge)
        intents = []
        try:
            with caller.observe_effects(intents.append), pytest.raises(ValidationError):
                caller.upload_document(
                    "original-document", "Original document content.", "original"
                )
        finally:
            caller.close()
        assert len(intents) == 1 and intents[0].phase == "prepared"
        job = bridge.response.headers["X-P3-Job-ID"]
        fresh = ForwardToP3(app, service.execution)
        observer = P3ValidationClient("http://testserver", "alice", transport=fresh)
        try:
            confirmed = observer.confirm_operation(intents[0])
            assert confirmed.state == "matched" and confirmed.lookup.job_id == job
            altered = ClientOperation.prepare(
                method="PUT",
                path="/p3/documents/{document_id}",
                operation_id="original",
                request_hash=intents[0].request_hash,
                target="/gateway/p3/documents/original-document?version=2",
                content_type=intents[0].binding.content_type,
            )
            assert observer.confirm_operation(altered).state == "mismatch"
            assert business_writes(fresh) == []
            assert intents[0].phase == "prepared" and intents[0].job_id is None
        finally:
            observer.close()
