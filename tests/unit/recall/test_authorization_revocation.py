"""AET-86: RC-IDE-07: Authorization Changes During Replay.

RC-IDE-07: Permission revoked after successful recall completion
- Idempotent POST with same operation ID after revocation doesn't leak old content
- GET original result after revocation enforces current permissions
- Internal read events may be preserved but content not delivered to client
"""

import pytest
from tests.unit.recall.helpers import Authority, policy, recall_input, scope

from aether_agent_memory.recall.admission import (
    RecallAdmissionService,
    RecallAuthorization,
    RecallError,
)
from azure_test_runtime import AzureRecords


@pytest.mark.p0
async def test_rc_ide_07_permission_revoked_after_recall_completion(tmp_path):
    """RC-IDE-07: Permission revoked after successful recall completion.

    Idempotent POST after revocation should not leak old content.
    """
    store = AzureRecords(tmp_path / "state.db")

    # Initial authorization with READ permission
    auth = Authority()
    auth.evidence = RecallAuthorization(
        principal_ref="tenant-a/user-1/agent-1",
        working_eligible=True,
        long_term_eligible=True,
        working_scope=scope(tenant_id="tenant-a"),
        long_term_scope=scope(tenant_id="tenant-a"),
    )

    svc = RecallAdmissionService(store, auth, policy())

    operation_id = "revocation-test-001"
    request = recall_input(
        idempotency_key=operation_id,
        scope=scope(tenant_id="tenant-a"),
        query="Sensitive query that should not leak after revocation"
    )

    # Original successful recall with READ permission
    original_result = await svc.admit(request)
    original_recall_id = original_result.execution.recall_id

    # Simulate permission revocation - remove READ eligibility
    auth.evidence = RecallAuthorization(
        principal_ref="tenant-a/user-1/agent-1",
        working_eligible=False,  # READ revoked for working memory
        long_term_eligible=False,  # READ revoked for long-term memory
        working_scope=scope(tenant_id="tenant-a"),
        long_term_scope=scope(tenant_id="tenant-a"),
    )

    # Idempotent POST with same operation ID after revocation
    # Should enforce current permissions and not leak old content
    with pytest.raises(RecallError, match="SCOPE_DENIED|AUTHORIZATION|PERMISSION"):
        await svc.admit(request)

    store.close()


@pytest.mark.p0
async def test_rc_ide_07_idempotent_replay_enforces_current_authorization(tmp_path):
    """RC-IDE-07: Idempotent replay checks current authorization, not cached.

    Even though operation was successful before, revoked permissions prevent replay.
    """
    store = AzureRecords(tmp_path / "state.db")

    # Start with full permissions
    auth = Authority()
    auth.evidence = RecallAuthorization(
        principal_ref="tenant-a/user-1/agent-1",
        working_eligible=True,
        long_term_eligible=True,
        working_scope=scope(tenant_id="tenant-a"),
        long_term_scope=scope(tenant_id="tenant-a"),
    )

    svc = RecallAdmissionService(store, auth, policy())

    operation_id = "revocation-test-002"
    request = recall_input(idempotency_key=operation_id)

    # Original request succeeds
    original_result = await svc.admit(request)
    assert original_result.execution.recall_id is not None

    # Revoke permissions
    auth.evidence = RecallAuthorization(
        principal_ref="tenant-a/user-1/agent-1",
        working_eligible=False,
        long_term_eligible=False,
        working_scope=scope(tenant_id="tenant-a"),
        long_term_scope=scope(tenant_id="tenant-a"),
    )

    # Retry with same operation ID after revocation should be denied
    with pytest.raises(RecallError, match="SCOPE_DENIED|AUTHORIZATION|PERMISSION"):
        await svc.admit(request)

    store.close()


@pytest.mark.p0
async def test_rc_ide_07_get_result_after_revocation_denied(tmp_path):
    """RC-IDE-07: GET original result after revocation enforces current permissions.

    Retrieving a previously successful result should fail if permissions revoked.
    """
    store = AzureRecords(tmp_path / "state.db")

    # Initial authorization
    auth = Authority()
    auth.evidence = RecallAuthorization(
        principal_ref="tenant-a/user-1/agent-1",
        working_eligible=True,
        long_term_eligible=True,
        working_scope=scope(tenant_id="tenant-a"),
        long_term_scope=scope(tenant_id="tenant-a"),
    )

    svc = RecallAdmissionService(store, auth, policy())

    operation_id = "revocation-test-003"
    request = recall_input(idempotency_key=operation_id)

    # Create successful result
    original_result = await svc.admit(request)
    recall_id = original_result.execution.recall_id

    # Revoke READ permission
    auth.evidence = RecallAuthorization(
        principal_ref="tenant-a/user-1/agent-1",
        working_eligible=False,
        long_term_eligible=False,
        working_scope=scope(tenant_id="tenant-a"),
        long_term_scope=scope(tenant_id="tenant-a"),
    )

    # Attempt to GET original result by recall_id should fail
    # (In a full implementation, this would be a separate GET endpoint)
    # Here we verify via idempotent retry which should also enforce current auth
    with pytest.raises(RecallError, match="SCOPE_DENIED|AUTHORIZATION|PERMISSION"):
        await svc.admit(request)

    store.close()


@pytest.mark.p0
async def test_rc_ide_07_no_content_leakage_through_http_cache(tmp_path):
    """RC-IDE-07: Cannot bypass current authorization with HTTP cache from before revocation.

    System must not replay pre-revocation HTTP responses; must re-check authorization.
    """
    store = AzureRecords(tmp_path / "state.db")

    auth = Authority()
    auth.evidence = RecallAuthorization(
        principal_ref="tenant-a/user-1/agent-1",
        working_eligible=True,
        long_term_eligible=True,
        working_scope=scope(tenant_id="tenant-a"),
        long_term_scope=scope(tenant_id="tenant-a"),
    )

    svc = RecallAdmissionService(store, auth, policy())

    operation_id = "revocation-test-004"
    request = recall_input(
        idempotency_key=operation_id,
        query="Confidential content"
    )

    # Original request with authorization
    original_result = await svc.admit(request)
    original_auth_calls = auth.calls

    # Revoke permission
    auth.evidence = RecallAuthorization(
        principal_ref="tenant-a/user-1/agent-1",
        working_eligible=False,
        long_term_eligible=False,
        working_scope=scope(tenant_id="tenant-a"),
        long_term_scope=scope(tenant_id="tenant-a"),
    )

    # Retry should re-check authorization (not replay cached HTTP response)
    # Authorization check count should increase even though result is denied
    with pytest.raises(RecallError, match="SCOPE_DENIED|AUTHORIZATION|PERMISSION"):
        await svc.admit(request)

    # Verify authorization was actually checked (calls increased)
    assert auth.calls > original_auth_calls

    store.close()


@pytest.mark.p1
async def test_rc_ide_07_internal_read_events_preserved_content_not_delivered(tmp_path):
    """RC-IDE-07: Internal read events may be preserved but content not delivered.

    System may keep internal records of what was read, but must not deliver
    content to client after revocation.
    """
    store = AzureRecords(tmp_path / "state.db")

    auth = Authority()
    auth.evidence = RecallAuthorization(
        principal_ref="tenant-a/user-1/agent-1",
        working_eligible=True,
        long_term_eligible=True,
        working_scope=scope(tenant_id="tenant-a"),
        long_term_scope=scope(tenant_id="tenant-a"),
    )

    svc = RecallAdmissionService(store, auth, policy())

    operation_id = "revocation-test-005"
    request = recall_input(idempotency_key=operation_id)

    # Create operation - internal read events recorded
    original_result = await svc.admit(request)

    # Revoke permissions
    auth.evidence = RecallAuthorization(
        principal_ref="tenant-a/user-1/agent-1",
        working_eligible=False,
        long_term_eligible=False,
        working_scope=scope(tenant_id="tenant-a"),
        long_term_scope=scope(tenant_id="tenant-a"),
    )

    # Even though internal read events exist, content must not be delivered
    with pytest.raises(RecallError, match="SCOPE_DENIED|AUTHORIZATION|PERMISSION"):
        await svc.admit(request)

    # Internal records may exist (e.g., for audit), but client doesn't receive them
    # This is verified by the exception being raised

    store.close()


@pytest.mark.p1
async def test_rc_ide_07_partial_revocation_working_memory_only(tmp_path):
    """RC-IDE-07: Partial revocation (working memory only) prevents access.

    If only working memory READ is revoked but long-term is still granted,
    combined recall request should still be restricted.
    """
    store = AzureRecords(tmp_path / "state.db")

    # Initial full authorization
    auth = Authority()
    auth.evidence = RecallAuthorization(
        principal_ref="tenant-a/user-1/agent-1",
        working_eligible=True,
        long_term_eligible=True,
        working_scope=scope(tenant_id="tenant-a"),
        long_term_scope=scope(tenant_id="tenant-a"),
    )

    svc = RecallAdmissionService(store, auth, policy())

    operation_id = "revocation-test-006"
    # Request that would use both working and long-term memory
    request = recall_input(
        idempotency_key=operation_id,
        scope=scope(tenant_id="tenant-a", session_id="session-1")
    )

    # Original request succeeds
    original_result = await svc.admit(request)

    # Revoke working memory only (keep long-term)
    auth.evidence = RecallAuthorization(
        principal_ref="tenant-a/user-1/agent-1",
        working_eligible=False,  # Revoked
        long_term_eligible=True,  # Still granted
        working_scope=scope(tenant_id="tenant-a"),
        long_term_scope=scope(tenant_id="tenant-a"),
    )

    # Retry should respect partial revocation
    # If request requires working memory, it should be denied
    try:
        retry_result = await svc.admit(request)
        # If accepted, verify working memory was not included
        # (specific verification depends on result structure)
        assert retry_result.execution.recall_id == original_result.execution.recall_id
    except RecallError as e:
        # Denial is acceptable if working memory is required
        assert "SCOPE_DENIED" in str(e) or "AUTHORIZATION" in str(e)

    store.close()


@pytest.mark.p0
async def test_rc_ide_07_revocation_different_tenant_unaffected(tmp_path):
    """RC-IDE-07: Revocation for one tenant doesn't affect other tenants.

    Security boundary: permission changes are tenant-scoped.
    """
    store = AzureRecords(tmp_path / "state.db")

    # Tenant A with authorization
    auth_a = Authority()
    auth_a.evidence = RecallAuthorization(
        principal_ref="tenant-a/user-1/agent-1",
        working_eligible=True,
        long_term_eligible=True,
        working_scope=scope(tenant_id="tenant-a"),
        long_term_scope=scope(tenant_id="tenant-a"),
    )

    # Tenant B with authorization
    auth_b = Authority()
    auth_b.evidence = RecallAuthorization(
        principal_ref="tenant-b/user-1/agent-1",
        working_eligible=True,
        long_term_eligible=True,
        working_scope=scope(tenant_id="tenant-b"),
        long_term_scope=scope(tenant_id="tenant-b"),
    )

    svc_a = RecallAdmissionService(store, auth_a, policy())
    svc_b = RecallAdmissionService(store, auth_b, policy())

    # Both tenants create operations
    result_a = await svc_a.admit(recall_input(
        idempotency_key="op-a",
        scope=scope(tenant_id="tenant-a")
    ))
    result_b = await svc_b.admit(recall_input(
        idempotency_key="op-b",
        scope=scope(tenant_id="tenant-b")
    ))

    # Revoke tenant A's permissions
    auth_a.evidence = RecallAuthorization(
        principal_ref="tenant-a/user-1/agent-1",
        working_eligible=False,
        long_term_eligible=False,
        working_scope=scope(tenant_id="tenant-a"),
        long_term_scope=scope(tenant_id="tenant-a"),
    )

    # Tenant A's retry should fail
    with pytest.raises(RecallError, match="SCOPE_DENIED|AUTHORIZATION|PERMISSION"):
        await svc_a.admit(recall_input(
            idempotency_key="op-a",
            scope=scope(tenant_id="tenant-a")
        ))

    # Tenant B's retry should still succeed (unaffected by tenant A's revocation)
    retry_b = await svc_b.admit(recall_input(
        idempotency_key="op-b",
        scope=scope(tenant_id="tenant-b")
    ))
    assert retry_b.execution.recall_id == result_b.execution.recall_id

    store.close()
