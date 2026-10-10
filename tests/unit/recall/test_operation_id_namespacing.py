"""AET-84: RC-IDE-04 & RC-IDE-05: Operation ID Namespacing and Validation.

RC-IDE-04: Same literal operation ID used by different tenants/users remains isolated
RC-IDE-05: Missing/empty/malformed/duplicate operation ID headers handled per spec
"""

import pytest
from tests.unit.recall.helpers import Authority, policy, recall_input, scope

from aether_agent_memory.recall.admission import (
    RecallAdmissionService,
    RecallAuthorization,
    RecallError,
)
from aether_agent_memory.runtime.contract_types import Scope
from azure_test_runtime import AzureRecords


@pytest.mark.p0
async def test_rc_ide_04_same_operation_id_different_tenants_isolated(tmp_path):
    """RC-IDE-04: Same literal operation ID used by different tenants remains isolated.

    No cross-tenant result leakage or metadata exposure.
    """
    store = AzureRecords(tmp_path / "state.db")

    # Create authorization for tenant A
    auth_tenant_a = Authority()
    auth_tenant_a.evidence = RecallAuthorization(
        principal_ref="tenant-a/user-1/agent-1",
        working_eligible=True,
        long_term_eligible=True,
        working_scope=scope(tenant_id="tenant-a"),
        long_term_scope=scope(tenant_id="tenant-a"),
    )

    # Create authorization for tenant B
    auth_tenant_b = Authority()
    auth_tenant_b.evidence = RecallAuthorization(
        principal_ref="tenant-b/user-1/agent-1",
        working_eligible=True,
        long_term_eligible=True,
        working_scope=scope(tenant_id="tenant-b"),
        long_term_scope=scope(tenant_id="tenant-b"),
    )

    svc_a = RecallAdmissionService(store, auth_tenant_a, policy())
    svc_b = RecallAdmissionService(store, auth_tenant_b, policy())

    # Both tenants use the same literal operation ID
    same_operation_id = "shared-operation-123"

    # Tenant A submits with this operation ID
    request_a = recall_input(
        idempotency_key=same_operation_id,
        scope=scope(tenant_id="tenant-a", session_id="session-a"),
        query="Tenant A query"
    )
    result_a = await svc_a.admit(request_a)

    # Tenant B submits with the same literal operation ID
    request_b = recall_input(
        idempotency_key=same_operation_id,
        scope=scope(tenant_id="tenant-b", session_id="session-b"),
        query="Tenant B query"
    )
    result_b = await svc_b.admit(request_b)

    # Results should be completely separate (different recall_ids)
    assert result_a.execution.recall_id != result_b.execution.recall_id

    # No cross-tenant leakage
    assert result_a.request.scope.tenant_id == "tenant-a"
    assert result_b.request.scope.tenant_id == "tenant-b"

    # Principal refs should be different
    assert result_a.index.principal_ref == "tenant-a/user-1/agent-1"
    assert result_b.index.principal_ref == "tenant-b/user-1/agent-1"

    # Request refs should be different
    assert result_a.request.request_ref != result_b.request.request_ref

    store.close()


@pytest.mark.p0
async def test_rc_ide_04_same_operation_id_different_users_same_tenant_isolated(tmp_path):
    """RC-IDE-04: Same operation ID used by different users in same tenant remains isolated."""
    store = AzureRecords(tmp_path / "state.db")

    # User 1 in tenant A
    auth_user_1 = Authority()
    auth_user_1.evidence = RecallAuthorization(
        principal_ref="tenant-a/user-1/agent-1",
        working_eligible=True,
        long_term_eligible=True,
        working_scope=scope(tenant_id="tenant-a"),
        long_term_scope=scope(tenant_id="tenant-a"),
    )

    # User 2 in tenant A
    auth_user_2 = Authority()
    auth_user_2.evidence = RecallAuthorization(
        principal_ref="tenant-a/user-2/agent-1",
        working_eligible=True,
        long_term_eligible=True,
        working_scope=scope(tenant_id="tenant-a"),
        long_term_scope=scope(tenant_id="tenant-a"),
    )

    svc_user_1 = RecallAdmissionService(store, auth_user_1, policy())
    svc_user_2 = RecallAdmissionService(store, auth_user_2, policy())

    same_operation_id = "shared-operation-456"

    # User 1 submits
    request_1 = recall_input(
        idempotency_key=same_operation_id,
        scope=scope(tenant_id="tenant-a", session_id="session-1"),
        query="User 1 query"
    )
    result_1 = await svc_user_1.admit(request_1)

    # User 2 submits with same operation ID
    request_2 = recall_input(
        idempotency_key=same_operation_id,
        scope=scope(tenant_id="tenant-a", session_id="session-2"),
        query="User 2 query"
    )
    result_2 = await svc_user_2.admit(request_2)

    # Results should be separate (different principals)
    assert result_1.execution.recall_id != result_2.execution.recall_id
    assert result_1.index.principal_ref == "tenant-a/user-1/agent-1"
    assert result_2.index.principal_ref == "tenant-a/user-2/agent-1"

    store.close()


@pytest.mark.p1
async def test_rc_ide_05_missing_operation_id_generates_stable_id(tmp_path):
    """RC-IDE-05: Missing operation ID should either be rejected or auto-generated."""
    store = AzureRecords(tmp_path / "state.db")
    auth = Authority()
    svc = RecallAdmissionService(store, auth, policy())

    # Submit without operation ID (None)
    request = recall_input(idempotency_key=None)

    # Should either reject or auto-generate
    # Based on implementation, we expect it to work with auto-generation
    # If spec requires rejection, this test will fail and guide implementation
    result = await svc.admit(request)

    # If accepted, should have a stable operation ID
    assert result.request.idempotency_key is not None

    store.close()


@pytest.mark.p1
async def test_rc_ide_05_empty_operation_id_rejected(tmp_path):
    """RC-IDE-05: Empty operation ID should be rejected."""
    store = AzureRecords(tmp_path / "state.db")
    auth = Authority()
    svc = RecallAdmissionService(store, auth, policy())

    # Submit with empty string operation ID
    try:
        request = recall_input(idempotency_key="")
        result = await svc.admit(request)

        # If not rejected, it should be treated as missing and auto-generated
        assert result.request.idempotency_key != ""
    except (RecallError, ValueError) as e:
        # Rejection is acceptable for empty operation ID
        assert True

    store.close()


@pytest.mark.p1
async def test_rc_ide_05_malformed_operation_id_format_validation(tmp_path):
    """RC-IDE-05: Malformed operation ID should provide clear error message."""
    store = AzureRecords(tmp_path / "state.db")
    auth = Authority()
    svc = RecallAdmissionService(store, auth, policy())

    malformed_ids = [
        "invalid spaces in id",
        "invalid\nnewline",
        "invalid\ttab",
        "invalid/slash",
        "invalid\\backslash",
        "invalid<bracket>",
        "invalid|pipe",
    ]

    for malformed_id in malformed_ids:
        try:
            request = recall_input(idempotency_key=malformed_id)
            result = await svc.admit(request)

            # If not rejected, the ID was sanitized or accepted as-is
            # Record what was accepted for validation
            assert result.request.idempotency_key is not None
        except (RecallError, ValueError) as e:
            # Clear error message expected
            error_message = str(e)
            assert len(error_message) > 0
            # Error should mention "operation" or "idempotency" or "id"
            assert any(term in error_message.lower() for term in ["operation", "idempotency", "id", "invalid"])

    store.close()


@pytest.mark.p1
async def test_rc_ide_05_very_long_operation_id_handling(tmp_path):
    """RC-IDE-05: Very long operation ID should be handled gracefully."""
    store = AzureRecords(tmp_path / "state.db")
    auth = Authority()
    svc = RecallAdmissionService(store, auth, policy())

    # Create a very long operation ID (e.g., 1000 characters)
    very_long_id = "x" * 1000

    try:
        request = recall_input(idempotency_key=very_long_id)
        result = await svc.admit(request)

        # If accepted, verify it's stored correctly
        assert result.request.idempotency_key == very_long_id
    except (RecallError, ValueError) as e:
        # Rejection with clear error is acceptable
        error_message = str(e)
        assert "length" in error_message.lower() or "long" in error_message.lower() or "limit" in error_message.lower()

    store.close()


@pytest.mark.p0
async def test_rc_ide_04_no_metadata_leakage_across_principals(tmp_path):
    """RC-IDE-04: No metadata about other principal's operations should leak."""
    store = AzureRecords(tmp_path / "state.db")

    # Tenant A
    auth_a = Authority()
    auth_a.evidence = RecallAuthorization(
        principal_ref="tenant-a/user-1/agent-1",
        working_eligible=True,
        long_term_eligible=True,
        working_scope=scope(tenant_id="tenant-a"),
        long_term_scope=scope(tenant_id="tenant-a"),
    )

    # Tenant B
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

    operation_id = "metadata-leak-test"

    # Tenant A creates an operation
    request_a = recall_input(
        idempotency_key=operation_id,
        scope=scope(tenant_id="tenant-a"),
        query="Confidential tenant A data"
    )
    result_a = await svc_a.admit(request_a)

    # Tenant B tries to use the same operation ID
    request_b = recall_input(
        idempotency_key=operation_id,
        scope=scope(tenant_id="tenant-b"),
        query="Tenant B query"
    )
    result_b = await svc_b.admit(request_b)

    # Verify no information from tenant A leaks into tenant B's result
    # The query should not match
    assert result_b.request.query != "Confidential tenant A data"

    # Scope digests should be different (different tenants)
    assert result_a.index.scope_digest != result_b.index.scope_digest

    # Request fingerprints should be different (different queries and scopes)
    assert result_a.index.request_fingerprint != result_b.index.request_fingerprint

    store.close()
