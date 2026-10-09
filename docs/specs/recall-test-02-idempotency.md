# Spec: Recall Idempotency and Admission Tests

## Problem Statement

The Recall system must handle duplicate requests, retries, and concurrent submissions correctly to ensure reliable operation in distributed systems. Without proper idempotency guarantees, clients may receive duplicate results, waste computational resources, or experience inconsistent behavior when retrying failed requests. The admission system must correctly identify duplicate operations, handle concurrent submissions, and maintain result integrity across different failure scenarios.

## Solution

Implement comprehensive idempotency tests (RC-IDE-01 through RC-IDE-08) that verify:
- Serial retry behavior with same operation ID
- Conflict detection for same ID with different request bodies
- Concurrent admission handling
- Cross-principal operation ID namespacing
- Operation ID header validation
- Response loss recovery
- Authorization changes during replay
- JSON normalization for idempotency signatures

## User Stories

1. As an API client, I want to safely retry failed requests using the same operation ID, so that I don't create duplicate recalls
2. As a system operator, I want retries to return the original result without re-execution, so that computational resources aren't wasted
3. As a security engineer, I want retry authorization to be checked at access time, so that revoked permissions are enforced even on cached results
4. As an API client, I want an error when I reuse an operation ID with different request parameters, so that I can detect programming bugs
5. As a developer, I want idempotency conflicts to prevent state corruption, so that the system remains consistent
6. As a system operator, I want concurrent submissions with the same operation ID to be deduplicated, so that only one execution occurs
7. As an API client, I want concurrent retries to wait for the in-progress operation, so that I receive the result when it completes
8. As a developer, I want concurrent requests to not generate multiple successful packs, so that downstream systems see consistent results
9. As a multi-tenant system operator, I want operation IDs to be namespaced per tenant, so that tenants can't observe each other's operations
10. As a security engineer, I want operation ID namespacing to prevent cross-tenant information disclosure, so that privacy is maintained
11. As a developer, I want to understand whether operation IDs must be globally unique or tenant-scoped, so that I can generate them correctly
12. As an API client, I want clear error messages when operation ID format is invalid, so that I can fix the issue
13. As a developer, I want to know if operation IDs are required or can be auto-generated, so that I can implement proper client behavior
14. As a system operator, I want to understand how duplicate operation ID headers are handled, so that I can troubleshoot client issues
15. As an API client recovering from network failure, I want to query operation status after response loss, so that I can determine what happened
16. As an API client, I want to safely retry after response loss using the same operation ID, so that I don't duplicate work
17. As a system operator, I want operations to be recoverable after response loss, so that transient network issues don't cause data loss
18. As a developer, I want response loss to not be counted as "client acknowledged", so that authorization checks are still enforced on result retrieval
19. As a security engineer, I want permission revocation to take effect even for completed operations, so that access control remains current
20. As an API client, I want to not receive results for operations where my permissions were revoked, so that I don't access unauthorized data
21. As a system operator, I want idempotent replay after revocation to not leak old content, so that security boundaries are maintained
22. As a developer, I want to know whether idempotency signatures canonicalize JSON, so that I can format requests consistently
23. As an API client, I want whitespace and field order differences to not affect idempotency, so that JSON serialization libraries don't cause duplicate executions
24. As a developer, I want to know how Unicode normalization affects idempotency, so that I can handle international text correctly
25. As a system operator, I want idempotency signatures to bind to actual execution semantics, so that different inputs don't accidentally match

## Implementation Decisions

### Test Infrastructure
- **Test framework**: pytest with pytest-asyncio for async operations
- **Test file**: `tests/recall/test_idempotency.py` containing all 8 idempotency tests (RC-IDE-01 through RC-IDE-08)
- **Test markers**: Each test tagged with `@pytest.mark.idempotency` and priority (`@pytest.mark.p0` or `@pytest.mark.p1`)
- **Concurrency tests**: Additional `@pytest.mark.concurrent` marker for tests involving parallel execution

### Test Fixtures
- **Data fixtures**: Use `f1_working_ready` fixture (Working memories: coffee + tea, v1/g1 Ready)
- **Identity fixtures**: 
  - `u01_tenant_a_read` for primary tenant
  - `u04_tenant_b_read` for cross-tenant tests
  - `u02_tenant_a_different_user` for same-tenant different-user tests
- **Barrier fixtures**: Use barrier registry for concurrent admission tests

### Idempotency Testing Strategy
- **Operation ID generation**: Tests use explicit operation IDs with test-specific prefixes (e.g., `test-rc-ide-01-{uuid}`)
- **Request hashing**: Use production idempotency signature calculation (fingerprint function from `recall.py`)
- **Result comparison**: Compare `recall_id`, `pack_hash`, and `rendered_context` for exact match verification
- **Event counting**: Use diagnostic client to verify no duplicate `packed` events

### Concurrent Testing Implementation
- **Synchronization**: Use `asyncio.Event` barriers via `BarrierRegistry` to coordinate concurrent submissions
- **Barrier points**: Register barrier at `B0` (before candidates stage) to ensure both clients reach admission before either proceeds
- **Execution pattern**:
  1. Set up barrier before candidates stage
  2. Launch both clients with `asyncio.create_task`
  3. Brief sleep to ensure both reach barrier
  4. Release barrier
  5. Verify both clients reference same operation

### Response Loss Simulation
- **HTTP layer interception**: Intercept response after commit stage (B7) but before HTTP delivery
- **Test sequence**:
  1. Submit operation
  2. Intercept and drop response at HTTP layer
  3. Query operation status via job/recall ID
  4. Retry with same operation ID
  5. Verify original result preserved

### Authorization Change Testing
- **Permission manipulation**: Use `IdentityFactory.revoke_permission()` to increment auth_epoch
- **Wait for propagation**: Use helper to wait for authority receipt after permission changes
- **Verification points**:
  - Idempotent POST after revocation doesn't leak old content
  - GET result after revocation enforces current permissions
  - Internal read events may be preserved (but not delivered to client)

### JSON Canonicalization
- **Field order test**: Submit identical semantics with different field ordering
- **Whitespace test**: Submit with different indentation/spacing
- **Unicode test**: Submit with different Unicode normalization forms (NFC vs NFD)
- **Blocked pending spec**: Q07 defines exact canonicalization rules - tests will skip variants until specified

### Cross-Principal Isolation
- **Tenant isolation**: Same operation ID used by different tenants must remain isolated
- **User isolation**: Same operation ID used by different users in same tenant (isolation policy TBD by Q07)
- **Verification**: Each principal receives only their own results, no metadata leakage

## Testing Decisions

### What Makes a Good Test
- Tests verify **observable idempotency guarantees** (same operation ID + same body → same result)
- Tests use **real concurrent execution** (not mocked) to catch race conditions
- Tests verify **no side effects** (duplicate executions, duplicate events, wasted resources)
- Tests verify **security properties** (authorization re-checked on replay, no leakage after revocation)

### Test Structure Patterns

**Basic Idempotency Test**:
```
async def test_rc_ide_01_serial_retry():
    # Submit initial request with operation ID
    # Wait for completion
    # Retry with same operation ID and body (serial)
    # Verify same recall_id returned
    # Verify result identical
    # Verify no duplicate packed events
```

**Concurrent Admission Test**:
```
async def test_rc_ide_03_concurrent_admission():
    # Set up barrier at B0 (before candidates)
    # Launch two clients with same operation ID + body
    # Wait for both to reach barrier
    # Release barrier
    # Verify both reference same recall_id
    # Verify single execution (one packed event)
```

**Authorization Change Test**:
```
async def test_rc_ide_07_revocation_replay():
    # Submit and complete recall (save result)
    # Revoke permissions and wait for authority receipt
    # Retry with same operation ID
    # Verify no old content leaked
    # GET result with revoked identity
    # Verify access denied
```

### Modules to Test
- **Primary**: `RecallAdmission.accept()` method for idempotency logic
- **Secondary**: Operation ID handling, request signature calculation
- **Diagnostic**: Task/event system to verify single execution
- **Authorization**: Identity validation on replay and result retrieval

### Assertion Helpers
- **Idempotency assertions**: `assert_idempotent_retry(first_response, second_response, first_result, second_result)`
  - Verifies same recall_id
  - Verifies same pack_hash
  - Verifies identical rendered_context
- **Authorization assertions**: `assert_authorization_denied(response, should_not_leak=["memory_content"])`
- **Event counting**: Use `DiagnosticClient.get_events()` to verify event uniqueness

### Prior Art
- Similar idempotency patterns may exist in Remember or Operate admission logic
- Concurrent testing patterns from existing integration tests
- Authorization replay patterns from security test suites

### Evidence Collection
- All test failures automatically collect evidence including:
  - Both request/response pairs (original and retry)
  - Operation IDs and recall IDs
  - Execution traces showing stage progression
  - Event logs showing packed events
  - Authorization state before/after revocation

## Out of Scope

The following are explicitly out of scope for idempotency tests:
- **Content correctness**: Verifying that recalled memories are semantically correct (covered by other test categories)
- **Performance**: Idempotency overhead, response time for retries
- **Authorization logic details**: How permissions are granted/revoked (Remember's responsibility)
- **Conflict resolution**: What happens when operation ID conflicts are detected (tested, but resolution policy is API contract decision)
- **Operation ID format**: Exact format requirements (blocked by Q01, will be tested when specified)
- **Cross-service idempotency**: How Recall idempotency relates to Remember or Operate operations

## Further Notes

### Blocked Requirements
- **Q07**: Idempotency signature details (JSON canonicalization, Unicode normalization, field ordering)
  - Tests RC-IDE-08 (JSON expression differences) will be partially skipped
  - Tests RC-API-10 (duplicate JSON keys) also affected
- **Q01**: Operation ID format and generation rules
  - Tests RC-IDE-05 (operation ID header validation) will be partially skipped

### Temporal Integration
- Some idempotency behavior may depend on Temporal workflow deduplication
- Tests should verify application-level idempotency independent of Temporal guarantees
- Barrier synchronization may use Temporal signals for workflow-level coordination

### Retry Strategy Guidance
Tests document proper retry behavior for clients:
1. Always use same operation ID for retries of same logical operation
2. Generate new operation ID for new logical operation even with same parameters
3. Don't assume response loss means operation didn't complete
4. Check operation status before retry if uncertain

### Concurrent Admission Performance
- Tests verify correctness (single execution) not performance
- Actual concurrent handling may use advisory locks, compare-and-swap, or Temporal deduplication
- Tests remain valid regardless of implementation mechanism

### Related Specs
- **API Contract Tests**: Define operation ID header structure and validation rules
- **Authorization Tests**: Define permission checking on replay and result retrieval
- **All Other Categories**: Assume idempotency works correctly; focus on business logic

### Future Considerations
- May add tests for distributed idempotency (cross-region, cross-datacenter)
- May add tests for idempotency timeout/expiration if implemented
- May add tests for idempotency cache invalidation scenarios
