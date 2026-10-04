"""Original mutation receipts with real current P2 bodies and reference metadata."""

from hashlib import sha256

import httpx
import pytest
from fastapi.testclient import TestClient
from tests.integration.test_current_p2_http import command, eventually, headers
from tests.integration.test_current_p2_http import configuration as configuration
from tests.integration.test_operation_lookup import body
from tests.integration.test_p4_demo_temporal import ForwardToP3, business_writes

from aether_agent_memory.remember.contracts.models import DeleteRequest
from aether_agent_memory.runtime.foundation.common import fingerprint
from aether_p4_simulator.validation.client import P3ValidationClient
from aether_p4_simulator.validation.errors import ValidationError
from azure_component_service import Service

pytestmark = pytest.mark.integration


@pytest.mark.parametrize(
    "action",
    [
        "lifecycle",
        "retention",
        "delete",
        "reindex",
        "reprocess",
        "reflection",
        "consolidate",
        "distill",
        "source-delete",
        "source-revoke",
    ],
)
def test_committed_receipt_survives_restart_and_does_not_disclose_body(configuration, action):
    service = Service(configuration)
    operation = "original-mutation"
    kind = "source." + action[7:] if action.startswith("source-") else "remember." + action
    lookup = "/p3/mutation-receipts/" + operation
    with TestClient(service.app()) as client:
        eventually(lambda: client.get("/p3/readyz").status_code == 200)
        saved, _ = command(client, "/p3/remember", body(), "seed")
        mid = saved["memories"][0]["memory_id"]
        item = client.get("/p3/remember/" + mid, headers=headers()).json()
        path = f"/p3/remember/{mid}/{action}"
        request = None
        if action == "lifecycle":
            request = {"expected_version": 1, "target": "archived", "reason": "test"}
        elif action == "retention":
            request = {
                "expected_version": 1,
                "expected_object_revision": item["object_revision"],
                "enabled": False,
                "reason": "test",
            }
        elif action == "delete":
            request = {"expected_revision": item["object_revision"], "reason": "test"}
        elif action in {"reflection", "consolidate"}:
            path = "/p3/remember/" + action
            request = (
                body()["selection"]
                if action == "consolidate"
                else {"selection": body()["selection"], "enabled": False, "reason": "test"}
            )
        elif action == "distill":
            path = "/p3/remember/distill"
            episodes = eventually(
                lambda: [
                    entry["ref"]
                    for entry in client.get("/p3/memories", headers=headers()).json()["items"]
                    if entry["kind"] == "episodic"
                ]
            )
            request = episodes
        elif action.startswith("source-"):
            path = f"/p3/sources/{saved['source']['source_id']}/{action[7:]}"
            request = {"expected_revision": 1, "reason": "test"}
        response = client.post(path, json=request, headers=headers(operation))
        assert response.status_code == 200, response.text
        original = response.json()
        found = client.get(lookup, params={"kind": kind}, headers=headers())
        assert found.status_code == 200, found.text
        value = found.json()
        assert value["state"] == "committed"
        result_basis = {
            key: value
            for key, value in original.items()
            if action != "lifecycle" or key != "content"
        }
        assert value["receipt"]["result_hash"] == fingerprint(result_basis)
        assert value["receipt"]["result_basis"] == (
            "metadata_without_content" if action == "lifecycle" else "response"
        )
        assert value["receipt"]["operation_id"] == operation
        assert value["receipt"]["kind"] == kind
        assert value["receipt"].get("http_request") == {
            "version": 1,
            "method": "POST",
            "route": "/p3/sources/{source_id}/" + action[7:]
            if action.startswith("source-")
            else "/p3/remember/" + action
            if action in {"consolidate", "distill", "reflection"}
            else "/p3/remember/{memory_id}/" + action,
            "target": path,
            "body_hash": sha256(response.request.content).hexdigest(),
            "content_type": response.request.headers.get("content-type", ""),
            "client_run": None,
        }
        assert "response" not in value["receipt"] and "content" not in value["receipt"]
        assert client.get(lookup, params={"kind": kind}).status_code == 401
        foreign = client.get(lookup, params={"kind": kind}, headers=headers(user="eve"))
        assert foreign.status_code == 200 and foreign.json()["state"] == "unconfirmed"
        assert client.post(path, json=request, headers=headers(operation)).json() == original

    with TestClient(Service(configuration).app()) as client:
        assert client.get(lookup, params={"kind": kind}, headers=headers()).json() == value
        assert client.post(path, json=request, headers=headers(operation)).json() == original


def test_lookup_unknown_and_revoked_identity_do_not_create_a_receipt(configuration):
    service = Service(configuration)
    lookup = "/p3/mutation-receipts/original?kind=remember.consolidate"
    with TestClient(service.app()) as client:
        unknown = client.get(lookup, headers=headers())
        assert unknown.status_code == 200, unknown.text
        assert unknown.json()["state"] == "unconfirmed" and unknown.json()["receipt"] is None
        response = client.post(
            "/p3/remember/consolidate", json=body()["selection"], headers=headers("original")
        )
        assert response.status_code == 200, response.text
        identity = service.runtime.foundation.identity
        principal = identity.authenticate("alice")
        identity.provision(
            [(sha256(b"alice").hexdigest(), principal.model_copy(update={"auth_epoch": 2}))]
        )
        assert client.get(lookup, headers=headers()).status_code == 403


@pytest.mark.parametrize("action", ["delete", "reprocess"])
def test_p4_reads_original_receipt_after_losing_actual_committed_response(configuration, action):
    service = Service(configuration)
    with TestClient(service.app()) as app:
        eventually(lambda: app.get("/p3/readyz").status_code == 200)
        saved, _ = command(app, "/p3/remember", body(), "seed")
        mid = saved["memories"][0]["memory_id"]
        item = app.get("/p3/remember/" + mid, headers=headers()).json()
        path = f"/p3/remember/{mid}/{action}"

        class LoseReply(ForwardToP3):
            response = None

            def handle_request(self, request):
                response = super().handle_request(request)
                if request.method == "POST" and request.url.path == path:
                    self.response = response
                    raise httpx.ReadError("lost real committed mutation response", request=request)
                return response

        bridge = LoseReply(app, service.execution)
        intents = []
        caller = P3ValidationClient("http://testserver", "alice", transport=bridge)
        try:
            with caller.observe_effects(intents.append), pytest.raises(ValidationError):
                caller._send(
                    "POST",
                    path,
                    operation_id="lost-reply",
                    write=True,
                    body=DeleteRequest(expected_revision=item["object_revision"], reason="test")
                    if action == "delete"
                    else None,
                )
        finally:
            caller.close()
        assert len(intents) == 1 and intents[0].phase == "prepared"
        assert bridge.response is not None and bridge.response.status_code == 200
        original = bridge.response.json()
        fresh = ForwardToP3(app, service.execution)
        observer = P3ValidationClient("http://testserver", "alice", transport=fresh)
        try:
            found = observer.lookup_mutation("lost-reply", "remember." + action)
            assert found.state == "committed" and found.receipt is not None
            assert found.receipt.result_hash == fingerprint(original)
            assert found.receipt.targets[0].object_id == mid
            original_tasks = original["task_ids"] if action == "delete" else [original["task_id"]]
            assert list(found.receipt.task_ids) == original_tasks
            confirmed = observer.confirm_operation(intents[0])
            assert confirmed.state == "matched" and confirmed.lookup == found
            assert business_writes(fresh) == []
        finally:
            observer.close()
