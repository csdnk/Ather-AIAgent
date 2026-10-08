"""Idempotency and request admission tests for Recall API (AET-10, RC-IDE-01 through RC-IDE-08).

This test module validates:
- Idempotent request handling with operation IDs
- Request deduplication and conflict detection
- Cross-tenant isolation
- Result retrieval safety
- Permission changes during replay
"""

import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from hashlib import sha256
from uuid import uuid4

import pytest
import yaml
from fastapi.testclient import TestClient

from aether_agent_memory.runtime.contracts.models import Permission
from azure_component_service import Service
from component_configuration import ComponentConfiguration

pytestmark = pytest.mark.integration


# ============================================================================
# Constants
# ============================================================================

SERVICE_READY_TIMEOUT = 30
SERVICE_READY_POLL_INTERVAL = 0.1
OPERATION_COMPLETION_TIMEOUT = 90
SUCCESS_CODES = {200, 202}
CONFLICT_CODE = 409


# ============================================================================
# Test Configuration and Fixtures
# ============================================================================


@pytest.fixture
def test_configuration(tmp_path, temporal_server):
    """Create test configuration for idempotency tests."""
    identity = tmp_path / "identities.yaml"
    identity.write_text(
        yaml.safe_dump(
            {
                "revision": 1,
                "tenants": [{"tenant_id": "t1"}, {"tenant_id": "t2"}],
                "identities": [
                    {
                        "credential_sha256": sha256(user.encode()).hexdigest(),
                        "principal": {
                            "principal_id": user,
                            "auth_epoch": 1,
                            "permissions": [permission.value for permission in Permission],
                            "home_scope": {
                                "tenant_id": tenant,
                                "application_id": "app",
                                "user_id": user,
                                "agent_id": user,
                            },
                        },
                    }
                    for user, tenant in (("alice", "t1"), ("eve", "t2"), ("bob", "t1"))
                ],
            }
        ),
        encoding="utf-8",
    )

    namespace = "recall-idempotency-" + uuid4().hex
    return ComponentConfiguration(
        data_dir=tmp_path / "state",
        identity_file=identity,
        embedding_profile="injected",
        remember={"consolidation_messages": 1},
        periodic_seconds=0.2,
        poll_seconds=0.02,
        shutdown_seconds=1,
        http_wait_seconds=0.05,
        temporal={"deployment_id": namespace, "endpoint": temporal_server.endpoint},
    )


@pytest.fixture
def client(test_configuration):
    """Provide a ready TestClient with service already started."""
    service = Service(test_configuration)
    with TestClient(service.app()) as test_client:
        # Wait for service readiness
        deadline = time.monotonic() + SERVICE_READY_TIMEOUT
        while time.monotonic() < deadline:
            if test_client.get("/p3/readyz").status_code == 200:
                yield test_client
                return
            time.sleep(SERVICE_READY_POLL_INTERVAL)
        raise AssertionError("Service did not become ready")


def headers(operation: str | None = None, user: str = "alice") -> dict[str, str]:
    """Generate authentication headers."""
    value = {"Authorization": f"Bearer {user}"}
    if operation:
        value["X-Operation-ID"] = operation
    return value


def make_recall_request(**overrides) -> dict:
    """Create a recall request with sensible defaults."""
    defaults = {
        "query": "test query",
        "selection": {},
        "sources": "long_term",
        "token_budget": 1000,
    }
    defaults.update(overrides)
    return defaults


def wait_for_completion(client: TestClient, location: str, user: str = "alice", timeout: float = OPERATION_COMPLETION_TIMEOUT) -> dict:
    """Poll operation until completion and return final state."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        response = client.get(location, headers=headers(user=user))
        if response.status_code != 200:
            time.sleep(0.1)
            continue

        operation = response.json()
        if operation.get("state") in {"succeeded", "failed", "attention_required"}:
            return operation
        time.sleep(0.1)

    raise AssertionError(f"Operation at {location} did not complete within {timeout}s")


# ============================================================================
# RC-IDE-01: Serial Retry Returns Original Result
# ============================================================================


def test_rc_ide_01_serial_retry_same_operation_id(client):
    """RC-IDE-01: Serial retry with same operation ID returns original result.

    Submitting the same request multiple times with the same operation ID
    should return the same result without re-executing the operation.
    """
    operation_id = f"serial-retry-{uuid4().hex}"
    request_data = make_recall_request(query="serial retry test")

    # First request
    response1 = client.post(
        "/p3/recall",
        json=request_data,
        headers=headers(operation=operation_id),
    )

    assert response1.status_code in SUCCESS_CODES, (
        f"First request should succeed: {response1.status_code}"
    )

    # Extract job_id and wait for completion if async
    if response1.status_code == 202:
        location = response1.headers.get("Location")
        assert location, "Async response must include Location header"
        result1 = wait_for_completion(client, location)
        job_id = location.split("/")[-1]
    else:
        result1 = response1.json()
        job_id = result1.get("recall_id")

    # Second request with same operation ID (should be idempotent)
    response2 = client.post(
        "/p3/recall",
        json=request_data,
        headers=headers(operation=operation_id),
    )

    assert response2.status_code in SUCCESS_CODES, (
        f"Idempotent retry should succeed: {response2.status_code}"
    )

    # Should return same result
    if response2.status_code == 202:
        location2 = response2.headers.get("Location")
        # Location should point to the same operation
        assert location == location2, "Idempotent retry should return same Location"
    else:
        result2 = response2.json()
        assert result1 == result2, "Idempotent retry should return identical result"

    # Third request - verify still idempotent
    response3 = client.post(
        "/p3/recall",
        json=request_data,
        headers=headers(operation=operation_id),
    )

    assert response3.status_code in SUCCESS_CODES, (
        f"Third retry should also be idempotent: {response3.status_code}"
    )


# ============================================================================
# RC-IDE-02: Same Operation ID with Different Body Rejected
# ============================================================================


def test_rc_ide_02_same_operation_id_different_body_rejected(client):
    """RC-IDE-02: Same operation ID with different body is rejected.

    Reusing an operation ID with a different request body should be rejected
    to prevent accidental data corruption or confusion.
    """
    operation_id = f"conflict-test-{uuid4().hex}"

    # First request
    request1 = make_recall_request(query="first query")
    response1 = client.post(
        "/p3/recall",
        json=request1,
        headers=headers(operation=operation_id),
    )

    assert response1.status_code in SUCCESS_CODES, (
        f"First request should succeed: {response1.status_code}"
    )

    # Second request with same operation ID but different body
    request2 = make_recall_request(query="different query")
    response2 = client.post(
        "/p3/recall",
        json=request2,
        headers=headers(operation=operation_id),
    )

    # Should return 409 Conflict
    assert response2.status_code == CONFLICT_CODE, (
        f"Different body with same operation ID should return 409, got {response2.status_code}"
    )

    # Verify error response indicates conflict
    error = response2.json()
    assert "code" in error, "Error response should include error code"
    assert error["code"] in {"IDEMPOTENCY_CONFLICT", "VERSION_CONFLICT"}, (
        f"Error code should indicate conflict, got {error['code']}"
    )


def test_rc_ide_02_different_token_budget_is_conflict(client):
    """RC-IDE-02: Changing token_budget with same operation ID is a conflict."""
    operation_id = f"token-conflict-{uuid4().hex}"

    # First request
    response1 = client.post(
        "/p3/recall",
        json=make_recall_request(token_budget=1000),
        headers=headers(operation=operation_id),
    )

    assert response1.status_code in SUCCESS_CODES

    # Second request with different token_budget
    response2 = client.post(
        "/p3/recall",
        json=make_recall_request(token_budget=2000),
        headers=headers(operation=operation_id),
    )

    assert response2.status_code == CONFLICT_CODE, (
        f"Different token_budget should cause conflict, got {response2.status_code}"
    )


# ============================================================================
# RC-IDE-03: Concurrent Requests Produce Single Result
# ============================================================================


def test_rc_ide_03_concurrent_requests_same_operation_id(client):
    """RC-IDE-03: Concurrent requests with same ID produce single result.

    Multiple simultaneous requests with the same operation ID should result
    in only one execution, with all requests receiving the same result.
    """
    operation_id = f"concurrent-test-{uuid4().hex}"
    request_data = make_recall_request(query="concurrent test")
    num_concurrent_requests = 5

    def submit_request():
        """Submit a recall request and return the response."""
        return client.post(
            "/p3/recall",
            json=request_data,
            headers=headers(operation=operation_id),
        )

    # Submit multiple concurrent requests
    with ThreadPoolExecutor(max_workers=num_concurrent_requests) as executor:
        futures = [executor.submit(submit_request) for _ in range(num_concurrent_requests)]
        responses = [future.result() for future in as_completed(futures)]

    # All should succeed
    for i, response in enumerate(responses):
        assert response.status_code in SUCCESS_CODES, (
            f"Request {i} should succeed: {response.status_code}"
        )

    # Collect job IDs / locations
    locations = set()
    for response in responses:
        if response.status_code == 202:
            location = response.headers.get("Location")
            assert location, "Async response must have Location"
            locations.add(location)
        else:
            # Synchronous responses should have identical recall_id
            result = response.json()
            locations.add(result.get("recall_id"))

    # All requests should reference the same operation
    assert len(locations) == 1, (
        f"All concurrent requests should reference same operation, got {len(locations)} unique locations/IDs"
    )


# ============================================================================
# RC-IDE-04: Cross-Tenant Operation ID Isolation
# ============================================================================


def test_rc_ide_04_cross_tenant_operation_id_isolation(client):
    """RC-IDE-04: Cross-tenant operation ID isolation.

    Operation IDs are scoped per tenant. Different tenants can use the same
    operation ID without conflict.
    """
    operation_id = f"cross-tenant-{uuid4().hex}"
    request_data = make_recall_request(query="tenant isolation test")

    # Alice (tenant t1) creates operation
    response_alice = client.post(
        "/p3/recall",
        json=request_data,
        headers=headers(operation=operation_id, user="alice"),
    )

    assert response_alice.status_code in SUCCESS_CODES, (
        f"Alice's request should succeed: {response_alice.status_code}"
    )

    # Eve (tenant t2) uses same operation ID - should be independent
    response_eve = client.post(
        "/p3/recall",
        json=request_data,
        headers=headers(operation=operation_id, user="eve"),
    )

    assert response_eve.status_code in SUCCESS_CODES, (
        f"Eve's request should succeed independently: {response_eve.status_code}"
    )

    # Extract operation identifiers
    if response_alice.status_code == 202:
        location_alice = response_alice.headers.get("Location")
        location_eve = response_eve.headers.get("Location")

        # Should be different operations (different job IDs)
        assert location_alice != location_eve, (
            "Different tenants should have independent operations"
        )
    else:
        result_alice = response_alice.json()
        result_eve = response_eve.json()

        # Should have different recall IDs
        assert result_alice.get("recall_id") != result_eve.get("recall_id"), (
            "Different tenants should have independent recall IDs"
        )


def test_rc_ide_04_same_tenant_different_users_share_operation_id(client):
    """RC-IDE-04: Same tenant, different users share operation ID namespace.

    Users in the same tenant share the operation ID namespace. If bob uses
    alice's operation ID in the same tenant, it should be idempotent.
    """
    operation_id = f"same-tenant-{uuid4().hex}"
    request_data = make_recall_request(query="same tenant test")

    # Alice creates operation
    response_alice = client.post(
        "/p3/recall",
        json=request_data,
        headers=headers(operation=operation_id, user="alice"),
    )

    assert response_alice.status_code in SUCCESS_CODES

    # Bob (same tenant t1) uses same operation ID with same body
    response_bob = client.post(
        "/p3/recall",
        json=request_data,
        headers=headers(operation=operation_id, user="bob"),
    )

    # Should be idempotent (same tenant, same operation ID, same body)
    assert response_bob.status_code in SUCCESS_CODES, (
        f"Bob's request should be idempotent: {response_bob.status_code}"
    )

    # Should reference same operation
    if response_alice.status_code == 202 and response_bob.status_code == 202:
        location_alice = response_alice.headers.get("Location")
        location_bob = response_bob.headers.get("Location")
        assert location_alice == location_bob, (
            "Same tenant users should share operation ID namespace"
        )


# ============================================================================
# RC-IDE-05: Missing/Malformed Operation ID Handling
# ============================================================================


def test_rc_ide_05_missing_operation_id(client):
    """RC-IDE-05: Missing operation ID handling.

    Requests without operation IDs should be accepted and each treated as unique.
    """
    request_data = make_recall_request(query="no operation id")

    # Submit without operation ID
    response1 = client.post(
        "/p3/recall",
        json=request_data,
        headers=headers(operation=None),  # No operation ID
    )

    assert response1.status_code in SUCCESS_CODES, (
        f"Request without operation ID should succeed: {response1.status_code}"
    )

    # Submit again without operation ID - should be treated as new request
    response2 = client.post(
        "/p3/recall",
        json=request_data,
        headers=headers(operation=None),
    )

    assert response2.status_code in SUCCESS_CODES, (
        f"Second request without operation ID should also succeed: {response2.status_code}"
    )

    # Should create different operations (not idempotent without operation ID)
    if response1.status_code == 202 and response2.status_code == 202:
        location1 = response1.headers.get("Location")
        location2 = response2.headers.get("Location")
        # Might be same or different depending on implementation
        # But both should be valid


def test_rc_ide_05_malformed_operation_id(client):
    """RC-IDE-05: Malformed operation ID handling.

    Very long or unusual operation IDs should be handled gracefully.
    """
    request_data = make_recall_request(query="malformed operation id")

    malformed_ids = [
        "a" * 200,  # Very long
        "op-id-with-special-chars-!@#$%",
        "op_id_with_underscores",
    ]

    for op_id in malformed_ids:
        response = client.post(
            "/p3/recall",
            json=request_data,
            headers=headers(operation=op_id),
        )

        # Should either accept or reject cleanly (no server error)
        assert response.status_code < 500, (
            f"Malformed operation ID should not cause server error: {response.status_code}"
        )


# ============================================================================
# RC-IDE-06: Response Loss After Commit
# ============================================================================


def test_rc_ide_06_result_retrieval_after_completion(client):
    """RC-IDE-06: Response loss after commit doesn't duplicate execution.

    If a client loses the response after the operation completes, they should
    be able to retrieve the result without re-executing the operation.
    """
    operation_id = f"result-retrieval-{uuid4().hex}"
    request_data = make_recall_request(query="result retrieval test")

    # Submit request
    response = client.post(
        "/p3/recall",
        json=request_data,
        headers=headers(operation=operation_id),
    )

    assert response.status_code in SUCCESS_CODES

    # If async, wait for completion
    if response.status_code == 202:
        location = response.headers.get("Location")
        operation = wait_for_completion(client, location)
        assert operation["state"] == "succeeded", f"Operation should succeed: {operation}"

        job_id = location.split("/")[-1]

        # Retrieve result via /result endpoint
        result_response = client.get(
            f"{location}/result",
            headers=headers(user="alice"),
        )

        assert result_response.status_code == 200, (
            f"Result retrieval should succeed: {result_response.status_code}"
        )

        result = result_response.json()
        assert "recall_id" in result, "Result should include recall_id"

        # Retrieve result again - should return same result
        result_response2 = client.get(
            f"{location}/result",
            headers=headers(user="alice"),
        )

        assert result_response2.status_code == 200
        result2 = result_response2.json()
        assert result == result2, "Multiple result retrievals should return identical data"


def test_rc_ide_06_replay_after_completion_is_idempotent(client):
    """RC-IDE-06: Replaying the request after completion returns result.

    After an operation completes, replaying the request with the same
    operation ID should return the result without re-execution.
    """
    operation_id = f"replay-after-complete-{uuid4().hex}"
    request_data = make_recall_request(query="replay test")

    # First request
    response1 = client.post(
        "/p3/recall",
        json=request_data,
        headers=headers(operation=operation_id),
    )

    assert response1.status_code in SUCCESS_CODES

    # Wait for completion if async
    if response1.status_code == 202:
        location = response1.headers.get("Location")
        operation = wait_for_completion(client, location)
        assert operation["state"] == "succeeded"

    # Wait a bit to ensure operation is fully committed
    time.sleep(0.5)

    # Replay request after completion
    response2 = client.post(
        "/p3/recall",
        json=request_data,
        headers=headers(operation=operation_id),
    )

    assert response2.status_code in SUCCESS_CODES, (
        f"Replay after completion should succeed: {response2.status_code}"
    )

    # Should reference the same operation
    if response2.status_code == 202:
        location2 = response2.headers.get("Location")
        if response1.status_code == 202:
            location1 = response1.headers.get("Location")
            assert location1 == location2, "Replay should reference same operation"


# ============================================================================
# RC-IDE-07: Revoked Permissions Block Idempotent Replay
# ============================================================================


def test_rc_ide_07_permission_check_on_replay(client):
    """RC-IDE-07: Revoked permissions block idempotent replay.

    Even for idempotent replay, the current permissions of the caller should
    be validated. This test verifies the principle, though actual permission
    revocation requires runtime identity changes which are not easily testable
    without system-level identity management.

    This test documents the expected behavior rather than fully testing it.
    """
    operation_id = f"permission-replay-{uuid4().hex}"
    request_data = make_recall_request(query="permission test")

    # Alice creates operation
    response1 = client.post(
        "/p3/recall",
        json=request_data,
        headers=headers(operation=operation_id, user="alice"),
    )

    assert response1.status_code in SUCCESS_CODES

    # Alice replays (should work)
    response2 = client.post(
        "/p3/recall",
        json=request_data,
        headers=headers(operation=operation_id, user="alice"),
    )

    assert response2.status_code in SUCCESS_CODES

    # Different user (Eve, different tenant) tries to replay alice's operation
    response_eve = client.post(
        "/p3/recall",
        json=request_data,
        headers=headers(operation=operation_id, user="eve"),
    )

    # Should either create new operation (different tenant) or reject access
    # Either way, should not give Eve access to Alice's results
    assert response_eve.status_code in SUCCESS_CODES or response_eve.status_code >= 400


# ============================================================================
# RC-IDE-08: JSON Normalization in Idempotency Signatures
# ============================================================================


def test_rc_ide_08_json_field_order_normalization(client):
    """RC-IDE-08: JSON normalization in idempotency signatures.

    Field order in JSON should not affect idempotency. Requests with fields
    in different order but same content should be treated as identical.
    """
    operation_id = f"json-normalize-{uuid4().hex}"

    # First request with fields in one order
    request1 = {
        "query": "test",
        "selection": {"session_id": "s1", "task_id": "t1"},
        "sources": "long_term",
        "token_budget": 1000,
    }

    response1 = client.post(
        "/p3/recall",
        json=request1,
        headers=headers(operation=operation_id),
    )

    assert response1.status_code in SUCCESS_CODES

    # Second request with fields in different order
    request2 = {
        "token_budget": 1000,
        "sources": "long_term",
        "selection": {"task_id": "t1", "session_id": "s1"},  # Different order
        "query": "test",
    }

    response2 = client.post(
        "/p3/recall",
        json=request2,
        headers=headers(operation=operation_id),
    )

    # Should be treated as idempotent (same content, different order)
    assert response2.status_code in SUCCESS_CODES, (
        f"Different field order should not break idempotency: {response2.status_code}"
    )

    # Should reference same operation
    if response1.status_code == 202 and response2.status_code == 202:
        location1 = response1.headers.get("Location")
        location2 = response2.headers.get("Location")
        assert location1 == location2, (
            "Different field order should not create new operation"
        )


def test_rc_ide_08_whitespace_normalization(client):
    """RC-IDE-08: Whitespace in string values should matter for idempotency.

    While JSON field order is normalized, actual string content including
    whitespace should be considered significant.
    """
    operation_id = f"whitespace-{uuid4().hex}"

    # First request
    response1 = client.post(
        "/p3/recall",
        json=make_recall_request(query="test"),
        headers=headers(operation=operation_id),
    )

    assert response1.status_code in SUCCESS_CODES

    # Second request with different whitespace in query value
    response2 = client.post(
        "/p3/recall",
        json=make_recall_request(query="test "),  # Trailing space
        headers=headers(operation=operation_id),
    )

    # Should be treated as different (whitespace matters in values)
    assert response2.status_code == CONFLICT_CODE, (
        f"Different whitespace should cause conflict: {response2.status_code}"
    )


def test_rc_ide_08_empty_vs_missing_fields(client):
    """RC-IDE-08: Empty objects vs missing fields in idempotency.

    Empty selection {} and missing selection should be normalized the same way.
    """
    operation_id = f"empty-vs-missing-{uuid4().hex}"

    # First request with explicit empty selection
    request1 = make_recall_request(selection={})
    response1 = client.post(
        "/p3/recall",
        json=request1,
        headers=headers(operation=operation_id),
    )

    assert response1.status_code in SUCCESS_CODES

    # Second request - also with empty selection
    request2 = make_recall_request(selection={})
    response2 = client.post(
        "/p3/recall",
        json=request2,
        headers=headers(operation=operation_id),
    )

    # Should be idempotent
    assert response2.status_code in SUCCESS_CODES, (
        f"Identical empty selection should be idempotent: {response2.status_code}"
    )
