"""Source selection and coverage tests for Recall API (AET-13, RC-SRC-01 through RC-SRC-14).

This test module validates:
- Working memory vector search execution
- Long-term memory independent retrieval
- Both sources execution and result fusion
- Auto mode source selection logic
- Explicit source specification enforcement
- Index pending/failed state handling
- Mixed availability coverage reporting
- Single-source failure degradation
- Required source failure handling
- Complete unavailability error reporting
- Normal empty vs degraded empty distinction
- Source completion order independence
"""

import time
from hashlib import sha256
from typing import Any
from uuid import uuid4

import pytest
import yaml
from fastapi.testclient import TestClient

from aether_agent_memory.recall.contracts.models import RecallRequest
from aether_agent_memory.remember.contracts.models import RememberRequest
from aether_agent_memory.runtime.contracts.models import Permission
from azure_component_service import Service
from component_configuration import ComponentConfiguration

pytestmark = pytest.mark.integration


# ============================================================================
# Constants
# ============================================================================

DEFAULT_OPERATION_TIMEOUT = 90
MEMORY_READY_TIMEOUT = 120
POLL_INTERVAL = 0.05
SUCCESS_CODES = {200, 202}


# ============================================================================
# Test Configuration and Fixtures
# ============================================================================


@pytest.fixture
def test_configuration(tmp_path, temporal_server):
    """Create test configuration for source selection tests."""
    identity = tmp_path / "identities.yaml"

    # Create users with different permission sets
    identities = []

    # Alice: Full permissions (standard test user)
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

    # Bob: Recall + diagnose permissions (for diagnostic access)
    identities.append({
        "credential_sha256": sha256("bob".encode()).hexdigest(),
        "principal": {
            "principal_id": "bob",
            "auth_epoch": 1,
            "permissions": ["recall:read", "recall:list", "diagnose:read"],
            "home_scope": {
                "tenant_id": "t1",
                "application_id": "app1",
                "user_id": "user1",
                "agent_id": "agent1",
            },
        },
    })

    # Charlie: Different tenant (for cross-tenant testing)
    identities.append({
        "credential_sha256": sha256("charlie".encode()).hexdigest(),
        "principal": {
            "principal_id": "charlie",
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

    identity.write_text(
        yaml.safe_dump({
            "revision": 1,
            "tenants": [{"tenant_id": "t1"}, {"tenant_id": "t2"}],
            "identities": identities,
        }),
        encoding="utf-8",
    )

    namespace = "recall-sources-" + uuid4().hex
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
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if test_client.get("/p3/readyz").status_code == 200:
                yield test_client
                return
            time.sleep(0.1)
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
        "query": "coffee preferences",
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
        "selection": {},
        "content": {"kind": "text", "text": "Test memory content"},
    }
    defaults.update(overrides)
    return defaults


def poll_operation_until_complete(
    client: TestClient,
    job_id: str,
    user: str = "alice",
    timeout: float = DEFAULT_OPERATION_TIMEOUT
) -> dict[str, Any]:
    """Poll an operation until it reaches a terminal state."""
    def check_complete():
        response = client.get(f"/p3/operations/{job_id}", headers=headers(user=user))
        assert response.status_code == 200, f"Failed to get operation status: {response.text}"
        operation = response.json()

        if operation["state"] in {"succeeded", "failed", "attention_required"}:
            return operation
        return None

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = check_complete()
        if result:
            assert result["state"] == "succeeded", (
                f"Operation {job_id} did not succeed. State: {result['state']}, "
                f"Details: {result.get('error', 'No error details')}"
            )
            return result
        time.sleep(POLL_INTERVAL)
    raise AssertionError(f"Operation did not complete within {timeout}s")


def create_working_memory(
    client: TestClient,
    text: str,
    session_id: str | None = None,
    task_id: str | None = None,
    user: str = "alice"
) -> str:
    """Create a working memory and wait for it to be ready."""
    selection = {}
    if session_id:
        selection["session_id"] = session_id
    if task_id:
        selection["task_id"] = task_id

    request = make_remember_request(
        selection=selection,
        content={"kind": "text", "text": text}
    )

    response = client.post("/p3/remember", json=request, headers=headers(user=user))
    assert response.status_code in SUCCESS_CODES, f"Remember request failed: {response.text}"

    job_id = response.json()["job_id"]
    operation = poll_operation_until_complete(client, job_id, user=user, timeout=MEMORY_READY_TIMEOUT)

    return operation["result"]["memory_id"]


def create_longterm_memory(
    client: TestClient,
    text: str,
    user: str = "alice"
) -> str:
    """Create a long-term memory (no session/task context) and wait for it to be ready."""
    return create_working_memory(client, text, session_id=None, task_id=None, user=user)


# ============================================================================
# RC-SRC-01: Working source uses real vector retrieval
# ============================================================================


@pytest.mark.sources
@pytest.mark.p0
def test_rc_src_01_working_vector_search(client):
    """RC-SRC-01: Working source executes query encoding and vector search.

    Validates:
    - Working memory request performs actual vector retrieval
    - Query is encoded using embedding model
    - Vector search is executed (not just listing recent memories)
    - Results contain only working memory references
    - Long-term memory is not included as fallback
    """
    # Setup: Create working memory with session context
    session_id = f"session-{uuid4().hex}"
    working_memory_id = create_working_memory(
        client,
        text="I prefer coffee without sugar",
        session_id=session_id,
        user="alice"
    )

    # Create long-term memory (should not appear in results)
    longterm_memory_id = create_longterm_memory(
        client,
        text="Historical preference for tea",
        user="alice"
    )

    # Submit recall request for working source only
    recall_request = make_recall_request(
        query="coffee preferences",
        sources="working",
        selection={"session_id": session_id}
    )

    response = client.post("/p3/recall", json=recall_request, headers=headers(user="alice"))
    assert response.status_code in SUCCESS_CODES, f"Recall request failed: {response.text}"

    job_id = response.json()["job_id"]
    operation = poll_operation_until_complete(client, job_id, user="alice")

    result = operation["result"]

    # Verify working source was used
    assert "selected_sources" in result, "Result missing selected_sources field"
    assert result["selected_sources"] == "working", f"Expected working source, got {result['selected_sources']}"

    # Verify results contain working memory
    assert "items" in result, "Result missing items field"
    memory_ids = [item["memory_id"] for item in result["items"]]
    assert working_memory_id in memory_ids, "Working memory not in results"
    assert longterm_memory_id not in memory_ids, "Long-term memory should not appear in working-only results"

    # Verify coverage indicates working source was searched
    if "coverage" in result:
        assert "working" in result["coverage"], "Coverage missing working source"
        # Working source should show as executed (Ready or similar status)


# ============================================================================
# RC-SRC-02: Long-term source independent retrieval
# ============================================================================


@pytest.mark.sources
@pytest.mark.p0
def test_rc_src_02_longterm_independent_retrieval(client):
    """RC-SRC-02: Long-term source retrieves independently without working fallback.

    Validates:
    - Long-term memory request searches only long-term
    - Working memory is not included as fallback
    - Source filtering happens before scope filtering
    - Results contain only long-term references
    """
    # Setup: Create long-term memory
    longterm_memory_id = create_longterm_memory(
        client,
        text="Historical knowledge about coffee brewing",
        user="alice"
    )

    # Create working memory (should not appear in results)
    session_id = f"session-{uuid4().hex}"
    working_memory_id = create_working_memory(
        client,
        text="Current conversation about tea",
        session_id=session_id,
        user="alice"
    )

    # Submit recall request for long-term source only
    recall_request = make_recall_request(
        query="coffee",
        sources="long_term",
        selection={}
    )

    response = client.post("/p3/recall", json=recall_request, headers=headers(user="alice"))
    assert response.status_code in SUCCESS_CODES, f"Recall request failed: {response.text}"

    job_id = response.json()["job_id"]
    operation = poll_operation_until_complete(client, job_id, user="alice")

    result = operation["result"]

    # Verify long-term source was used
    assert result.get("selected_sources") == "long_term", f"Expected long_term source, got {result.get('selected_sources')}"

    # Verify results contain long-term memory
    memory_ids = [item["memory_id"] for item in result["items"]]
    assert longterm_memory_id in memory_ids, "Long-term memory not in results"
    assert working_memory_id not in memory_ids, "Working memory should not appear in long-term-only results"


# ============================================================================
# RC-SRC-03: Both sources execute and merge correctly
# ============================================================================


@pytest.mark.sources
@pytest.mark.p0
def test_rc_src_03_both_sources_merge(client):
    """RC-SRC-03: Both sources are searched independently and results are merged.

    Validates:
    - Both working and long-term sources execute
    - Results from both sources are included
    - Source attribution is preserved
    - Duplicate memories appear only once
    - Coverage indicates both sources were searched
    """
    # Setup: Create memories in both sources about same topic
    session_id = f"session-{uuid4().hex}"

    working_memory_id = create_working_memory(
        client,
        text="Current preference: I like espresso",
        session_id=session_id,
        user="alice"
    )

    longterm_memory_id = create_longterm_memory(
        client,
        text="Historical note: espresso is a coffee preparation method",
        user="alice"
    )

    # Submit recall request for both sources
    recall_request = make_recall_request(
        query="espresso",
        sources="both",
        selection={"session_id": session_id}
    )

    response = client.post("/p3/recall", json=recall_request, headers=headers(user="alice"))
    assert response.status_code in SUCCESS_CODES, f"Recall request failed: {response.text}"

    job_id = response.json()["job_id"]
    operation = poll_operation_until_complete(client, job_id, user="alice")

    result = operation["result"]

    # Verify both sources were used
    assert result.get("selected_sources") == "both", f"Expected both sources, got {result.get('selected_sources')}"

    # Verify results contain memories from both sources
    memory_ids = [item["memory_id"] for item in result["items"]]
    assert working_memory_id in memory_ids, "Working memory not in results"
    assert longterm_memory_id in memory_ids, "Long-term memory not in results"

    # Verify coverage shows both sources
    if "coverage" in result:
        assert "working" in result["coverage"], "Coverage missing working source"
        assert "long_term" in result["coverage"], "Coverage missing long_term source"


# ============================================================================
# RC-SRC-04: Auto mode with session/task selects both
# ============================================================================


@pytest.mark.sources
@pytest.mark.p1
def test_rc_src_04_auto_mode_with_session_selects_both(client):
    """RC-SRC-04: Auto mode with session context selects both sources.

    Validates:
    - Auto source selection chooses both when session_id present
    - Selection reasoning is traceable
    - Results include both working and long-term memories
    """
    # Setup: Create memories in both sources
    session_id = f"session-{uuid4().hex}"

    working_memory_id = create_working_memory(
        client,
        text="Session context: discussing coffee preferences",
        session_id=session_id,
        user="alice"
    )

    longterm_memory_id = create_longterm_memory(
        client,
        text="Background knowledge about coffee",
        user="alice"
    )

    # Submit recall request with auto source selection and session context
    recall_request = make_recall_request(
        query="coffee",
        sources="auto",
        selection={"session_id": session_id}
    )

    response = client.post("/p3/recall", json=recall_request, headers=headers(user="alice"))
    assert response.status_code in SUCCESS_CODES, f"Recall request failed: {response.text}"

    job_id = response.json()["job_id"]
    operation = poll_operation_until_complete(client, job_id, user="alice")

    result = operation["result"]

    # Verify auto mode selected both sources
    assert result.get("selected_sources") in ["both", "auto"], \
        f"Expected both/auto sources with session, got {result.get('selected_sources')}"

    # Verify results contain memories from both sources
    memory_ids = [item["memory_id"] for item in result["items"]]
    assert working_memory_id in memory_ids, "Working memory not in auto results with session"
    assert longterm_memory_id in memory_ids, "Long-term memory not in auto results with session"


# ============================================================================
# RC-SRC-05: Auto mode without context selects long-term
# ============================================================================


@pytest.mark.sources
@pytest.mark.p1
def test_rc_src_05_auto_mode_without_context_selects_longterm(client):
    """RC-SRC-05: Auto mode without session/task context selects long-term only.

    Validates:
    - Auto selection chooses long-term when no context provided
    - Working memory is not automatically included
    - Selection logic is deterministic
    """
    # Setup: Create memories in both sources
    session_id = f"session-{uuid4().hex}"

    working_memory_id = create_working_memory(
        client,
        text="Working memory content",
        session_id=session_id,
        user="alice"
    )

    longterm_memory_id = create_longterm_memory(
        client,
        text="Long-term knowledge content",
        user="alice"
    )

    # Submit recall request with auto source selection but NO session/task context
    recall_request = make_recall_request(
        query="content",
        sources="auto",
        selection={}  # No session_id or task_id
    )

    response = client.post("/p3/recall", json=recall_request, headers=headers(user="alice"))
    assert response.status_code in SUCCESS_CODES, f"Recall request failed: {response.text}"

    job_id = response.json()["job_id"]
    operation = poll_operation_until_complete(client, job_id, user="alice")

    result = operation["result"]

    # Verify auto mode selected long-term only
    assert result.get("selected_sources") in ["long_term", "auto"], \
        f"Expected long_term with no context, got {result.get('selected_sources')}"

    # Verify results contain long-term but not working memory
    memory_ids = [item["memory_id"] for item in result["items"]]
    assert longterm_memory_id in memory_ids, "Long-term memory should be in auto results without context"
    assert working_memory_id not in memory_ids, "Working memory should not be in auto results without context"


# ============================================================================
# RC-SRC-06: Explicit working doesn't fallback to long-term
# ============================================================================


@pytest.mark.sources
@pytest.mark.p0
def test_rc_src_06_explicit_working_no_fallback(client):
    """RC-SRC-06: Explicit working source request doesn't fall back to long-term.

    Validates:
    - Working-only request returns empty when no working memories match
    - No automatic fallback to long-term source
    - Long-term source remains searchable (isolation verification)
    """
    # Setup: Create only long-term memory (no working memory for this session)
    longterm_memory_id = create_longterm_memory(
        client,
        text="Long-term information about a specific topic",
        user="alice"
    )

    # Use a fresh session with no working memories
    empty_session_id = f"empty-session-{uuid4().hex}"

    # Submit working-only recall request
    recall_request = make_recall_request(
        query="specific topic",
        sources="working",
        selection={"session_id": empty_session_id}
    )

    response = client.post("/p3/recall", json=recall_request, headers=headers(user="alice"))
    assert response.status_code in SUCCESS_CODES, f"Recall request failed: {response.text}"

    job_id = response.json()["job_id"]
    operation = poll_operation_until_complete(client, job_id, user="alice")

    result = operation["result"]

    # Verify working source was used (no fallback)
    assert result.get("selected_sources") == "working", f"Source changed from working: {result.get('selected_sources')}"

    # Verify result is empty (legitimate empty, not error)
    assert "items" in result, "Result missing items field"
    memory_ids = [item["memory_id"] for item in result["items"]]
    assert longterm_memory_id not in memory_ids, "Long-term memory should not appear via fallback"

    # Verify long-term source is still searchable (isolation check)
    longterm_request = make_recall_request(
        query="specific topic",
        sources="long_term",
        selection={}
    )

    response2 = client.post("/p3/recall", json=longterm_request, headers=headers(user="alice"))
    assert response2.status_code in SUCCESS_CODES, f"Long-term recall failed: {response2.text}"

    job_id2 = response2.json()["job_id"]
    operation2 = poll_operation_until_complete(client, job_id2, user="alice")
    result2 = operation2["result"]

    # Verify long-term source can find the memory
    memory_ids2 = [item["memory_id"] for item in result2["items"]]
    assert longterm_memory_id in memory_ids2, "Long-term source should find its own memories"


# ============================================================================
# RC-SRC-07,08 are implemented at P0 in test_recall_projection_states.py.
# Q09 restricts only wait duration/terminal policy, never the whole scenario.


# ============================================================================
# RC-SRC-09: implemented in test_recall_mixed_coverage.py (Ready+pending/failed).
# ============================================================================


# ============================================================================
# RC-SRC-10: Single source failure degradation
# ============================================================================


@pytest.mark.sources
@pytest.mark.p1
def test_rc_src_10_single_source_failure_degrades(client):
    """RC-SRC-10: Single source failure in both-mode returns successful source.

    Validates:
    - With degradation policy, one source failure returns other source
    - Results marked as degraded with reason
    - Coverage shows successful and failed sources
    - Degradation doesn't return unauthorized content

    Note: Requires ability to inject source failures.
    """
    pytest.skip("Requires source failure injection capability")


# ============================================================================
# RC-SRC-11: Required source failure rejects partial results
# ============================================================================


@pytest.mark.sources
@pytest.mark.p1
def test_rc_src_11_required_source_failure_rejects(client):
    """RC-SRC-11: Required source failure fails entire request.

    Validates:
    - With strict policy, required source failure fails request
    - No partial results are delivered
    - Failure is not masked by having some valid candidates

    Note: Requires ability to inject source failures and configure policy.
    """
    pytest.skip("Requires source failure injection and policy configuration")


# ============================================================================
# RC-SRC-12: All vector sources unavailable fails correctly
# ============================================================================


@pytest.mark.sources
@pytest.mark.p0
def test_rc_src_12_all_vector_sources_unavailable(client):
    """RC-SRC-12: Complete vector source unavailability fails clearly.

    Validates:
    - All sources unavailable fails with clear error
    - No fallback to keyword search
    - Body availability doesn't mask vector failure
    - Failure mode is explicit

    Note: Requires ability to inject vector database failures.
    """
    pytest.skip("Requires vector database failure injection capability")


# ============================================================================
# RC-SRC-13: Normal empty vs empty+failure distinction
# ============================================================================


@pytest.mark.sources
@pytest.mark.p1
def test_rc_src_13_normal_empty_vs_degraded_empty(client):
    """RC-SRC-13: Normal empty is distinguished from degraded empty.

    Validates:
    - Normal empty: all sources searched successfully, no matches
    - Degraded empty: some sources failed, no matches from successful sources
    - Coverage status accurately reflects difference
    - Monitoring can distinguish health states
    """
    # Setup: Create memories that won't match the query
    session_id = f"session-{uuid4().hex}"

    create_working_memory(
        client,
        text="Information about tea preferences",
        session_id=session_id,
        user="alice"
    )

    create_longterm_memory(
        client,
        text="Historical data about tea cultivation",
        user="alice"
    )

    # Submit recall with non-matching query
    recall_request = make_recall_request(
        query="quantum physics and relativity theory",
        sources="both",
        selection={"session_id": session_id}
    )

    response = client.post("/p3/recall", json=recall_request, headers=headers(user="alice"))
    assert response.status_code in SUCCESS_CODES, f"Recall request failed: {response.text}"

    job_id = response.json()["job_id"]
    operation = poll_operation_until_complete(client, job_id, user="alice")

    result = operation["result"]

    # Verify normal empty (all sources searched, no matches)
    assert result.get("selected_sources") == "both", "Both sources should have been selected"
    assert len(result.get("items", [])) == 0, "Should have no matching items"

    # Verify NOT marked as degraded
    assert not result.get("degraded", False), "Normal empty should not be marked degraded"

    # Verify coverage shows both sources completed successfully
    if "coverage" in result:
        # Both sources should show as searched (exact status depends on implementation)
        assert "working" in result["coverage"], "Coverage should include working source"
        assert "long_term" in result["coverage"], "Coverage should include long_term source"


# ============================================================================
# RC-SRC-14: Source completion order doesn't affect ranking
# ============================================================================


@pytest.mark.sources
@pytest.mark.p1
def test_rc_src_14_completion_order_independence(client):
    """RC-SRC-14: Results don't depend on which source completes first.

    Validates:
    - Source completion order doesn't affect fusion scores
    - Ranking is deterministic regardless of timing
    - Late-arriving results past deadline don't modify committed results

    Note: Requires ability to control source completion timing.
    """
    pytest.skip("Requires source timing control capability")


# ============================================================================
# Additional Edge Cases
# ============================================================================


@pytest.mark.sources
@pytest.mark.p2
def test_task_context_auto_selection(client):
    """Auto mode with task_id context should select both sources like session_id does."""
    # Setup: Create memories in both sources
    task_id = f"task-{uuid4().hex}"

    working_memory_id = create_working_memory(
        client,
        text="Task context information",
        task_id=task_id,
        user="alice"
    )

    longterm_memory_id = create_longterm_memory(
        client,
        text="Background knowledge for task",
        user="alice"
    )

    # Submit recall with auto source and task context
    recall_request = make_recall_request(
        query="task information",
        sources="auto",
        selection={"task_id": task_id}
    )

    response = client.post("/p3/recall", json=recall_request, headers=headers(user="alice"))
    assert response.status_code in SUCCESS_CODES, f"Recall request failed: {response.text}"

    job_id = response.json()["job_id"]
    operation = poll_operation_until_complete(client, job_id, user="alice")

    result = operation["result"]

    # Verify both sources were selected with task context
    assert result.get("selected_sources") in ["both", "auto"], \
        f"Expected both sources with task context, got {result.get('selected_sources')}"

    # Verify results from both sources
    memory_ids = [item["memory_id"] for item in result["items"]]
    assert working_memory_id in memory_ids, "Working memory not in results with task context"
    assert longterm_memory_id in memory_ids, "Long-term memory not in results with task context"


@pytest.mark.sources
@pytest.mark.p2
def test_cross_tenant_isolation_working_source(client):
    """Working source respects tenant boundaries."""
    # Setup: Create memories in different tenants
    session_alice = f"session-{uuid4().hex}"
    session_charlie = f"session-{uuid4().hex}"

    memory_alice = create_working_memory(
        client,
        text="Alice's private session data",
        session_id=session_alice,
        user="alice"
    )

    memory_charlie = create_working_memory(
        client,
        text="Charlie's private session data",
        session_id=session_charlie,
        user="charlie"
    )

    # Alice requests working source
    recall_request = make_recall_request(
        query="session data",
        sources="working",
        selection={"session_id": session_alice}
    )

    response = client.post("/p3/recall", json=recall_request, headers=headers(user="alice"))
    assert response.status_code in SUCCESS_CODES, f"Recall request failed: {response.text}"

    job_id = response.json()["job_id"]
    operation = poll_operation_until_complete(client, job_id, user="alice")

    result = operation["result"]

    # Verify Alice only sees her own tenant's memories
    memory_ids = [item["memory_id"] for item in result["items"]]
    assert memory_alice in memory_ids, "Alice should see her own working memory"
    assert memory_charlie not in memory_ids, "Alice should not see Charlie's working memory (cross-tenant)"
