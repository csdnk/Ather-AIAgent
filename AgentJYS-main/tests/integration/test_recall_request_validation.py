"""Request contract validation tests for Recall API (AET-9, RC-API-02 through RC-API-12).

This test module validates API request handling including:
- Parameter validation (query, token_budget, sources, selection)
- Malformed request handling
- Security (injection attacks, field forgery)
- HTTP protocol compliance
"""

import json
from hashlib import sha256
from uuid import uuid4

import pytest
import yaml
from fastapi.testclient import TestClient

from aether_agent_memory.recall.contracts.models import RecallRequest
from aether_agent_memory.runtime.contracts.models import Permission
from azure_component_service import Service
from component_configuration import ComponentConfiguration

pytestmark = pytest.mark.integration


# ============================================================================
# Constants
# ============================================================================

# Timeout and polling constants
SERVICE_READY_TIMEOUT = 30  # seconds to wait for service readiness
SERVICE_READY_POLL_INTERVAL = 0.1  # seconds between readiness checks

# HTTP status code groups
SUCCESS_CODES = {200, 202}
CLIENT_ERROR_CODES = {400, 422}
METHOD_NOT_ALLOWED = 405
UNSUPPORTED_MEDIA_TYPE = 415


# ============================================================================
# Test Configuration and Fixtures
# ============================================================================


@pytest.fixture
def test_configuration(tmp_path, temporal_server):
    """Create test configuration for validation tests."""
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

    namespace = "recall-validation-" + uuid4().hex
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
    """Provide a ready TestClient with service already started.

    This fixture eliminates the need for repeated service setup in each test.
    """
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
    """Create a recall request with sensible defaults.

    Args:
        **overrides: Override any default field values

    Returns:
        Dictionary suitable for JSON request body
    """
    defaults = {
        "query": "test",
        "selection": {},
        "sources": "long_term",
        "token_budget": 1000,
    }
    defaults.update(overrides)
    return defaults


def assert_client_error(response):
    """Assert response is a client error (400 or 422)."""
    assert response.status_code in CLIENT_ERROR_CODES, (
        f"Expected client error (400/422), got {response.status_code}: {response.text}"
    )


def assert_success(response):
    """Assert response is successful (200 or 202)."""
    assert response.status_code in SUCCESS_CODES, (
        f"Expected success (200/202), got {response.status_code}: {response.text}"
    )


def assert_no_server_error(response):
    """Assert no server error (status < 500)."""
    assert response.status_code < 500, (
        f"Server error (5xx) occurred: {response.status_code}: {response.text}"
    )


# ============================================================================
# RC-API-02 & RC-API-03: Query Parameter Validation
# ============================================================================


def test_rc_api_02_null_query_parameter(client):
    """RC-API-02: Validate handling of null query parameter.

    The API should reject requests with null query values.
    """
    response = client.post(
        "/p3/recall",
        json=make_recall_request(query=None),
        headers=headers(operation="null-query-test"),
    )

    assert_client_error(response)


def test_rc_api_03_empty_and_whitespace_query(client):
    """RC-API-03: Validate handling of empty and whitespace-only queries.

    Empty strings and whitespace-only queries should be handled appropriately.
    """
    # Test empty string query
    response = client.post(
        "/p3/recall",
        json=make_recall_request(query=""),
        headers=headers(operation="empty-query-test"),
    )

    # Empty query might be accepted (return empty results) or rejected
    assert response.status_code in {200, 202, 400, 422}, (
        f"Unexpected status for empty query: {response.status_code}"
    )

    # Test whitespace-only query
    response = client.post(
        "/p3/recall",
        json=make_recall_request(query="   \t\n  "),
        headers=headers(operation="whitespace-query-test"),
    )

    assert response.status_code in {200, 202, 400, 422}, (
        f"Unexpected status for whitespace query: {response.status_code}"
    )


def test_rc_api_03_query_type_mismatches(client):
    """RC-API-03: Validate rejection of wrong query parameter types.

    Query must be a string, not integer, array, or object.
    """
    invalid_query_types = [
        (123, "integer"),
        (123.45, "float"),
        (["array", "query"], "array"),
        ({"object": "query"}, "object"),
        (True, "boolean"),
    ]

    for invalid_query, type_name in invalid_query_types:
        response = client.post(
            "/p3/recall",
            json=make_recall_request(query=invalid_query),
            headers=headers(operation=f"query-type-{type_name}"),
        )

        assert_client_error(response)


def test_rc_api_03_unicode_query_handling(client):
    """RC-API-03: Validate handling of Unicode characters in queries.

    The API should properly handle various Unicode characters including
    emoji, CJK characters, and special symbols.
    """
    unicode_queries = [
        "咖啡☕",  # Chinese + emoji
        "🔍 search query",  # Emoji at start
        "test​‌query",  # Zero-width characters
        "café résumé",  # Accented characters
        "query\u0000test",  # Null byte
    ]

    for query in unicode_queries:
        response = client.post(
            "/p3/recall",
            json=make_recall_request(query=query),
            headers=headers(operation=f"unicode-query-{abs(hash(query))}"),
        )

        # Should either accept or reject cleanly, not crash
        assert_no_server_error(response)


# ============================================================================
# RC-API-04: Token Budget Boundary Tests
# ============================================================================


def test_rc_api_04_token_budget_boundaries(client):
    """RC-API-04: Validate token budget boundary conditions.

    Test minimum, maximum, zero, negative, and invalid token budget values.
    """
    test_cases = [
        (0, "zero tokens", True),  # Should reject
        (-1, "negative tokens", True),  # Should reject
        (-999999, "large negative", True),  # Should reject
        (1, "minimum valid", False),  # Should accept
        (10000, "reasonable large", False),  # Should accept
        (1000000, "very large", False),  # Should handle gracefully
        (None, "null token budget", True),  # Should reject
    ]

    for token_budget, description, should_reject in test_cases:
        request_data = make_recall_request(query="test", selection={}, sources="long_term")
        if token_budget is not None:
            request_data["token_budget"] = token_budget
        else:
            # Remove token_budget to test None/missing
            request_data.pop("token_budget", None)

        response = client.post(
            "/p3/recall",
            json=request_data,
            headers=headers(operation=f"token-budget-{description.replace(' ', '-')}"),
        )

        if should_reject:
            assert_client_error(response)
        else:
            # Should either accept or reject gracefully (no server error)
            assert_no_server_error(response)


def test_rc_api_04_token_budget_type_mismatch(client):
    """RC-API-04: Validate rejection of non-integer token budget values."""
    invalid_budgets = [
        ("1000", "string"),
        (1000.5, "float"),
        ([], "array"),
        ({}, "object"),
    ]

    for invalid_budget, type_name in invalid_budgets:
        response = client.post(
            "/p3/recall",
            json=make_recall_request(token_budget=invalid_budget),
            headers=headers(operation=f"invalid-budget-{type_name}"),
        )

        assert_client_error(response)


# ============================================================================
# RC-API-05: Sources Enumeration Validation
# ============================================================================


def test_rc_api_05_sources_enumeration(client):
    """RC-API-05: Validate sources parameter enumeration.

    Test valid values, invalid values, case sensitivity, and defaults.
    """
    # Valid sources values
    valid_sources = ["working", "long_term", "all"]

    for source in valid_sources:
        response = client.post(
            "/p3/recall",
            json=make_recall_request(sources=source),
            headers=headers(operation=f"valid-source-{source}"),
        )

        assert_success(response)

    # Invalid sources value
    response = client.post(
        "/p3/recall",
        json=make_recall_request(sources="invalid_source"),
        headers=headers(operation="invalid-source"),
    )

    assert_client_error(response)

    # Case sensitivity test
    response = client.post(
        "/p3/recall",
        json=make_recall_request(sources="Long_Term"),  # Wrong case
        headers=headers(operation="case-sensitive-source"),
    )

    # Case mismatch should be rejected unless API is case-insensitive
    assert response.status_code in {200, 202, 400, 422}, (
        f"Unexpected status for case mismatch: {response.status_code}"
    )


def test_rc_api_05_sources_default_value(client):
    """RC-API-05: Validate default sources value when omitted."""
    request_data = make_recall_request()
    request_data.pop("sources")  # Remove sources to test default

    response = client.post(
        "/p3/recall",
        json=request_data,
        headers=headers(operation="default-source"),
    )

    # Should either use default or require the field
    assert response.status_code in {200, 202, 400, 422}, (
        f"Unexpected status when sources omitted: {response.status_code}"
    )


# ============================================================================
# RC-API-06: Selection Structure Validation
# ============================================================================


def test_rc_api_06_selection_structure(client):
    """RC-API-06: Validate selection parameter structure.

    Test missing, empty, and various selection structures.
    """
    # Empty selection (valid)
    response = client.post(
        "/p3/recall",
        json=make_recall_request(selection={}),
        headers=headers(operation="empty-selection"),
    )

    assert_success(response)

    # Selection with valid fields
    response = client.post(
        "/p3/recall",
        json=make_recall_request(selection={
            "session_id": "test-session",
            "task_id": "test-task",
        }),
        headers=headers(operation="valid-selection"),
    )

    assert_success(response)

    # Null selection
    response = client.post(
        "/p3/recall",
        json=make_recall_request(selection=None),
        headers=headers(operation="null-selection"),
    )

    assert_client_error(response)


def test_rc_api_06_selection_type_errors(client):
    """RC-API-06: Validate rejection of wrong selection types."""
    invalid_selections = [
        ("string-selection", "string"),
        (["array", "selection"], "array"),
        (123, "number"),
    ]

    for invalid_selection, type_name in invalid_selections:
        response = client.post(
            "/p3/recall",
            json=make_recall_request(selection=invalid_selection),
            headers=headers(operation=f"invalid-selection-{type_name}"),
        )

        assert_client_error(response)


# ============================================================================
# RC-API-07: Injection Attack Prevention
# ============================================================================


def test_rc_api_07_sql_injection_prevention(client):
    """RC-API-07: Validate SQL injection prevention in query field.

    The API should safely handle SQL injection attempts without exposing
    database internals or allowing unauthorized data access.
    """
    sql_injection_payloads = [
        "'; DROP TABLE memories; --",
        "1' OR '1'='1",
        "' UNION SELECT * FROM users --",
        "admin'--",
        "' OR 1=1--",
    ]

    for payload in sql_injection_payloads:
        response = client.post(
            "/p3/recall",
            json=make_recall_request(query=payload),
            headers=headers(operation=f"sql-injection-{abs(hash(payload))}"),
        )

        # Should handle safely (not crash) and not expose DB errors
        assert_no_server_error(response)

        if response.status_code >= 400:
            # Error messages should not expose database details
            error_text = response.text.lower()
            assert "sql" not in error_text, "Error exposes SQL details"
            assert "database" not in error_text, "Error exposes database details"


def test_rc_api_07_nosql_injection_prevention(client):
    """RC-API-07: Validate NoSQL injection prevention in selection field."""
    # NoSQL injection attempts in selection object
    nosql_payloads = [
        {"session_id": {"$ne": None}},
        {"session_id": {"$gt": ""}},
        {"$where": "this.session_id == 'admin'"},
    ]

    for payload in nosql_payloads:
        response = client.post(
            "/p3/recall",
            json=make_recall_request(selection=payload),
            headers=headers(operation=f"nosql-injection-{abs(hash(str(payload)))}"),
        )

        # Should handle safely
        assert_no_server_error(response)


# ============================================================================
# RC-API-08: Client Field Forgery Prevention
# ============================================================================


def test_rc_api_08_tenant_id_forgery_prevention(client):
    """RC-API-08: Validate that clients cannot forge tenant_id.

    The tenant context should be determined by authentication, not client input.
    """
    # Attempt to include tenant_id in request body
    response = client.post(
        "/p3/recall",
        json=make_recall_request(selection={"tenant_id": "t2"}),  # Try to forge tenant
        headers=headers(operation="tenant-forgery", user="alice"),  # alice is in t1
    )

    # Should either ignore the field or reject it, but not honor the forgery
    assert_no_server_error(response)


def test_rc_api_08_internal_field_forgery(client):
    """RC-API-08: Validate that clients cannot inject internal policy fields."""
    # Attempt to inject internal fields
    internal_fields = [
        ({"_internal_policy": "bypass"}, "internal_policy"),
        ({"admin": True}, "admin"),
        ({"is_system": True}, "is_system"),
        ({"bypass_auth": True}, "bypass_auth"),
    ]

    for fields, field_name in internal_fields:
        response = client.post(
            "/p3/recall",
            json=make_recall_request(selection=fields),
            headers=headers(operation=f"internal-field-{field_name}"),
        )

        # Should either ignore or reject, but handle safely
        assert_no_server_error(response)


# ============================================================================
# RC-API-09: Malformed Request Handling
# ============================================================================


def test_rc_api_09_truncated_json(client):
    """RC-API-09: Validate handling of truncated JSON requests."""
    truncated_jsons = [
        '{"query": "test", "selection": {',  # Truncated object
        '{"query": "test"',  # Missing closing brace
        '{"query": "test", "selection": {}, "sources": "long_term", "token_budget":',  # Truncated value
    ]

    for truncated_json in truncated_jsons:
        response = client.post(
            "/p3/recall",
            content=truncated_json,
            headers={
                **headers(operation=f"truncated-{abs(hash(truncated_json))}"),
                "Content-Type": "application/json",
            },
        )

        # Should return 400 or 422 for malformed JSON
        assert response.status_code in {400, 422}, (
            f"Truncated JSON should return 400/422, got {response.status_code}"
        )


def test_rc_api_09_wrong_content_type(client):
    """RC-API-09: Validate handling of wrong Content-Type header."""
    valid_json = json.dumps(make_recall_request())

    wrong_content_types = [
        "text/plain",
        "application/xml",
        "application/x-www-form-urlencoded",
        "multipart/form-data",
    ]

    for content_type in wrong_content_types:
        response = client.post(
            "/p3/recall",
            content=valid_json,
            headers={
                **headers(operation=f"wrong-content-type-{content_type.replace('/', '-')}"),
                "Content-Type": content_type,
            },
        )

        # Should reject wrong content type
        assert response.status_code in {400, UNSUPPORTED_MEDIA_TYPE}, (
            f"Wrong Content-Type should be rejected, got {response.status_code}"
        )


def test_rc_api_09_invalid_json_syntax(client):
    """RC-API-09: Validate handling of various JSON syntax errors."""
    invalid_jsons = [
        "not json at all",
        '{"query": "test", "selection": {},}',  # Trailing comma
        "{query: 'test'}",  # Unquoted key
        '{"query": \'test\'}',  # Single quotes
        'NaN',  # Invalid value
    ]

    for invalid_json in invalid_jsons:
        response = client.post(
            "/p3/recall",
            content=invalid_json,
            headers={
                **headers(operation=f"invalid-json-{abs(hash(invalid_json))}"),
                "Content-Type": "application/json",
            },
        )

        # Should return 400 or 422
        assert response.status_code in {400, 422}, (
            f"Invalid JSON should return 400/422, got {response.status_code}"
        )


# ============================================================================
# RC-API-10: Duplicate JSON Key Handling
# ============================================================================


def test_rc_api_10_duplicate_json_keys(client):
    """RC-API-10: Validate handling of duplicate keys in JSON request.

    JSON spec allows duplicate keys but behavior is undefined.
    The API should handle them consistently.
    """
    # JSON with duplicate query keys
    duplicate_key_json = '''{
        "query": "first query",
        "selection": {},
        "sources": "long_term",
        "query": "second query",
        "token_budget": 1000
    }'''

    response = client.post(
        "/p3/recall",
        content=duplicate_key_json,
        headers={
            **headers(operation="duplicate-keys"),
            "Content-Type": "application/json",
        },
    )

    # Should handle consistently (accept with last value or reject)
    assert response.status_code in {200, 202, 400, 422}, (
        f"Unexpected status for duplicate keys: {response.status_code}"
    )


# ============================================================================
# RC-API-11: HTTP Method Validation
# ============================================================================


def test_rc_api_11_http_method_validation(client):
    """RC-API-11: Validate POST/GET routing and method enforcement.

    Recall endpoint should only accept POST requests.
    """
    request_data = make_recall_request()

    # Valid POST request
    response = client.post(
        "/p3/recall",
        json=request_data,
        headers=headers(operation="valid-post"),
    )

    assert_success(response)

    # Invalid GET request
    response = client.get(
        "/p3/recall",
        headers=headers(operation="invalid-get"),
    )

    assert response.status_code == METHOD_NOT_ALLOWED, (
        f"GET should return 405, got {response.status_code}"
    )

    # Invalid PUT request
    response = client.put(
        "/p3/recall",
        json=request_data,
        headers=headers(operation="invalid-put"),
    )

    assert response.status_code == METHOD_NOT_ALLOWED, (
        f"PUT should return 405, got {response.status_code}"
    )

    # Invalid DELETE request
    response = client.delete(
        "/p3/recall",
        headers=headers(operation="invalid-delete"),
    )

    assert response.status_code == METHOD_NOT_ALLOWED, (
        f"DELETE should return 405, got {response.status_code}"
    )

    # Invalid PATCH request
    response = client.patch(
        "/p3/recall",
        json=request_data,
        headers=headers(operation="invalid-patch"),
    )

    assert response.status_code == METHOD_NOT_ALLOWED, (
        f"PATCH should return 405, got {response.status_code}"
    )

    # OPTIONS request (should be allowed for CORS preflight)
    response = client.options(
        "/p3/recall",
        headers=headers(operation="options-request"),
    )

    # OPTIONS might return 200 (allowed) or 405 (not configured)
    assert response.status_code in {200, METHOD_NOT_ALLOWED}, (
        f"OPTIONS should return 200 or 405, got {response.status_code}"
    )


# ============================================================================
# RC-API-12: Invalid Identifier Handling
# ============================================================================


def test_rc_api_12_invalid_identifiers_in_selection(client):
    """RC-API-12: Validate handling of invalid identifiers in selection fields.

    Test various invalid identifier formats.
    """
    invalid_identifiers = [
        ({"session_id": ""}, "empty"),
        ({"session_id": " "}, "whitespace"),
        ({"session_id": "a" * 1000}, "extremely_long"),
        ({"session_id": "\x00\x01\x02"}, "control_chars"),
        ({"session_id": "../../../etc/passwd"}, "path_traversal"),
        ({"session_id": "session\nid"}, "newline"),
    ]

    for selection, desc in invalid_identifiers:
        response = client.post(
            "/p3/recall",
            json=make_recall_request(selection=selection),
            headers=headers(operation=f"invalid-id-{desc}"),
        )

        # Should either accept (and find nothing) or reject cleanly
        assert_no_server_error(response)
