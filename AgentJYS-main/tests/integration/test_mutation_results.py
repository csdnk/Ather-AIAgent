"""Original synchronous results are read by ID, never reconstructed by another write."""

from hashlib import sha256

import httpx
import pytest
from fastapi.testclient import TestClient
from tests.integration.test_current_p2_http import command, eventually, headers
from tests.integration.test_current_p2_http import configuration as configuration
from tests.integration.test_operation_lookup import body
from tests.integration.test_p4_demo_temporal import ForwardToP3, business_writes

from aether_agent_memory.remember.contracts.models import DeleteRequest
from aether_agent_memory.runtime.contracts.models import Permission
from aether_agent_memory.runtime.foundation.common import fingerprint
from aether_p4_simulator.validation.client import P3ValidationClient
from aether_p4_simulator.validation.errors import ValidationError
from azure_component_service import Service

pytestmark = pytest.mark.integration


def mutate(client, action, operation="original"):
    saved, _ = command(client, "/p3/remember", body(), "seed")
    mid = saved["memories"][0]["memory_id"]
    item = client.get("/p3/remember/" + mid, headers=headers()).json()
    path, value = f"/p3/remember/{mid}/{action}", None
    if action == "lifecycle":
        value = {"expected_version": 1, "target": "archived", "reason": "test"}
    elif action == "retention":
        value = {
            "expected_version": 1,
            "expected_object_revision": item["object_revision"],
            "enabled": False,
            "reason": "test",
        }
    elif action == "delete":
        value = {"expected_revision": item["object_revision"], "reason": "test"}
    elif action in {"reflection", "consolidate"}:
        path = "/p3/remember/" + action
        value = (
            body()["selection"]
            if action == "consolidate"
            else {"selection": body()["selection"], "enabled": False, "reason": "test"}
        )
    elif action == "distill":
        path = "/p3/remember/distill"
        value = eventually(
            lambda: [
                entry["ref"]
                for entry in client.get("/p3/memories", headers=headers()).json()["items"]
                if entry["kind"] == "episodic"
            ]
        )
    elif action.startswith("source-"):
        path = f"/p3/sources/{saved['source']['source_id']}/{action[7:]}"
        value = {"expected_revision": 1, "reason": "test"}
    response = client.post(path, json=value, headers=headers(operation))
    assert response.status_code == 200, response.text
    return response.json(), mid


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
def test_original_mutation_result_survives_restart_without_another_post(configuration, action):
    kind = "source." + action[7:] if action.startswith("source-") else "remember." + action
    path = "/p3/mutation-receipts/original/result"
    with TestClient(Service(configuration).app()) as client:
        original, _ = mutate(client, action)
        expected = {
            key: value
            for key, value in original.items()
            if action != "lifecycle" or key != "content"
        }
        response = client.get(path, params={"kind": kind}, headers=headers())
        assert response.status_code == 200, response.text
        found = response.json()
        assert found["response"] == expected
        assert found["receipt"]["result_hash"] == fingerprint(expected)
        assert found["receipt"]["operation_id"] == "original" and found["receipt"]["kind"] == kind
        assert response.headers["Cache-Control"] == "no-store"
        assert client.get(path, params={"kind": kind}).status_code == 401
        foreign = client.get(path, params={"kind": kind}, headers=headers(user="eve"))
        assert foreign.status_code == 400 and foreign.json()["code"] == "COMMIT_UNCONFIRMED"
    with TestClient(Service(configuration).app()) as client:
        assert client.get(path, params={"kind": kind}, headers=headers()).json() == found


def test_lifecycle_result_keeps_original_state_after_later_activation(configuration):
    with TestClient(Service(configuration).app()) as client:
        original, mid = mutate(client, "lifecycle")
        activated = client.post(
            f"/p3/remember/{mid}/lifecycle",
            json={
                "expected_version": 1,
                "target": "active",
                "reason": "later",
            },
            headers=headers("later"),
        )
        assert activated.status_code == 200 and activated.json()["status"] == "active"
        result = client.get(
            "/p3/mutation-receipts/original/result?kind=remember.lifecycle", headers=headers()
        )
        assert result.status_code == 200, result.text
        assert result.json()["response"] == {
            key: value for key, value in original.items() if key != "content"
        }
        assert result.json()["response"]["status"] == "archived"
        assert result.json()["receipt"]["result_basis"] == "metadata_without_content"


def test_empty_consolidation_result_is_not_recomputed_after_new_input(configuration):
    with TestClient(Service(configuration).app()) as client:
        original = client.post(
            "/p3/remember/consolidate", json=body()["selection"], headers=headers("empty")
        )
        assert original.status_code == 200 and original.json() == {"task_ids": []}
        command(client, "/p3/remember", body(), "later-save")
        result = client.get(
            "/p3/mutation-receipts/empty/result?kind=remember.consolidate", headers=headers()
        )
        assert result.status_code == 200 and result.json()["response"] == {"task_ids": []}


@pytest.mark.parametrize("change", ["epoch", "read-permission", "result"])
def test_original_result_revalidates_identity_permission_and_integrity(configuration, change):
    service = Service(configuration)
    with TestClient(service.app()) as client:
        mutate(client, "retention")
        foundation = service.runtime.foundation
        principal = foundation.identity.authenticate("alice")
        if change == "result":
            ctx = foundation.identity.context("alice")
            ref = service.runtime.remember.mutations.ref(ctx, "original", "remember.retention")
            with foundation.uow.transaction() as tx:
                raw = tx.get(ref)
                tx.put_if_revision(ref, {**raw, "response": {"forged": True}}, tx.revision(ref))
        else:
            update = (
                {"auth_epoch": 2}
                if change == "epoch"
                else {
                    "auth_epoch": 2,
                    "permissions": tuple(p for p in principal.permissions if p != Permission.READ),
                }
            )
            foundation.identity.provision(
                [(sha256(b"alice").hexdigest(), principal.model_copy(update=update))]
            )
        target = "/p3/mutation-receipts/original/result?kind=remember.retention"
        if change == "read-permission":
            # Create this result under the new epoch so target READ is the tested fence.
            created = client.post("/p3/remember/consolidate", json={}, headers=headers("no-read"))
            assert created.status_code == 200, created.text
            target = "/p3/mutation-receipts/no-read/result?kind=remember.consolidate"
        refused = client.get(target, headers=headers())
        assert refused.status_code == (409 if change == "result" else 403), refused.text


def test_unknown_result_never_creates_an_operation(configuration):
    service = Service(configuration)
    with TestClient(service.app()) as client:
        result = client.get(
            "/p3/mutation-receipts/unknown/result?kind=remember.delete", headers=headers()
        )
        assert result.status_code == 400 and result.json()["code"] == "COMMIT_UNCONFIRMED"
        ctx = service.runtime.foundation.identity.context("alice")
        with service.runtime.foundation.uow.transaction() as tx:
            assert (
                tx.get(
                    service.runtime.remember.mutations.ref(
                        ctx,
                        "unknown",
                        "remember.delete",
                    )
                )
                is None
            )


def test_p4_reads_result_after_real_response_loss_without_reposting(configuration):
    service = Service(configuration)
    with TestClient(service.app()) as app:
        saved, _ = command(app, "/p3/remember", body(), "seed")
        mid = saved["memories"][0]["memory_id"]
        item = app.get("/p3/remember/" + mid, headers=headers()).json()
        path = f"/p3/remember/{mid}/delete"

        class LoseReply(ForwardToP3):
            original = None

            def handle_request(self, request):
                result = super().handle_request(request)
                if request.method == "POST" and request.url.path == path:
                    self.original = result.json()
                    raise httpx.ReadError("lost original mutation response", request=request)
                return result

        bridge = LoseReply(app, service.execution)
        caller = P3ValidationClient("http://testserver", "alice", transport=bridge)
        try:
            with pytest.raises(ValidationError):
                caller.delete_memory(
                    mid,
                    DeleteRequest(expected_revision=item["object_revision"], reason="test"),
                    "lost",
                )
        finally:
            caller.close()
        fresh = ForwardToP3(app, service.execution)
        observer = P3ValidationClient("http://testserver", "alice", transport=fresh)
        try:
            recovered = observer.mutation_result("lost", "remember.delete")
            assert recovered.response == bridge.original
            assert business_writes(fresh) == []
        finally:
            observer.close()
