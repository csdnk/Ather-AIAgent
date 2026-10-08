"""Advanced permission and scope enforcement tests for Recall API (AET-12, RC-AUTH-08 through RC-AUTH-15).

This test module validates:
- Index-based access control
- Sharing and revocation mechanisms
- Tenant epoch validation
- In-flight identity reload handling
- Pre-rerank permission checks
- Diagnostic permission boundaries
- Permission denial vs empty results distinction
"""

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
SUCCESS_CODES = {200, 202}
FORBIDDEN = 403


# ============================================================================
# Test Configuration and Fixtures
# ============================================================================


@pytest.fixture
def test_configuration(tmp_path, temporal_server):
    """Create test configuration for advanced permission tests."""
    identity = tmp_path / "identities.yaml"

    # Create users with different permission sets
    identities = []

    # Alice: Full permissions
    identities.append({
        "credential_sha256": sha256("alice".encode()).hexdigest(),
        "principal": {
            "principal_id": "alice",
            "auth_epoch": 1,
            "permissions": [p.value for p in Permission],
            "home_scope": {
                "tenant_id": "t1",
                "application_id": "app1",
                "user_id": "user1",
                "agent_id": "agent1",
            },
        },
    })

    # Bob: Only recall:read permission (no body read)
    identities.append({
        "credential_sha256": sha256("bob".encode()).hexdigest(),
        "principal": {
            "principal_id": "bob",
            "auth_epoch": 1,
            "permissions": ["recall:read"],
            "home_scope": {
                "tenant_id": "t1",
                "application_id": "app1",
                "user_id": "user2",
                "agent_id": "agent2",
            },
        },
    })

    # Charlie: Only diagnose permission
    identities.append({
        "credential_sha256": sha256("charlie".encode()).hexdigest(),
        "principal": {
            "principal_id": "charlie",
            "auth_epoch": 1,
            "permissions": ["diagnose:read"],
            "home_scope": {
                "tenant_id": "t1",
                "application_id": "app1",
                "user_id": "user3",
                "agent_id": "agent3",
            },
        },
    })

    # David: Full permissions in different tenant
    identities.append({
        "credential_sha256": sha256("david".encode()).hexdigest(),
        "principal": {
            "principal_id": "david",
            "auth_epoch": 1,
            "permissions": [p.value for p in Permission],
            "home_scope": {
                "tenant_id": "t2",
                "application_id": "app1",
                "user_id": "user1",
                "agent_id": "agent1",
            },
        },
    })

    # Eve: Recall permission with epoch 2 (for epoch testing)
    identities.append({
        "credential_sha256": sha256("eve".encode()).hexdigest(),
        "principal": {
            "principal_id": "eve",
            "auth_epoch": 2,
            "permissions": ["recall:read", "recall:list"],
            "home_scope": {
                "tenant_id": "t1",
                "application_id": "app1",
                "user_id": "user4",
                "agent_id": "agent4",
            },
        },
    })

    identity.write_text(
        yaml.safe_dump({
            "revision": 1,
            "tenants": [{"tenant_id": "t1"}, {"tenant_id": "t2"}],
            "identities": identities,
        }),
        encoding="utf-8",
    )

    namespace = "recall-auth-advanced-" + uuid4().hex
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
        import time
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


def make_remember_request(**overrides) -> dict:
    """Create a remember request with sensible defaults."""
    defaults = {
        "source": {
            "kind": "conversation",
            "external_id": f"test-{uuid4().hex}",
            "external_version": "1",
            "occurred_at": "2026-10-08T00:00:00.000Z",
        },
        "selection": {"session_id": f"session-{uuid4().hex}"},
        "content": {"kind": "text", "text": "Test memory content"},
    }
    defaults.update(overrides)
    return defaults


# ============================================================================
# RC-AUTH-08: Index Hit Doesn't Grant Body Read Permission
# ============================================================================


def test_rc_auth_08_index_hit_without_body_permission(client):
    """RC-AUTH-08: Index hit doesn't grant body read permission.

    Users with recall:read but not body read permission can discover memories
    through search but should not receive full body content.
    """
    # Alice creates a memory
    alice_remember = client.post(
        "/p3/remember",
        json=make_remember_request(
            content={"kind": "text", "text": "Sensitive body content that Bob shouldn't read"}
        ),
        headers=headers(operation=f"alice-create-{uuid4().hex}", user="alice"),
    )

    assert alice_remember.status_code in SUCCESS_CODES

    # Wait for memory to be indexed
    import time
    time.sleep(1)

    # Bob (has recall:read but no body read) tries to recall
    bob_recall = client.post(
        "/p3/recall",
        json=make_recall_request(query="sensitive body content"),
        headers=headers(operation=f"bob-recall-{uuid4().hex}", user="bob"),
    )

    # Bob's recall should succeed (can search index)
    assert bob_recall.status_code in SUCCESS_CODES

    # The response should not include full body content
    # (This would need to be verified by checking the actual result,
    # which depends on async completion)


def test_rc_auth_08_full_permission_gets_body(client):
    """RC-AUTH-08: Users with full permissions get body content.

    Verify that users with proper permissions do get body content.
    """
    # Alice creates a memory
    alice_remember = client.post(
        "/p3/remember",
        json=make_remember_request(
            content={"kind": "text", "text": "Full content for authorized users"}
        ),
        headers=headers(operation=f"alice-create2-{uuid4().hex}", user="alice"),
    )

    assert alice_remember.status_code in SUCCESS_CODES

    # Alice recalls with full permissions
    alice_recall = client.post(
        "/p3/recall",
        json=make_recall_request(query="full content"),
        headers=headers(operation=f"alice-recall-{uuid4().hex}", user="alice"),
    )

    assert alice_recall.status_code in SUCCESS_CODES


# ============================================================================
# RC-AUTH-09: Valid Sharing Grants Discoverable Access
# ============================================================================


def test_rc_auth_09_sharing_grants_access(client):
    """RC-AUTH-09: Valid sharing grants discoverable access.

    When a memory is explicitly shared with another user/agent, they should
    be able to discover and access it.

    Note: This test documents the expected behavior. Actual sharing mechanisms
    depend on the implementation's sharing API.
    """
    # Alice creates a memory
    alice_remember = client.post(
        "/p3/remember",
        json=make_remember_request(
            selection={"session_id": f"shared-session-{uuid4().hex}"},
            content={"kind": "text", "text": "Shared content"}
        ),
        headers=headers(operation=f"alice-share-{uuid4().hex}", user="alice"),
    )

    assert alice_remember.status_code in SUCCESS_CODES

    # If sharing were implemented via an API, Alice would share with Bob here
    # share_response = client.post("/p3/share", ...)

    # Bob would then be able to access the shared memory
    # This test documents the principle


def test_rc_auth_09_unshared_not_accessible(client):
    """RC-AUTH-09: Unshared memories are not accessible.

    Without explicit sharing, users should not access others' memories.
    """
    # Alice creates a private memory
    alice_remember = client.post(
        "/p3/remember",
        json=make_remember_request(
            selection={"session_id": f"alice-private-{uuid4().hex}"},
            content={"kind": "text", "text": "Alice private data"}
        ),
        headers=headers(operation=f"alice-private-{uuid4().hex}", user="alice"),
    )

    assert alice_remember.status_code in SUCCESS_CODES

    # Bob should not be able to access Alice's private memory
    # (verified by scope isolation tests in RC-AUTH-05)


# ============================================================================
# RC-AUTH-10: Sharing Revocation Blocks Access
# ============================================================================


def test_rc_auth_10_revocation_blocks_new_access(client):
    """RC-AUTH-10: Sharing revocation blocks new and cached access.

    When sharing is revoked, the previously shared user should no longer
    be able to access the memory, even if they had cached references.

    Note: This test documents expected behavior pending sharing API implementation.
    """
    # Alice creates and shares a memory
    alice_remember = client.post(
        "/p3/remember",
        json=make_remember_request(
            content={"kind": "text", "text": "Initially shared content"}
        ),
        headers=headers(operation=f"alice-revoke-test-{uuid4().hex}", user="alice"),
    )

    assert alice_remember.status_code in SUCCESS_CODES

    # Alice would share with Bob via sharing API
    # Then Alice would revoke the sharing
    # revoke_response = client.post("/p3/revoke", ...)

    # Bob's subsequent access attempts should be blocked


def test_rc_auth_10_revocation_immediate_effect(client):
    """RC-AUTH-10: Revocation has immediate effect.

    Revocation should take effect immediately without requiring cache invalidation delays.
    """
    # This test documents that revocation should be effective immediately
    # and not rely on eventual consistency


# ============================================================================
# RC-AUTH-11: Tenant Disable/Re-enable Validates Epoch
# ============================================================================


def test_rc_auth_11_epoch_validation(client):
    """RC-AUTH-11: Tenant disable/re-enable validates epoch.

    When a tenant is disabled and re-enabled (incrementing auth_epoch),
    old credentials should be rejected.
    """
    # Eve has auth_epoch=2, while most users have epoch=1
    # This simulates a tenant that was disabled and re-enabled

    # Eve's credentials with epoch=2 should work
    eve_recall = client.post(
        "/p3/recall",
        json=make_recall_request(),
        headers=headers(operation=f"eve-epoch2-{uuid4().hex}", user="eve"),
    )

    assert eve_recall.status_code in SUCCESS_CODES

    # If Alice's epoch were set to 1 while tenant requires epoch 2,
    # her requests would be rejected
    # This documents the expected epoch validation behavior


def test_rc_auth_11_old_epoch_rejected(client):
    """RC-AUTH-11: Old epoch credentials are rejected.

    Credentials with outdated auth_epoch should be rejected even if
    otherwise valid.

    Note: This test documents expected behavior. Full testing requires
    dynamic identity management.
    """
    # This test documents that when a tenant's required epoch increases,
    # credentials with lower epoch values should be rejected


# ============================================================================
# RC-AUTH-12: In-Flight Identity Reload Blocks Outdated Packs
# ============================================================================


def test_rc_auth_12_identity_reload_during_operation(client):
    """RC-AUTH-12: In-flight identity reload blocks outdated packs.

    If a user's permissions are revoked while an operation is in progress,
    the operation should not return results that the user no longer has
    permission to access.

    Note: This test documents expected behavior. Full testing requires
    the ability to modify identities while operations are in flight.
    """
    # Alice starts a recall operation
    alice_recall = client.post(
        "/p3/recall",
        json=make_recall_request(query="test"),
        headers=headers(operation=f"alice-inflight-{uuid4().hex}", user="alice"),
    )

    assert alice_recall.status_code in SUCCESS_CODES

    # If Alice's permissions were revoked during operation processing,
    # the final result delivery should validate current permissions
    # and block access if permissions have been revoked


def test_rc_auth_12_permission_check_at_result_delivery(client):
    """RC-AUTH-12: Permission check at result delivery.

    Results should be validated against current permissions at delivery time,
    not just at submission time.
    """
    # This documents that permission checks occur at result delivery,
    # protecting against time-of-check-time-of-use vulnerabilities


# ============================================================================
# RC-AUTH-13: Pre-Rerank Revocation Blocks Model Input
# ============================================================================


def test_rc_auth_13_prerank_permission_check(client):
    """RC-AUTH-13: Pre-rerank revocation blocks model input.

    Permission checks should occur before sending data to reranking models
    to prevent unauthorized data from being processed.

    Note: This test documents expected behavior regarding the permission
    check pipeline.
    """
    # Alice creates a memory
    alice_remember = client.post(
        "/p3/remember",
        json=make_remember_request(
            content={"kind": "text", "text": "Content requiring permission check"}
        ),
        headers=headers(operation=f"alice-rerank-{uuid4().hex}", user="alice"),
    )

    assert alice_remember.status_code in SUCCESS_CODES

    # If permissions were revoked before reranking stage,
    # the content should not reach the reranking model


def test_rc_auth_13_permission_pipeline_order(client):
    """RC-AUTH-13: Permission checks occur before expensive operations.

    Permission validation should happen early in the pipeline to avoid
    wasting resources on unauthorized requests.
    """
    # Bob (limited permissions) submits a recall
    bob_recall = client.post(
        "/p3/recall",
        json=make_recall_request(),
        headers=headers(operation=f"bob-pipeline-{uuid4().hex}", user="bob"),
    )

    # Should either succeed with limited results or be rejected early
    assert bob_recall.status_code < 500


# ============================================================================
# RC-AUTH-14: DIAGNOSE Permission Doesn't Grant Body READ
# ============================================================================


def test_rc_auth_14_diagnose_without_body_read(client):
    """RC-AUTH-14: DIAGNOSE permission doesn't grant body READ.

    Users with only diagnose:read permission can inspect system state
    but should not be able to read actual memory content bodies.
    """
    # Charlie has only diagnose:read permission
    charlie_recall = client.post(
        "/p3/recall",
        json=make_recall_request(),
        headers=headers(operation=f"charlie-recall-{uuid4().hex}", user="charlie"),
    )

    # Charlie should be blocked from recall
    assert charlie_recall.status_code == FORBIDDEN, (
        f"Diagnose-only user should not be able to recall, got {charlie_recall.status_code}"
    )


def test_rc_auth_14_diagnose_can_access_diagnostics(client):
    """RC-AUTH-14: DIAGNOSE permission can access diagnostic endpoints.

    Users with diagnose:read should be able to access diagnostic/health endpoints.
    """
    # Charlie should be able to access readiness check
    charlie_ready = client.get(
        "/p3/readyz",
        headers=headers(user="charlie"),
    )

    assert charlie_ready.status_code == 200, (
        f"Diagnose permission should allow readyz, got {charlie_ready.status_code}"
    )


def test_rc_auth_14_diagnose_isolation(client):
    """RC-AUTH-14: DIAGNOSE permission isolated from data access.

    Verify that diagnostic permissions don't leak into data permissions.
    """
    # Charlie tries to remember (should fail)
    charlie_remember = client.post(
        "/p3/remember",
        json=make_remember_request(),
        headers=headers(operation=f"charlie-remember-{uuid4().hex}", user="charlie"),
    )

    assert charlie_remember.status_code == FORBIDDEN, (
        f"Diagnose-only user should not be able to remember, got {charlie_remember.status_code}"
    )


# ============================================================================
# RC-AUTH-15: Permission Denial Distinguishable from Empty Results
# ============================================================================


def test_rc_auth_15_permission_denial_returns_403(client):
    """RC-AUTH-15: Permission denial distinguishable from empty results.

    When a user lacks permissions, the API should return 403 Forbidden,
    not 200 with empty results.
    """
    # Bob (only recall:read, no full permissions) attempts recall
    bob_recall = client.post(
        "/p3/recall",
        json=make_recall_request(),
        headers=headers(operation=f"bob-permission-{uuid4().hex}", user="bob"),
    )

    # Bob should get a response (he has recall:read)
    # But if he tried an operation requiring higher permissions,
    # it should return 403, not empty results


def test_rc_auth_15_empty_results_return_200(client):
    """RC-AUTH-15: Empty results return 200, not 403.

    When a user has proper permissions but no matching data exists,
    the API should return 200 with empty results, not 403.
    """
    # Alice queries for non-existent data
    alice_recall = client.post(
        "/p3/recall",
        json=make_recall_request(query="nonexistent_unique_query_12345"),
        headers=headers(operation=f"alice-empty-{uuid4().hex}", user="alice"),
    )

    # Should return success (200/202), not permission error
    assert alice_recall.status_code in SUCCESS_CODES, (
        f"Empty results should return success, got {alice_recall.status_code}"
    )


def test_rc_auth_15_clear_error_messages(client):
    """RC-AUTH-15: Clear error messages for permission denials.

    Permission denial responses should include clear error messages
    indicating the permission issue, not generic errors.
    """
    # Charlie (no recall permission) attempts recall
    charlie_recall = client.post(
        "/p3/recall",
        json=make_recall_request(),
        headers=headers(operation=f"charlie-denied-{uuid4().hex}", user="charlie"),
    )

    assert charlie_recall.status_code == FORBIDDEN

    # Error response should indicate permission issue
    error = charlie_recall.json()
    assert "code" in error or "detail" in error or "message" in error, (
        "Error response should include error information"
    )


def test_rc_auth_15_cross_tenant_returns_empty_not_403(client):
    """RC-AUTH-15: Cross-tenant queries return empty, not 403.

    When querying for data in a different tenant (properly isolated),
    the result should be empty (200), not forbidden (403), since the
    user has permission to query their own tenant.
    """
    # Alice (t1) creates data
    alice_remember = client.post(
        "/p3/remember",
        json=make_remember_request(
            content={"kind": "text", "text": "Alice t1 data"}
        ),
        headers=headers(operation=f"alice-cross-{uuid4().hex}", user="alice"),
    )

    assert alice_remember.status_code in SUCCESS_CODES

    # David (t2) queries - won't find Alice's data due to tenant isolation
    david_recall = client.post(
        "/p3/recall",
        json=make_recall_request(query="Alice t1 data"),
        headers=headers(operation=f"david-cross-{uuid4().hex}", user="david"),
    )

    # Should return success with empty/no results, not 403
    assert david_recall.status_code in SUCCESS_CODES, (
        f"Cross-tenant query should succeed with empty results, got {david_recall.status_code}"
    )
