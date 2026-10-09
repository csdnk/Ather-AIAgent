# Spec: Recall API Contract Tests

## Problem Statement

The Recall API needs comprehensive contract validation tests to ensure that the HTTP interface correctly handles various input scenarios including valid requests, invalid inputs, edge cases, malformed data, and security boundary conditions. Without these tests, we cannot guarantee that the API properly validates inputs, rejects malicious requests, and provides appropriate error responses.

## Solution

Implement a comprehensive test suite (RC-API-01 through RC-API-12) that validates the Recall API's request/response contracts, covering:
- End-to-end successful request validation
- Query parameter validation (null, empty, type mismatches)
- Unicode and multilingual input handling
- Token budget boundary conditions
- Source enumeration validation
- Selection structure validation
- Injection attack prevention
- Internal field protection
- Malformed request handling
- JSON key duplication handling
- HTTP method/route validation
- Invalid identifier handling

## User Stories

1. As a Recall API consumer, I want to successfully submit a valid recall request with proper authentication, so that I can retrieve relevant memories
2. As a system integrator, I want the API to reject null query values with clear error messages, so that I can fix integration issues quickly
3. As a system integrator, I want the API to reject empty query strings appropriately, so that I don't waste computational resources
4. As a developer, I want to know if whitespace-only queries are accepted or rejected, so that I can handle user input correctly
5. As a system integrator, I want the API to reject non-string query types (numbers, arrays), so that type errors are caught early
6. As an international user, I want to submit queries in Chinese, English, or mixed languages, so that I can search in my preferred language
7. As an international user, I want emoji and Unicode composition characters to be handled correctly, so that my queries aren't corrupted
8. As a developer, I want query length to be measured in tokens not bytes, so that multilingual queries are treated fairly
9. As an API consumer, I want to know the minimum and maximum allowed token_budget values, so that I can set appropriate limits
10. As a system administrator, I want token_budget validation to prevent integer overflow, so that the system remains stable
11. As a developer, I want to understand how the API handles token_budget edge values (min-1, min, min+1, max-1, max, max+1), so that I can test boundaries
12. As an API consumer, I want the API to reject invalid token_budget types (strings, booleans, floats), so that I get immediate feedback
13. As a developer, I want to know valid source enumeration values, so that I can use them correctly
14. As an API consumer, I want the API to reject unknown source values rather than silently converting them, so that typos are caught
15. As a system integrator, I want to understand case-sensitivity rules for source values, so that I can normalize inputs
16. As a developer, I want to know how the API handles missing or null source parameters, so that I can implement proper defaults
17. As an API consumer, I want to understand selection structure requirements, so that I can construct valid requests
18. As a developer, I want the API to validate selection field types, so that type errors are caught at admission
19. As a security engineer, I want the API to treat selection as scope-narrowing only, so that clients cannot escalate privileges
20. As a developer, I want to know how empty selection objects are interpreted, so that I can use them appropriately
21. As a security engineer, I want query strings containing quotes, SQL syntax, or filter expressions to be treated as literal data, so that injection attacks fail
22. As a security engineer, I want selection parameters to be escaped properly, so that they cannot be used to bypass authorization
23. As a system administrator, I want error messages to not leak database connection strings, so that internal infrastructure remains protected
24. As a security engineer, I want the API to reject cross-tenant queries regardless of how cleverly they're disguised, so that tenant isolation is maintained
25. As a security engineer, I want the API to ignore client-provided tenant_id fields, so that clients cannot impersonate other tenants
26. As a security engineer, I want the API to reject client-provided K_memory values, so that clients cannot bypass server-side limits
27. As a security engineer, I want the API to ignore client-provided strategy version fields, so that clients cannot downgrade security policies
28. As a developer, I want the API to reject or ignore unknown JSON fields according to a clear policy, so that forward compatibility is maintained
29. As a system integrator, I want the API to handle truncated JSON gracefully with proper error codes, so that I can detect transmission issues
30. As a developer, I want the API to reject requests with incorrect Content-Type headers, so that parsing errors are avoided
31. As a system integrator, I want the API to handle missing request bodies appropriately, so that I can detect client bugs
32. As a security engineer, I want the API to reject deeply nested JSON to prevent stack overflow attacks, so that the system remains available
33. As a developer, I want to understand how the API handles duplicate JSON keys, so that I can avoid ambiguous requests
34. As a system integrator, I want the idempotency signature to bind to actual execution semantics, so that duplicate key order doesn't affect behavior unpredictably
35. As an API consumer, I want to use POST /p3/recall to submit new requests, so that I follow RESTful conventions
36. As an API consumer, I want to use GET /p3/recalls/{id} to retrieve recall records, so that I can check status
37. As an API consumer, I want to use GET /p3/recalls/{id}/result to retrieve results, so that I can access completed recalls
38. As a developer, I want GET requests to never trigger recall re-execution, so that reads are idempotent
39. As a system integrator, I want unsupported HTTP methods to return proper error codes without side effects, so that I can detect client bugs
40. As an API consumer, I want to receive appropriate errors for non-existent recall IDs, so that I can handle missing resources
41. As a developer, I want the API to validate recall ID format, so that malformed IDs are rejected early
42. As a security engineer, I want the API to not leak information about other users' recalls through ID guessing, so that privacy is maintained
43. As a system administrator, I want consistent error responses for "not found" vs "unauthorized" according to the API policy, so that information disclosure is controlled

## Implementation Decisions

### Test Infrastructure
- **Test framework**: pytest with pytest-asyncio for all async test functions
- **Test client**: `RecallTestClient` class in `tests/recall/client.py` that wraps HTTP calls and automatically captures requests/responses for evidence
- **Test organization**: Single file `tests/recall/test_api_contract.py` containing all 12 API contract tests (RC-API-01 through RC-API-12)
- **Test markers**: Each test tagged with `@pytest.mark.api_contract` and appropriate priority marker (`@pytest.mark.p0` or `@pytest.mark.p1`)

### Test Fixtures
- **Data fixtures**: Use `f1_working_ready` fixture (Working memories: coffee + tea, v1/g1 Ready state)
- **Identity fixtures**: Use `u01_tenant_a_read` fixture (Tenant A user with READ permission)
- **Configuration**: Use layered config (defaults → YAML file → env vars → CLI args) for API endpoints and timeouts

### Parametrization Strategy
- **RC-API-02** (query validation): Parametrize over query variants (None, "", whitespace, number, array) with descriptive IDs
- **RC-API-04** (token budget): Parametrize over boundary values (m-1, m, m+1, M-1, M, M+1) and invalid types
- **RC-API-05** (sources): Parametrize over invalid source values (null, unknown, uppercase, array)
- Use `pytest.mark.parametrize` with explicit `ids` parameter for clear test output

### Contract Validation
- Tests verify HTTP status codes, error response structure, and error messages
- Use assertion helpers: `assert_successful_recall()`, `assert_authorization_denied()`
- Token counting uses actual production tokenizer for dynamic calculation
- Unicode tests verify both input integrity and semantic retrieval (no normalization requirements)

### Security Testing
- Injection tests (RC-API-07) use safe canary data in isolated test tenant
- Verify filter plans, returned IDs, and database query logs don't show anomalies
- Field injection tests (RC-API-08) verify that client-provided internal fields are ignored or rejected
- Error messages verified to not leak DSN, internal IDs, or stack traces

### Edge Case Handling
- Malformed JSON tests use test endpoints only (not production)
- Truncation, wrong Content-Type, missing body all tested separately
- Nesting depth limits verified if specified in schema
- Duplicate key handling awaits specification (blocked by Q01, will skip with marker)

### HTTP Routing
- Tests verify all three documented routes: POST /p3/recall, GET /p3/recalls/{id}, GET /p3/recalls/{id}/result
- Wrong HTTP methods tested to ensure no side effects (GET on POST endpoint should not execute)
- Invalid/non-existent recall IDs tested for proper error responses

### Blocked Requirements
- Several test aspects blocked awaiting specification (tracked in `blocked_requirements.yaml`):
  - Q01: token_budget min/max values, selection defaults, HTTP mappings, operation ID format
  - Tests with blocked aspects will be skipped automatically with clear skip messages

## Testing Decisions

### What Makes a Good Test
- Tests verify **external API behavior** (HTTP status, response structure, error messages), not internal implementation
- Tests use **real dependencies** (actual API endpoints, real tokenizer for token counting)
- Each test is **self-contained** and creates its own preconditions using fixtures
- Tests verify **security properties** explicitly (no information leakage, proper rejection)

### Test Structure Pattern
```
@pytest.mark.p0
@pytest.mark.api_contract
async def test_rc_api_01_working_request_e2e(
    recall_client: RecallTestClient,
    f1_working_ready: Fixture,
    u01_tenant_a_read: Identity
):
    """RC-API-01: Working request end-to-end verification.
    
    Spec: docs/refs/Recall_测试用例_精简版.md#case-rc-api-01
    """
    # Prepare request
    # Submit recall
    # Wait for completion
    # Verify result
    # Verify stages (if diagnostic access needed)
```

### Modules to Test
- **Primary**: Recall HTTP API endpoints (`POST /p3/recall`, `GET /p3/recalls/{id}`, `GET /p3/recalls/{id}/result`)
- **Secondary**: Request validation logic, error response formatting
- **Diagnostic**: Stage execution verification for P0 tests that need internal evidence

### Prior Art
- Similar API contract tests may exist in Remember or Operate test suites
- HTTP client testing patterns from existing integration test suites
- Parametrization patterns from existing test files using `pytest.mark.parametrize`

### Evidence Collection
- Automatic evidence collection on test failure via pytest plugin
- Captures: HTTP requests/responses, timestamps (UTC + Shanghai), recall IDs, operation IDs
- Evidence saved to `test_results/evidence/{test_id}_{timestamp}.json`
- Sensitive data automatically redacted (Bearer tokens masked, tenant IDs hashed)

## Out of Scope

The following are explicitly out of scope for API contract tests:
- **Business logic verification**: Detailed verification of recall quality, ranking accuracy, or memory relevance (covered by other test categories)
- **Concurrent behavior**: Idempotency and concurrent admission (covered by RC-IDE tests)
- **Authorization logic**: Cross-tenant isolation, permission enforcement (covered by RC-AUTH tests)
- **Source selection**: Working vs long-term source behavior (covered by RC-SRC tests)
- **Performance testing**: Response time, throughput, or load testing
- **Remember integration**: How Remember stores/retrieves memories (Remember's responsibility)
- **Vector search quality**: Accuracy of vector similarity search (covered by RC-SEA tests)

## Further Notes

### Test Execution
- API contract tests can run independently without dependencies on other test categories
- Tests require: running Recall API endpoint, working Remember service (for fixture setup), Milvus (for vector indexing)
- Estimated execution time: ~2-3 minutes for all 12 tests (including fixture setup)

### Specification Dependencies
- Several tests blocked pending schema clarification (Q01 in blocked_requirements.yaml)
- When schema is finalized, update blocked_requirements.yaml and remove skip markers
- Unicode handling tests (RC-API-03) serve as documentation of actual behavior, not requirements for specific normalization

### Maintenance
- When API schema changes, update test expectations accordingly
- When new query parameters added, add corresponding validation tests
- When error response format changes, update assertion helpers in `tests/recall/assertions.py`

### Related Specs
- **Idempotency Tests**: Verify operation ID handling and retry behavior
- **Authorization Tests**: Verify security boundaries and tenant isolation
- **All Other Categories**: Assume valid API contracts; focus on business logic
