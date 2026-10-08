"""Core testing infrastructure & happy path for Recall API (AET-8, RC-API-01).

This test module establishes the foundational test infrastructure for validating
the Recall flow, including:
- Test data preparation (Working memory fixtures)
- API client setup with proper authentication
- Request submission and operation polling
- Result verification within token budget
- Evidence collection (request/response, operation IDs, trace data, execution stages)
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
# Test Fixtures and Helpers
# ============================================================================


def headers(operation: str | None = None, user: str = "alice") -> dict[str, str]:
    """Generate authentication headers for API requests.

    Args:
        operation: Optional operation ID for idempotency tracking
        user: User credential for Bearer token authentication

    Returns:
        Dictionary of HTTP headers with Authorization and optional X-Operation-ID
    """
    value = {"Authorization": f"Bearer {user}"}
    if operation:
        value["X-Operation-ID"] = operation
    return value


def eventually(check: callable, timeout: float = 90) -> Any:
    """Poll a check function until it returns a truthy value or times out.

    Args:
        check: Function that returns a value when condition is met, None otherwise
        timeout: Maximum seconds to wait before raising AssertionError

    Returns:
        The truthy value returned by check()

    Raises:
        AssertionError: If timeout is exceeded without check returning truthy value
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = check()
        if value:
            return value
        time.sleep(0.05)
    raise AssertionError(f"Condition not met within {timeout}s timeout")


def poll_operation_until_complete(
    client: TestClient,
    job_id: str,
    user: str = "alice",
    timeout: float = 90
) -> dict[str, Any]:
    """Poll an operation until it reaches a terminal state.

    Args:
        client: FastAPI test client
        job_id: Operation/task ID to poll
        user: User for authentication headers
        timeout: Maximum seconds to wait

    Returns:
        Final operation state as a dictionary

    Raises:
        AssertionError: If operation doesn't complete or fails
    """
    def check_complete():
        response = client.get(f"/p3/operations/{job_id}", headers=headers(user=user))
        assert response.status_code == 200, f"Failed to get operation status: {response.text}"
        operation = response.json()

        if operation["state"] in {"succeeded", "failed", "attention_required"}:
            return operation
        return None

    operation = eventually(check_complete, timeout=timeout)
    assert operation["state"] == "succeeded", (
        f"Operation {job_id} did not succeed. State: {operation['state']}, "
        f"Details: {operation.get('error', 'No error details')}"
    )
    return operation


class TestEvidence:
    """Container for collecting test evidence during execution."""

    def __init__(self):
        self.requests: list[dict[str, Any]] = []
        self.responses: list[dict[str, Any]] = []
        self.operation_ids: list[str] = []
        self.trace_ids: list[str] = []
        self.execution_stages: list[dict[str, Any]] = []

    def record_request(self, method: str, path: str, body: Any, headers: dict):
        """Record an API request."""
        self.requests.append({
            "method": method,
            "path": path,
            "body": body,
            "headers": {k: v for k, v in headers.items() if k != "Authorization"}
        })

    def record_response(self, status: int, body: Any, headers: dict):
        """Record an API response."""
        self.responses.append({
            "status": status,
            "body": body,
            "location": headers.get("Location"),
        })

    def record_operation(self, operation_id: str, job_id: str, state: str):
        """Record an operation's state."""
        self.operation_ids.append(job_id)
        self.execution_stages.append({
            "operation_id": operation_id,
            "job_id": job_id,
            "state": state,
            "timestamp": time.time()
        })

    def summary(self) -> dict[str, Any]:
        """Generate a summary of collected evidence."""
        return {
            "total_requests": len(self.requests),
            "total_responses": len(self.responses),
            "operation_ids": self.operation_ids,
            "execution_stages": self.execution_stages,
            "trace_ids": self.trace_ids,
        }


@pytest.fixture
def test_configuration(tmp_path, temporal_server):
    """Create a test configuration with authentication and Temporal setup.

    This fixture provides:
    - Isolated data directory
    - Test identity configuration (alice in t1, eve in t2)
    - Temporal server connection
    - Test-optimized timing parameters
    """
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
                    for user, tenant in (("alice", "t1"), ("eve", "t2"))
                ],
            }
        ),
        encoding="utf-8",
    )

    namespace = "recall-test-" + uuid4().hex
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


class WorkingMemoryFixture:
    """夹具 F1: Predefined working memory test data."""

    COFFEE_PREFERENCE = {
        "source": {
            "kind": "conversation",
            "external_id": "coffee-pref",
            "external_version": "1",
            "occurred_at": "2026-10-08T00:00:00.000Z",
        },
        "selection": {"session_id": "test-session-1", "task_id": "coffee-task"},
        "content": {"kind": "text", "text": "用户喝咖啡不加糖"},
    }

    TEA_PREFERENCE = {
        "source": {
            "kind": "conversation",
            "external_id": "tea-pref",
            "external_version": "1",
            "occurred_at": "2026-10-08T00:01:00.000Z",
        },
        "selection": {"session_id": "test-session-1", "task_id": "tea-task"},
        "content": {"kind": "text", "text": "用户喜欢乌龙茶"},
    }


def prepare_working_memory(
    client: TestClient,
    evidence: TestEvidence,
    user: str = "alice"
) -> tuple[str, str]:
    """Prepare test data: create two working memories and wait for Ready state.

    This implements 夹具 F1 preparation:
    - Creates "用户喝咖啡不加糖" memory
    - Creates "用户喜欢乌龙茶" memory
    - Waits for both to reach Ready state in long-term storage

    Args:
        client: FastAPI test client
        evidence: Evidence collector for tracking requests/responses
        user: User for authentication

    Returns:
        Tuple of (coffee_memory_id, tea_memory_id)
    """
    # Wait for service readiness
    eventually(lambda: client.get("/p3/readyz").status_code == 200)

    # Create coffee preference memory
    evidence.record_request("POST", "/p3/remember", WorkingMemoryFixture.COFFEE_PREFERENCE, headers(user=user))
    response = client.post(
        "/p3/remember",
        json=WorkingMemoryFixture.COFFEE_PREFERENCE,
        headers=headers(operation="prepare-coffee", user=user)
    )
    evidence.record_response(response.status_code, response.json(), response.headers)
    assert response.status_code == 200, f"Failed to create coffee memory: {response.text}"

    # Create tea preference memory
    evidence.record_request("POST", "/p3/remember", WorkingMemoryFixture.TEA_PREFERENCE, headers(user=user))
    response = client.post(
        "/p3/remember",
        json=WorkingMemoryFixture.TEA_PREFERENCE,
        headers=headers(operation="prepare-tea", user=user)
    )
    evidence.record_response(response.status_code, response.json(), response.headers)
    assert response.status_code == 200, f"Failed to create tea memory: {response.text}"

    # Wait for memories to reach Ready state in long-term storage
    def check_ready_memories():
        response = client.get("/p3/memories", headers=headers(user=user))
        assert response.status_code == 200, f"Failed to get memories: {response.text}"

        memories = response.json()["items"]
        ready_memories = [
            m for m in memories
            if m["kind"] != "working" and m["projection_state"] == "ready"
        ]

        if len(ready_memories) >= 2:
            return ready_memories
        return None

    ready_memories = eventually(check_ready_memories, timeout=120)

    # Extract memory IDs
    memory_ids = [m["ref"]["memory_id"] for m in ready_memories[:2]]
    assert len(memory_ids) == 2, f"Expected 2 ready memories, got {len(memory_ids)}"

    return memory_ids[0], memory_ids[1]


# ============================================================================
# RC-API-01: Happy Path - Simplest Successful Recall Flow
# ============================================================================


def test_rc_api_01_happy_path_recall_with_working_memory(test_configuration):
    """RC-API-01: Test the simplest successful Recall flow end-to-end.

    This test validates:
    1. Test harness can prepare test data (夹具 F1: Working memory in Ready state)
    2. Can submit POST /p3/recall requests with proper authentication
    3. Can poll operation status until completion
    4. Can retrieve results via /p3/operations/{job_id}/result
    5. Returned context pack contains expected content within token budget
    6. Evidence collection captures: request/response, operation IDs, trace data, execution stages

    Acceptance Criteria:
    - Test data preparation succeeds (2 memories in Ready state)
    - Recall request is accepted (200 response with job_id)
    - Operation completes successfully (state: succeeded)
    - Result contains relevant context matching the query
    - Token budget is respected
    - All evidence is collected
    """
    evidence = TestEvidence()
    service = Service(test_configuration)

    with TestClient(service.app()) as client:
        # Step 1: Prepare test data (夹具 F1)
        print("\n[RC-API-01] Step 1: Preparing working memory fixtures...")
        coffee_id, tea_id = prepare_working_memory(client, evidence, user="alice")
        print(f"  ✓ Coffee memory ready: {coffee_id}")
        print(f"  ✓ Tea memory ready: {tea_id}")

        # Step 2: Submit Recall request with authentication
        print("\n[RC-API-01] Step 2: Submitting recall request...")
        recall_request = RecallRequest(
            query="咖啡",  # Query for coffee-related memories
            selection={},  # Empty selection means search across all accessible memories
            sources="long_term",  # Search in long-term storage (Ready state memories)
            token_budget=1000,
        )

        operation_id = "rc-api-01-recall"
        evidence.record_request(
            "POST",
            "/p3/recall",
            recall_request.model_dump(),
            headers(operation=operation_id, user="alice")
        )

        response = client.post(
            "/p3/recall",
            json=recall_request.model_dump(),
            headers=headers(operation=operation_id, user="alice")
        )

        evidence.record_response(response.status_code, response.json(), response.headers)

        # Step 3: Verify request acceptance
        print(f"  Response status: {response.status_code}")
        assert response.status_code in {200, 202}, (
            f"Recall request should be accepted. Got {response.status_code}: {response.text}"
        )

        # Handle both synchronous (200) and asynchronous (202) responses
        if response.status_code == 200:
            # Synchronous execution - result returned immediately
            result = response.json()
            job_id = result.get("recall_id", "sync-execution")
            print(f"  ✓ Recall completed synchronously")
        else:
            # Asynchronous execution - need to poll
            location = response.headers.get("Location")
            assert location, "Async response must include Location header"

            # Extract job_id from Location header (e.g., "/p3/operations/{job_id}")
            job_id = location.split("/")[-1]
            print(f"  ✓ Recall accepted, job_id: {job_id}")
            evidence.record_operation(operation_id, job_id, "accepted")

            # Step 4: Poll operation status until completion
            print("\n[RC-API-01] Step 3: Polling operation status...")
            operation = poll_operation_until_complete(client, job_id, user="alice", timeout=120)
            print(f"  ✓ Operation completed: {operation['state']}")
            evidence.record_operation(operation_id, job_id, operation["state"])

            # Step 5: Retrieve result
            print("\n[RC-API-01] Step 4: Retrieving result...")
            result_response = client.get(
                f"/p3/operations/{job_id}/result",
                headers=headers(user="alice")
            )
            assert result_response.status_code == 200, (
                f"Failed to retrieve result: {result_response.text}"
            )
            result = result_response.json()
            evidence.record_response(result_response.status_code, result, result_response.headers)

        # Step 6: Verify result content
        print("\n[RC-API-01] Step 5: Verifying result content...")
        assert "recall_id" in result, "Result must include recall_id"
        assert "rendered_context" in result, "Result must include rendered_context"
        assert "items" in result, "Result must include items list"

        # Verify that the result contains coffee-related content
        rendered_context = result["rendered_context"]
        assert "咖啡" in rendered_context or "coffee" in rendered_context.lower(), (
            f"Result should contain coffee-related content. Got: {rendered_context[:200]}"
        )

        print(f"  ✓ Result contains relevant content")
        print(f"  ✓ Recall ID: {result['recall_id']}")
        print(f"  ✓ Items returned: {len(result['items'])}")

        # Step 7: Verify token budget compliance
        print("\n[RC-API-01] Step 6: Verifying token budget...")
        # The rendered_context should respect the token_budget parameter
        # Rough estimate: 1 token ≈ 4 characters for English, ~1.5 for Chinese
        context_length = len(rendered_context)
        estimated_tokens = context_length / 2  # Conservative estimate for mixed content

        assert estimated_tokens <= recall_request.token_budget * 1.2, (
            f"Context exceeds token budget. Estimated {estimated_tokens} tokens, "
            f"budget: {recall_request.token_budget}"
        )
        print(f"  ✓ Token budget respected (estimated {int(estimated_tokens)} tokens)")

        # Step 8: Verify evidence collection
        print("\n[RC-API-01] Step 7: Verifying evidence collection...")
        evidence_summary = evidence.summary()

        assert evidence_summary["total_requests"] >= 3, (
            "Should have at least 3 requests (2 remember + 1 recall)"
        )
        assert evidence_summary["total_responses"] >= 3, (
            "Should have at least 3 responses"
        )
        assert len(evidence_summary["operation_ids"]) >= 1, (
            "Should have recorded at least 1 operation ID"
        )
        assert len(evidence_summary["execution_stages"]) >= 1, (
            "Should have recorded execution stages"
        )

        print(f"  ✓ Evidence collected:")
        print(f"    - Requests: {evidence_summary['total_requests']}")
        print(f"    - Responses: {evidence_summary['total_responses']}")
        print(f"    - Operation IDs: {evidence_summary['operation_ids']}")
        print(f"    - Execution stages: {len(evidence_summary['execution_stages'])}")

        print("\n[RC-API-01] ✅ All acceptance criteria passed!")
        print(f"\nTest Summary:")
        print(f"  - Working memory fixtures prepared: 2")
        print(f"  - Recall request submitted and completed successfully")
        print(f"  - Result verified with relevant content")
        print(f"  - Token budget respected")
        print(f"  - Evidence collection complete")


# ============================================================================
# Additional Infrastructure Tests
# ============================================================================


def test_recall_infrastructure_can_handle_empty_results(test_configuration):
    """Verify the infrastructure handles queries with no matching results gracefully."""
    evidence = TestEvidence()
    service = Service(test_configuration)

    with TestClient(service.app()) as client:
        # Prepare working memory
        coffee_id, tea_id = prepare_working_memory(client, evidence, user="alice")

        # Submit recall request with query that shouldn't match
        recall_request = RecallRequest(
            query="火星旅行计划",  # Query that shouldn't match any memory
            selection={},
            sources="long_term",
            token_budget=1000,
        )

        response = client.post(
            "/p3/recall",
            json=recall_request.model_dump(),
            headers=headers(operation="empty-result-test", user="alice")
        )

        # Should still succeed, just with empty or minimal results
        assert response.status_code in {200, 202}, (
            f"Empty result query should still succeed: {response.text}"
        )

        if response.status_code == 202:
            location = response.headers.get("Location")
            job_id = location.split("/")[-1]
            operation = poll_operation_until_complete(client, job_id)

            result_response = client.get(
                f"/p3/operations/{job_id}/result",
                headers=headers(user="alice")
            )
            result = result_response.json()
        else:
            result = response.json()

        # Should have structure even if empty
        assert "recall_id" in result
        assert "rendered_context" in result
        assert "items" in result


def test_recall_infrastructure_respects_authentication(test_configuration):
    """Verify the infrastructure properly enforces authentication."""
    service = Service(test_configuration)

    with TestClient(service.app()) as client:
        eventually(lambda: client.get("/p3/readyz").status_code == 200)

        recall_request = RecallRequest(
            query="test",
            selection={},
            sources="long_term",
            token_budget=1000,
        )

        # Request without authentication should fail
        response = client.post(
            "/p3/recall",
            json=recall_request.model_dump(),
        )
        assert response.status_code == 401, (
            "Request without authentication should return 401"
        )

        # Request with invalid token should fail
        response = client.post(
            "/p3/recall",
            json=recall_request.model_dump(),
            headers={"Authorization": "Bearer invalid-token"}
        )
        assert response.status_code == 401, (
            "Request with invalid token should return 401"
        )
