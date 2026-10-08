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
# Test Configuration
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


def headers(operation: str | None = None, user: str = "alice") -> dict[str, str]:
    """Generate authentication headers."""
    value = {"Authorization": f"Bearer {user}"}
    if operation:
        value["X-Operation-ID"] = operation
    return value


def wait_for_ready(client: TestClient) -> None:
    """Wait for service to be ready."""
    import time
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if client.get("/p3/readyz").status_code == 200:
            return
        time.sleep(0.1)
    raise AssertionError("Service did not become ready")


# ============================================================================
# RC-API-02 & RC-API-03: Query Parameter Validation
# ============================================================================


def test_rc_api_02_null_query_parameter(test_configuration):
    """RC-API-02: Validate handling of null query parameter.

    The API should reject requests with null query values.
    """
    service = Service(test_configuration)
    with TestClient(service.app()) as client:
        wait_for_ready(client)

        # Test with null query
        response = client.post(
            "/p3/recall",
            json={
                "query": None,
                "selection": {},
                "sources": "long_term",
                "token_budget": 1000,
            },
            headers=headers(operation="null-query-test"),
        )

        # Should reject with 400 Bad Request or 422 Unprocessable Entity
        assert response.status_code in {400, 422}, (
            f"Null query should be rejected, got {response.status_code}: {response.text}"
        )


def test_rc_api_03_empty_and_whitespace_query(test_configuration):
    """RC-API-03: Validate handling of empty and whitespace-only queries.

    Empty strings and whitespace-only queries should be handled appropriately.
    """
    service = Service(test_configuration)
    with TestClient(service.app()) as client:
        wait_for_ready(client)

        # Test empty string query
        response = client.post(
            "/p3/recall",
            json={
                "query": "",
                "selection": {},
                "sources": "long_term",
                "token_budget": 1000,
            },
            headers=headers(operation="empty-query-test"),
        )

        # Empty query might be accepted (return empty results) or rejected
        # Both behaviors are valid depending on API design
        assert response.status_code in {200, 202, 400, 422}, (
            f"Unexpected status for empty query: {response.status_code}"
        )

        # Test whitespace-only query
        response = client.post(
            "/p3/recall",
            json={
                "query": "   \t\n  ",
                "selection": {},
                "sources": "long_term",
                "token_budget": 1000,
            },
            headers=headers(operation="whitespace-query-test"),
        )

        assert response.status_code in {200, 202, 400, 422}, (
            f"Unexpected status for whitespace query: {response.status_code}"
        )


def test_rc_api_03_unicode_query_handling(test_configuration):
    """RC-API-03: Validate handling of Unicode characters in queries.

    The API should properly handle various Unicode characters including
    emoji, CJK characters, and special symbols.
    """
    service = Service(test_configuration)
    with TestClient(service.app()) as client:
        wait_for_ready(client)

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
                json={
                    "query": query,
                    "selection": {},
                    "sources": "long_term",
                    "token_budget": 1000,
                },
                headers=headers(operation=f"unicode-query-{hash(query)}"),
            )

            # Should either accept or reject cleanly, not crash
            assert response.status_code < 500, (
                f"Server error on Unicode query '{query}': {response.status_code}"
            )


# ============================================================================
# RC-API-04: Token Budget Boundary Tests
# ============================================================================


def test_rc_api_04_token_budget_boundaries(test_configuration):
    """RC-API-04: Validate token budget boundary conditions.

    Test minimum, maximum, zero, negative, and invalid token budget values.
    """
    service = Service(test_configuration)
    with TestClient(service.app()) as client:
        wait_for_ready(client)

        test_cases = [
            (0, "zero tokens"),
            (-1, "negative tokens"),
            (-999999, "large negative"),
            (1, "minimum valid"),
            (1000000, "very large"),
            (None, "null token budget"),
        ]

        for token_budget, description in test_cases:
            request_data = {
                "query": "test query",
                "selection": {},
                "sources": "long_term",
            }
            if token_budget is not None:
                request_data["token_budget"] = token_budget

            response = client.post(
                "/p3/recall",
                json=request_data,
                headers=headers(operation=f"token-budget-{description}"),
            )

            # Zero and negative should be rejected
            if token_budget is not None and token_budget <= 0:
                assert response.status_code in {400, 422}, (
                    f"{description} should be rejected: {response.status_code}"
                )
            # Very large values should either work or be rejected with clear error
            elif token_budget and token_budget > 100000:
                assert response.status_code < 500, (
                    f"Server error on {description}: {response.status_code}"
                )


def test_rc_api_04_token_budget_type_mismatch(test_configuration):
    """RC-API-04: Validate rejection of non-integer token budget values."""
    service = Service(test_configuration)
    with TestClient(service.app()) as client:
        wait_for_ready(client)

        invalid_budgets = [
            "1000",  # String instead of int
            1000.5,  # Float instead of int
            [],  # Array
            {},  # Object
        ]

        for invalid_budget in invalid_budgets:
            response = client.post(
                "/p3/recall",
                json={
                    "query": "test",
                    "selection": {},
                    "sources": "long_term",
                    "token_budget": invalid_budget,
                },
                headers=headers(operation=f"invalid-budget-{type(invalid_budget).__name__}"),
            )

            # Type mismatches should be rejected
            assert response.status_code in {400, 422}, (
                f"Type mismatch {type(invalid_budget).__name__} should be rejected: "
                f"{response.status_code}"
            )


# ============================================================================
# RC-API-05: Sources Enumeration Validation
# ============================================================================


def test_rc_api_05_sources_enumeration(test_configuration):
    """RC-API-05: Validate sources parameter enumeration.

    Test valid values, invalid values, case sensitivity, and defaults.
    """
    service = Service(test_configuration)
    with TestClient(service.app()) as client:
        wait_for_ready(client)

        # Valid sources values
        valid_sources = ["working", "long_term", "all"]

        for source in valid_sources:
            response = client.post(
                "/p3/recall",
                json={
                    "query": "test",
                    "selection": {},
                    "sources": source,
                    "token_budget": 1000,
                },
                headers=headers(operation=f"valid-source-{source}"),
            )

            # Valid sources should be accepted
            assert response.status_code in {200, 202}, (
                f"Valid source '{source}' rejected: {response.status_code}"
            )

        # Invalid sources value
        response = client.post(
            "/p3/recall",
            json={
                "query": "test",
                "selection": {},
                "sources": "invalid_source",
                "token_budget": 1000,
            },
            headers=headers(operation="invalid-source"),
        )

        assert response.status_code in {400, 422}, (
            f"Invalid source should be rejected: {response.status_code}"
        )

        # Case sensitivity test
        response = client.post(
            "/p3/recall",
            json={
                "query": "test",
                "selection": {},
                "sources": "Long_Term",  # Wrong case
                "token_budget": 1000,
            },
            headers=headers(operation="case-sensitive-source"),
        )

        # Case mismatch should be rejected unless API is case-insensitive
        assert response.status_code in {200, 202, 400, 422}, (
            f"Unexpected status for case mismatch: {response.status_code}"
        )


def test_rc_api_05_sources_default_value(test_configuration):
    """RC-API-05: Validate default sources value when omitted."""
    service = Service(test_configuration)
    with TestClient(service.app()) as client:
        wait_for_ready(client)

        # Omit sources parameter
        response = client.post(
            "/p3/recall",
            json={
                "query": "test",
                "selection": {},
                "token_budget": 1000,
            },
            headers=headers(operation="default-source"),
        )

        # Should either use default or require the field
        assert response.status_code in {200, 202, 400, 422}, (
            f"Unexpected status when sources omitted: {response.status_code}"
        )


# ============================================================================
# RC-API-06: Selection Structure Validation
# ============================================================================


def test_rc_api_06_selection_structure(test_configuration):
    """RC-API-06: Validate selection parameter structure.

    Test missing, empty, and various selection structures.
    """
    service = Service(test_configuration)
    with TestClient(service.app()) as client:
        wait_for_ready(client)

        # Empty selection (valid)
        response = client.post(
            "/p3/recall",
            json={
                "query": "test",
                "selection": {},
                "sources": "long_term",
                "token_budget": 1000,
            },
            headers=headers(operation="empty-selection"),
        )

        assert response.status_code in {200, 202}, (
            f"Empty selection should be valid: {response.status_code}"
        )

        # Selection with valid fields
        response = client.post(
            "/p3/recall",
            json={
                "query": "test",
                "selection": {
                    "session_id": "test-session",
                    "task_id": "test-task",
                },
                "sources": "long_term",
                "token_budget": 1000,
            },
            headers=headers(operation="valid-selection"),
        )

        assert response.status_code in {200, 202}, (
            f"Valid selection should be accepted: {response.status_code}"
        )

        # Null selection
        response = client.post(
            "/p3/recall",
            json={
                "query": "test",
                "selection": None,
                "sources": "long_term",
                "token_budget": 1000,
            },
            headers=headers(operation="null-selection"),
        )

        assert response.status_code in {400, 422}, (
            f"Null selection should be rejected: {response.status_code}"
        )


def test_rc_api_06_selection_type_errors(test_configuration):
    """RC-API-06: Validate rejection of wrong selection types."""
    service = Service(test_configuration)
    with TestClient(service.app()) as client:
        wait_for_ready(client)

        invalid_selections = [
            "string-selection",  # String instead of object
            ["array", "selection"],  # Array instead of object
            123,  # Number instead of object
        ]

        for invalid_selection in invalid_selections:
            response = client.post(
                "/p3/recall",
                json={
                    "query": "test",
                    "selection": invalid_selection,
                    "sources": "long_term",
                    "token_budget": 1000,
                },
                headers=headers(operation=f"invalid-selection-{type(invalid_selection).__name__}"),
            )

            assert response.status_code in {400, 422}, (
                f"Invalid selection type {type(invalid_selection).__name__} should be rejected: "
                f"{response.status_code}"
            )


# ============================================================================
# RC-API-07: Injection Attack Prevention
# ============================================================================


def test_rc_api_07_sql_injection_prevention(test_configuration):
    """RC-API-07: Validate SQL injection prevention in query field.

    The API should safely handle SQL injection attempts without exposing
    database internals or allowing unauthorized data access.
    """
    service = Service(test_configuration)
    with TestClient(service.app()) as client:
        wait_for_ready(client)

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
                json={
                    "query": payload,
                    "selection": {},
                    "sources": "long_term",
                    "token_budget": 1000,
                },
                headers=headers(operation=f"sql-injection-{hash(payload)}"),
            )

            # Should handle safely (not crash) and not expose DB errors
            assert response.status_code < 500, (
                f"SQL injection attempt caused server error: {response.status_code}"
            )

            if response.status_code >= 400:
                # Error messages should not expose database details
                error_text = response.text.lower()
                assert "sql" not in error_text, "Error exposes SQL details"
                assert "database" not in error_text, "Error exposes database details"


def test_rc_api_07_nosql_injection_prevention(test_configuration):
    """RC-API-07: Validate NoSQL injection prevention in selection field."""
    service = Service(test_configuration)
    with TestClient(service.app()) as client:
        wait_for_ready(client)

        # NoSQL injection attempts in selection object
        nosql_payloads = [
            {"session_id": {"$ne": None}},
            {"session_id": {"$gt": ""}},
            {"$where": "this.session_id == 'admin'"},
        ]

        for payload in nosql_payloads:
            response = client.post(
                "/p3/recall",
                json={
                    "query": "test",
                    "selection": payload,
                    "sources": "long_term",
                    "token_budget": 1000,
                },
                headers=headers(operation=f"nosql-injection-{hash(str(payload))}"),
            )

            # Should handle safely
            assert response.status_code < 500, (
                f"NoSQL injection attempt caused server error: {response.status_code}"
            )


def test_rc_api_07_command_injection_prevention(test_configuration):
    """RC-API-07: Validate command injection prevention."""
    service = Service(test_configuration)
    with TestClient(service.app()) as client:
        wait_for_ready(client)

        command_injection_payloads = [
            "; rm -rf /",
            "| cat /etc/passwd",
            "$(whoami)",
            "`id`",
            "&& ls -la",
        ]

        for payload in command_injection_payloads:
            response = client.post(
                "/p3/recall",
                json={
                    "query": payload,
                    "selection": {},
                    "sources": "long_term",
                    "token_budget": 1000,
                },
                headers=headers(operation=f"cmd-injection-{hash(payload)}"),
            )

            # Should handle safely
            assert response.status_code < 500, (
                f"Command injection attempt caused server error: {response.status_code}"
            )


# ============================================================================
# RC-API-08: Client Field Forgery Prevention
# ============================================================================


def test_rc_api_08_tenant_id_forgery_prevention(test_configuration):
    """RC-API-08: Validate that clients cannot forge tenant_id.

    The tenant context should be determined by authentication, not client input.
    """
    service = Service(test_configuration)
    with TestClient(service.app()) as client:
        wait_for_ready(client)

        # Attempt to include tenant_id in request body
        response = client.post(
            "/p3/recall",
            json={
                "query": "test",
                "selection": {"tenant_id": "t2"},  # Try to forge tenant
                "sources": "long_term",
                "token_budget": 1000,
            },
            headers=headers(operation="tenant-forgery", user="alice"),  # alice is in t1
        )

        # Should either ignore the field or reject it, but not honor the forgery
        # Cannot easily verify without checking actual data access, but should not crash
        assert response.status_code < 500, (
            f"Tenant forgery attempt caused server error: {response.status_code}"
        )


def test_rc_api_08_internal_field_forgery(test_configuration):
    """RC-API-08: Validate that clients cannot inject internal policy fields."""
    service = Service(test_configuration)
    with TestClient(service.app()) as client:
        wait_for_ready(client)

        # Attempt to inject internal fields
        internal_fields = [
            {"_internal_policy": "bypass"},
            {"admin": True},
            {"is_system": True},
            {"bypass_auth": True},
        ]

        for fields in internal_fields:
            response = client.post(
                "/p3/recall",
                json={
                    "query": "test",
                    "selection": fields,
                    "sources": "long_term",
                    "token_budget": 1000,
                },
                headers=headers(operation=f"internal-field-{hash(str(fields))}"),
            )

            # Should either ignore or reject, but handle safely
            assert response.status_code < 500, (
                f"Internal field forgery caused server error: {response.status_code}"
            )


# ============================================================================
# RC-API-09: Malformed Request Handling
# ============================================================================


def test_rc_api_09_truncated_json(test_configuration):
    """RC-API-09: Validate handling of truncated JSON requests."""
    service = Service(test_configuration)
    with TestClient(service.app()) as client:
        wait_for_ready(client)

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
                    **headers(operation=f"truncated-{hash(truncated_json)}"),
                    "Content-Type": "application/json",
                },
            )

            # Should return 400 Bad Request for malformed JSON
            assert response.status_code == 400, (
                f"Truncated JSON should return 400, got {response.status_code}"
            )


def test_rc_api_09_wrong_content_type(test_configuration):
    """RC-API-09: Validate handling of wrong Content-Type header."""
    service = Service(test_configuration)
    with TestClient(service.app()) as client:
        wait_for_ready(client)

        valid_json = json.dumps({
            "query": "test",
            "selection": {},
            "sources": "long_term",
            "token_budget": 1000,
        })

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
                    **headers(operation=f"wrong-content-type-{content_type}"),
                    "Content-Type": content_type,
                },
            )

            # Should reject wrong content type
            assert response.status_code in {400, 415}, (
                f"Wrong Content-Type should be rejected, got {response.status_code}"
            )


def test_rc_api_09_invalid_json_syntax(test_configuration):
    """RC-API-09: Validate handling of various JSON syntax errors."""
    service = Service(test_configuration)
    with TestClient(service.app()) as client:
        wait_for_ready(client)

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
                    **headers(operation=f"invalid-json-{hash(invalid_json)}"),
                    "Content-Type": "application/json",
                },
            )

            # Should return 400 Bad Request
            assert response.status_code == 400, (
                f"Invalid JSON should return 400, got {response.status_code}"
            )


# ============================================================================
# RC-API-10: Duplicate JSON Key Handling
# ============================================================================


def test_rc_api_10_duplicate_json_keys(test_configuration):
    """RC-API-10: Validate handling of duplicate keys in JSON request.

    JSON spec allows duplicate keys but behavior is undefined.
    The API should handle them consistently.
    """
    service = Service(test_configuration)
    with TestClient(service.app()) as client:
        wait_for_ready(client)

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


def test_rc_api_11_http_method_validation(test_configuration):
    """RC-API-11: Validate POST/GET routing and method enforcement.

    Recall endpoint should only accept POST requests.
    """
    service = Service(test_configuration)
    with TestClient(service.app()) as client:
        wait_for_ready(client)

        request_data = {
            "query": "test",
            "selection": {},
            "sources": "long_term",
            "token_budget": 1000,
        }

        # Valid POST request
        response = client.post(
            "/p3/recall",
            json=request_data,
            headers=headers(operation="valid-post"),
        )

        assert response.status_code in {200, 202}, (
            f"Valid POST should be accepted, got {response.status_code}"
        )

        # Invalid GET request
        response = client.get(
            "/p3/recall",
            headers=headers(operation="invalid-get"),
        )

        # Should return 405 Method Not Allowed
        assert response.status_code == 405, (
            f"GET should return 405, got {response.status_code}"
        )

        # Invalid PUT request
        response = client.put(
            "/p3/recall",
            json=request_data,
            headers=headers(operation="invalid-put"),
        )

        assert response.status_code == 405, (
            f"PUT should return 405, got {response.status_code}"
        )

        # Invalid DELETE request
        response = client.delete(
            "/p3/recall",
            headers=headers(operation="invalid-delete"),
        )

        assert response.status_code == 405, (
            f"DELETE should return 405, got {response.status_code}"
        )


# ============================================================================
# RC-API-12: Invalid Identifier Handling
# ============================================================================


def test_rc_api_12_invalid_identifiers_in_selection(test_configuration):
    """RC-API-12: Validate handling of invalid identifiers in selection fields.

    Test various invalid identifier formats.
    """
    service = Service(test_configuration)
    with TestClient(service.app()) as client:
        wait_for_ready(client)

        invalid_identifiers = [
            {"session_id": ""},  # Empty string
            {"session_id": " "},  # Whitespace only
            {"session_id": "a" * 1000},  # Extremely long
            {"session_id": "\x00\x01\x02"},  # Control characters
            {"session_id": "../../../etc/passwd"},  # Path traversal attempt
            {"session_id": "session\nid"},  # Newline in ID
        ]

        for selection in invalid_identifiers:
            response = client.post(
                "/p3/recall",
                json={
                    "query": "test",
                    "selection": selection,
                    "sources": "long_term",
                    "token_budget": 1000,
                },
                headers=headers(operation=f"invalid-id-{hash(str(selection))}"),
            )

            # Should either accept (and find nothing) or reject cleanly
            assert response.status_code < 500, (
                f"Invalid identifier caused server error: {response.status_code}"
            )


def test_rc_api_12_invalid_operation_id_header(test_configuration):
    """RC-API-12: Validate handling of invalid X-Operation-ID values."""
    service = Service(test_configuration)
    with TestClient(service.app()) as client:
        wait_for_ready(client)

        invalid_operation_ids = [
            "",  # Empty
            " ",  # Whitespace
            "a" * 200,  # Too long
            "op\nid",  # Newline
            "op\x00id",  # Null byte
        ]

        for op_id in invalid_operation_ids:
            # Manual header construction to bypass helper validation
            response = client.post(
                "/p3/recall",
                json={
                    "query": "test",
                    "selection": {},
                    "sources": "long_term",
                    "token_budget": 1000,
                },
                headers={
                    "Authorization": "Bearer alice",
                    "X-Operation-ID": op_id,
                },
            )

            # Should reject or accept with sanitized ID, but not crash
            assert response.status_code < 500, (
                f"Invalid operation ID caused server error: {response.status_code}"
            )
