"""AET-82: RC-IDE-01 & RC-IDE-02: Basic Serial Idempotency.

RC-IDE-01: Same operation ID + same request body returns original result without re-execution
RC-IDE-02: Same operation ID + different request body/query/selection/sources/budget returns conflict error
"""

import pytest
from tests.unit.recall.helpers import Authority, policy, recall_input, scope

from aether_agent_memory.recall.admission import (
    RecallAdmissionService,
    RecallError,
    RecallFilters,
)
from azure_test_runtime import AzureRecords


@pytest.mark.p0
async def test_rc_ide_01_same_operation_id_same_request_returns_cached_result(tmp_path):
    """RC-IDE-01: Serial retry with same operation ID returns cached result without re-execution."""
    store = AzureRecords(tmp_path / "state.db")
    auth = Authority()
    svc = RecallAdmissionService(store, auth, policy())

    # First request with operation ID
    raw = recall_input(idempotency_key="operation-001")
    first = await svc.admit(raw)

    assert first.request.idempotency_key == "operation-001"
    assert first.execution.recall_id is not None
    first_recall_id = first.execution.recall_id
    first_request_ref = first.request.request_ref

    # Second request with same operation ID and identical request
    second = await svc.admit(raw)

    # Verify returns same recall_id (cached result)
    assert second.execution.recall_id == first_recall_id
    assert second.request.request_ref == first_request_ref
    assert second.request.idempotency_key == "operation-001"

    # Verify no duplicate Pack events generated (same execution returned)
    assert second.execution.recall_id == first.execution.recall_id

    # Verify authorization is still checked on retry
    assert auth.calls >= 2  # Should check auth on both requests

    store.close()


@pytest.mark.p0
async def test_rc_ide_02_same_operation_id_different_query_returns_conflict(tmp_path):
    """RC-IDE-02: Same operation ID with different query returns conflict error."""
    store = AzureRecords(tmp_path / "state.db")
    auth = Authority()
    svc = RecallAdmissionService(store, auth, policy())

    # First request with operation ID
    raw = recall_input(idempotency_key="operation-002", query="用户的咖啡加糖习惯是什么？")
    first = await svc.admit(raw)
    assert first.request.idempotency_key == "operation-002"

    # Second request with same operation ID but different query
    with pytest.raises(RecallError, match="IDEMPOTENCY_CONFLICT"):
        await svc.admit(
            recall_input(idempotency_key="operation-002", query="用户喜欢喝什么茶？")
        )

    store.close()


@pytest.mark.p0
async def test_rc_ide_02_same_operation_id_different_scope_returns_conflict(tmp_path):
    """RC-IDE-02: Same operation ID with different selection (scope) returns conflict error."""
    store = AzureRecords(tmp_path / "state.db")
    auth = Authority()
    svc = RecallAdmissionService(store, auth, policy())

    # First request with operation ID and specific session
    raw = recall_input(
        idempotency_key="operation-003",
        scope=scope(session_id="session-1")
    )
    first = await svc.admit(raw)
    assert first.request.idempotency_key == "operation-003"

    # Second request with same operation ID but different session
    with pytest.raises(RecallError, match="IDEMPOTENCY_CONFLICT"):
        await svc.admit(
            recall_input(
                idempotency_key="operation-003",
                scope=scope(session_id="session-2")
            )
        )

    store.close()


@pytest.mark.p0
async def test_rc_ide_02_same_operation_id_different_sources_returns_conflict(tmp_path):
    """RC-IDE-02: Same operation ID with different sources returns conflict error."""
    store = AzureRecords(tmp_path / "state.db")
    auth = Authority()
    svc = RecallAdmissionService(store, auth, policy())

    # First request with operation ID requesting working memory
    raw = recall_input(
        idempotency_key="operation-004",
        retrieval_constraints=RecallFilters(memory_types=["Working"])
    )
    first = await svc.admit(raw)
    assert first.request.idempotency_key == "operation-004"

    # Second request with same operation ID but requesting semantic memory
    with pytest.raises(RecallError, match="IDEMPOTENCY_CONFLICT"):
        await svc.admit(
            recall_input(
                idempotency_key="operation-004",
                retrieval_constraints=RecallFilters(memory_types=["Semantic"])
            )
        )

    store.close()


@pytest.mark.p0
async def test_rc_ide_02_same_operation_id_different_budget_returns_conflict(tmp_path):
    """RC-IDE-02: Same operation ID with different token budget returns conflict error."""
    store = AzureRecords(tmp_path / "state.db")
    auth = Authority()
    svc = RecallAdmissionService(store, auth, policy())

    # First request with operation ID and 128 token budget
    raw = recall_input(idempotency_key="operation-005", token_budget=128)
    first = await svc.admit(raw)
    assert first.request.idempotency_key == "operation-005"

    # Second request with same operation ID but different budget
    with pytest.raises(RecallError, match="IDEMPOTENCY_CONFLICT"):
        await svc.admit(
            recall_input(idempotency_key="operation-005", token_budget=256)
        )

    store.close()


@pytest.mark.p0
async def test_rc_ide_01_idempotent_retry_checks_current_authorization(tmp_path):
    """RC-IDE-01: Idempotent retry still checks current authorization."""
    store = AzureRecords(tmp_path / "state.db")
    auth = Authority()
    svc = RecallAdmissionService(store, auth, policy())

    # First request
    raw = recall_input(idempotency_key="operation-006")
    first = await svc.admit(raw)
    first_recall_id = first.execution.recall_id
    initial_auth_calls = auth.calls

    # Second request (idempotent retry)
    second = await svc.admit(raw)

    # Same result returned
    assert second.execution.recall_id == first_recall_id

    # But authorization was checked again
    assert auth.calls > initial_auth_calls

    store.close()
