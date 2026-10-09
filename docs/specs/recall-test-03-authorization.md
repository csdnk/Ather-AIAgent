# Spec: Recall Authorization and Permission Tests

## Problem Statement

The Recall system must enforce strict authorization boundaries to prevent unauthorized access to memories across tenants, users, agents, sessions, and tasks. Without comprehensive authorization testing, the system risks data leakage, privilege escalation, and violation of privacy guarantees. The authorization system must handle authentication failures, permission checks, scope isolation, shared grants, revocation, and dynamic permission changes throughout the recall lifecycle.

## Solution

Implement comprehensive authorization tests (RC-AUTH-01 through RC-AUTH-15) that verify:
- Authentication and credential validation
- Permission-based access control (READ, WRITE, DIAGNOSE)
- Multi-tenant isolation
- Cross-application boundaries
- User and agent separation
- Session and task scoping
- Client identity protection
- Index-based authorization (payload cannot grant access)
- Explicit sharing and grant lifecycle
- Permission revocation and epoch handling
- In-flight authorization changes
- Diagnostic permission boundaries
- Authorization denial vs empty result distinction

## User Stories

1. As a system administrator, I want unauthenticated requests to be rejected, so that only authorized users can access memories
2. As a user, I want expired credentials to be rejected, so that my access is time-limited as configured
3. As a developer, I want clear distinction between 401 (authentication failed) and 403 (authenticated but unauthorized), so that I can troubleshoot access issues
4. As a security engineer, I want missing or invalid Bearer tokens to prevent any recall execution, so that authentication is mandatory
5. As a user with WRITE permission, I want to be unable to recall memories, so that permissions are properly separated
6. As a security engineer, I want WRITE permission to not imply READ permission, so that principle of least privilege is maintained
7. As a system administrator, I want to verify that permission checks leave audit trails, so that access can be reviewed
8. As a tenant administrator, I want my tenant's data to be completely isolated from other tenants, so that privacy is guaranteed
9. As a security engineer, I want same-named user/session/memory IDs in different tenants to remain isolated, so that namespace collisions don't cause leakage
10. As a tenant user, I want high-scoring memories from other tenants to not displace my legitimate results, so that search ranking respects authorization
11. As a tenant administrator, I want deduplication and caching to be tenant-scoped, so that cross-tenant optimization doesn't leak data
12. As an application developer, I want my application's memories isolated from other applications in the same tenant, so that application boundaries are maintained
13. As a user, I want selection parameters to only narrow my authorized scope, so that I can't use them to access other applications
14. As a security engineer, I want explicit cross-application sharing to be required for any access, so that default-deny is enforced
15. As a user, I want my memories isolated from other users in the same application, so that personal data remains private
16. As a security engineer, I want agent-scoped memories to be isolated from user-scoped memories, so that different principals remain separated
17. As a developer, I want grants to require explicit evidence, so that authorization is auditable
18. As a user, I want session-scoped recalls to only access that session's memories, so that conversations remain isolated
19. As a task owner, I want task-scoped recalls to only access that task's context, so that task isolation is maintained
20. As a security engineer, I want selection to take intersection with authorization, so that scope can only be narrowed, never expanded
21. As a developer, I want empty selection behavior to be clearly defined, so that I don't accidentally expand scope
22. As a security engineer, I want client-provided tenant_id fields to be ignored, so that clients can't impersonate other tenants
23. As a security engineer, I want client-provided grant fields to be ignored, so that clients can't forge authorization
24. As a security engineer, I want client-provided auth_epoch to be ignored, so that clients can't bypass revocation
25. As a developer, I want Bearer token identity to always take precedence over request body fields, so that authentication is trustworthy
26. As a security engineer, I want vector search payload to never grant read access, so that index optimization doesn't bypass authorization
27. As a security engineer, I want excluded candidates to not leak their IDs to callers, so that unauthorized content remains hidden
28. As a security engineer, I want unauthorized content to never reach reranking models, so that even ML services don't see restricted data
29. As a user, I want to access memories explicitly shared with me, so that collaboration is possible
30. As a memory owner, I want sharing to be discoverable only by the granted party, so that grants are private
31. As a security engineer, I want grants to specify exact memory versions, so that sharing is precise and auditable
32. As a user, I want shared memories to not imply ownership, so that I can't modify or delete them
33. As a security engineer, I want grant scope to be limited to explicitly granted memories, so that one grant doesn't expand to related memories
34. As a memory owner, I want to revoke sharing, so that access is time-limited
35. As a formerly-granted user, I want revoked access to take effect immediately for new recalls, so that revocation is enforced
36. As a security engineer, I want old cached results to enforce current authorization, so that result caching doesn't bypass revocation
37. As a security engineer, I want vector index residue after revocation to not grant access, so that physical deletion lag doesn't create vulnerabilities
38. As a system administrator, I want suspended tenants to lose access immediately, so that account suspension is effective
39. As a security engineer, I want tenant reactivation to require new auth_epoch, so that old tokens can't be reused
40. As a system administrator, I want old credentials to be invalid after tenant reactivation, so that suspension creates a security boundary
41. As a security engineer, I want identity epoch changes during recall execution to be detected, so that long-running operations respect current authorization
42. As a security engineer, I want permission revocation during assembly to prevent result commit, so that permission changes are enforced at all stages
43. As a security engineer, I want identity to be revalidated at final guard, so that begin-of-request authentication isn't sufficient
44. As a security engineer, I want reranking to check current permissions before model invocation, so that revocation during execution is respected
45. As a developer with DIAGNOSE permission, I want to inspect execution traces for my own operations, so that I can troubleshoot issues
46. As a security engineer, I want DIAGNOSE permission to not grant READ access to memory content, so that diagnostic access is limited
47. As a security engineer, I want DIAGNOSE users to not access other users' traces, so that operation privacy is maintained
48. As a developer, I want clear documentation of what DIAGNOSE permission allows, so that I can request appropriate access
49. As a user, I want "no authorized memories found" to be distinguishable from "authorization denied", so that I can understand why recall is empty
50. As a security engineer, I want permission-blocked results to not leak existence information, so that unauthorized content discovery is prevented
51. As a system administrator, I want error messages for authorization failures to be informative but not leak sensitive details, so that troubleshooting is possible without security compromise

## Implementation Decisions

### Test Infrastructure
- **Test framework**: pytest with pytest-asyncio
- **Test file**: `tests/recall/test_auth.py` containing all 15 authorization tests (RC-AUTH-01 through RC-AUTH-15)
- **Test markers**: Each test tagged with `@pytest.mark.auth`, `@pytest.mark.security`, and priority markers
- **Test isolation**: Each test uses unique tenant ID (`T-test-{test_id}`) for complete isolation

### Test Fixtures

**Identity Fixtures** (via `IdentityFactory`):
- `u01_tenant_a_read`: Tenant A, user with READ permission
- `u02_tenant_a_different_user`: Tenant A, different user with READ
- `u04_tenant_b_read`: Tenant B, user with READ (for cross-tenant tests)
- `u05_write_only`: Same scope as U01 but only WRITE permission
- `u06_read_diagnose`: READ + DIAGNOSE permissions
- `u07_diagnose_only`: Only DIAGNOSE permission
- `u08_shared_grant`: Independent principal with specific grant

**Data Fixtures**:
- `f1_working_ready`: Standard Working memories (coffee + tea)
- `f4_cross_tenant`: Memories in multiple tenants with same user/session/memory IDs, includes high-scoring distractor data

### Authorization Testing Strategy

**Multi-layer Verification**:
1. **HTTP layer**: Verify appropriate status codes (401, 403) and error responses
2. **Candidate layer**: Use diagnostic client to verify unauthorized memories never become candidates
3. **Model layer**: Verify unauthorized content never reaches reranking models
4. **Result layer**: Verify final results contain only authorized content
5. **Event layer**: Verify no `read` events for unauthorized content

**Cross-boundary Testing**:
- **Tenant boundaries**: Use `f4_cross_tenant` fixture with same logical IDs in different tenants
- **Application boundaries**: Use same-tenant but different-application identities
- **User boundaries**: Use same-tenant/app but different users
- **Session boundaries**: Use same user but different session IDs in selection

### Permission Manipulation
- **Revocation**: Use `IdentityFactory.revoke_permission(identity, "READ")` to increment auth_epoch
- **Epoch changes**: Use `IdentityFactory.increment_epoch(identity)` to simulate tenant suspend/resume
- **Wait for authority**: Use `wait_for_authority_receipt()` helper after permission changes
- **Grant management**: Use Remember client to create/revoke explicit grants for sharing tests

### In-flight Authorization Changes
- **Barrier-based testing**: Use stage barriers to pause execution at specific points:
  - `B4`: Before reranking (RC-AUTH-13)
  - `B5`: Before final guard (RC-AUTH-12)
- **Test sequence**:
  1. Submit recall and pause at barrier
  2. Revoke permissions and wait for propagation
  3. Release barrier
  4. Verify operation fails or excludes revoked content

### Shared Grant Testing
- **Grant setup**: Create explicit grants via Remember API
- **Grant verification**: Confirm grant is active before testing recall
- **Isolation**: Verify granted user can access only granted memories, not all memories in that scope
- **Revocation**: Revoke grant, wait for propagation, verify access denied

### Diagnostic Access Testing
- **DIAGNOSE permission**: Create identities with various permission combinations
- **Trace access**: Verify DIAGNOSE allows trace access for own operations
- **Content exclusion**: Verify DIAGNOSE doesn't grant access to memory content
- **Scope limitation**: Verify can't access other users' traces even with DIAGNOSE

### Empty vs Denied Distinction
- **Setup parallel tests**:
  - Test A: User has authorized scope but no memories exist (legitimate empty)
  - Test B: Memories exist but all are unauthorized (authorization block)
- **Verification**: Response structure, status codes, and any observable differences
- **Blocked by Q13**: Exact response policy awaiting specification

## Testing Decisions

### What Makes a Good Test
- Tests verify **security boundaries are enforced** at all layers (HTTP, candidates, models, results)
- Tests use **realistic multi-tenant data** with intentional naming collisions
- Tests verify **no information leakage** (IDs, existence, counts, content)
- Tests verify **defense in depth** (authorization checked at multiple stages, not just entry)

### Test Structure Patterns

**Cross-Tenant Isolation Test**:
```
async def test_rc_auth_03_cross_tenant_isolation():
    # Setup: f4_cross_tenant with same IDs in both tenants
    # Submit from Tenant A
    # Submit from Tenant B (same query)
    # Verify A gets only A's memories
    # Verify B gets only B's memories
    # Verify no ID collision in results
```

**Permission Revocation Test**:
```
async def test_rc_auth_10_grant_revocation():
    # Setup: Create shared grant (RC-AUTH-09)
    # Verify shared access works
    # Revoke grant and wait for propagation
    # New recall should deny access
    # Old result retrieval should deny access
    # Verify no content leakage
```

**In-flight Change Test**:
```
async def test_rc_auth_12_inflight_revocation():
    # Setup barrier at B5 (before final guard)
    # Submit recall and pause
    # Revoke READ permission
    # Wait for identity reload
    # Release barrier
    # Verify final guard blocks result
    # Verify already-read content may be preserved in events (but not delivered)
```

### Modules to Test
- **Primary**: Authorization checks in `RecallAdmission`, `RecallStages`
- **Secondary**: Identity validation, scope calculation, grant verification
- **Diagnostic**: Event system for `read` event verification
- **Remember integration**: Qualification decisions and guard stamps

### Assertion Helpers
- **Authorization denial**: `assert_authorization_denied(response, should_not_leak=["memory_id", "content"])`
- **Tenant isolation**: `assert_no_cross_tenant_leakage(tenant_a_result, tenant_a_memories, tenant_b_result, tenant_b_memories)`
- **Scope verification**: `assert_result_within_scope(result, authorized_scope)`
- **No information leakage**: `assert_no_sensitive_info_in_error(error_response, ["memory_id", "tenant_id", "content"])`

### Prior Art
- Similar authorization tests in Remember service
- Multi-tenant isolation patterns from platform tests
- Permission revocation patterns from identity service tests

### Evidence Collection
- All authorization test failures collect:
  - Identity details (tenant, user, permissions, epoch)
  - Authorization decisions from Remember
  - Candidate lists at each stage
  - Event logs showing read/packed events
  - Error responses with full structure

## Out of Scope

The following are explicitly out of scope for authorization tests:
- **Authentication implementation**: How tokens are generated, validated, or refreshed (identity service responsibility)
- **Permission grant implementation**: How Remember stores and checks grants (Remember's responsibility)
- **Authorization policy definition**: What permissions mean and how they're assigned (platform policy)
- **Performance**: Authorization check overhead, caching efficiency
- **Audit logging**: Completeness of audit trails (observability concern)
- **Rate limiting**: Authorization-based rate limits (separate concern)

## Further Notes

### Blocked Requirements
- **Q13**: Empty vs authorization-denied response policy
  - Test RC-AUTH-15 partially blocked
  - Will document observed behavior and mark blocked variants

### Security Testing Best Practices
- Use **isolated test tenants** to prevent interference with production data
- Use **safe canary data** in injection tests (no actual malicious payloads)
- **Never disable security** in tests (test the real authorization stack)
- **Verify negative cases** (unauthorized access is denied) as thoroughly as positive cases

### Cross-Service Coordination
- Authorization tests require Remember service for:
  - Memory creation with specific ownership
  - Grant creation and revocation
  - Authority receipt verification
- Tests should be resilient to Remember authorization check delays

### Performance Considerations
- Authorization tests may be slower due to:
  - Multi-tenant fixture setup
  - Grant propagation waits
  - Barrier synchronization
- Estimated execution time: ~5-8 minutes for all 15 tests

### Related Specs
- **API Contract Tests**: Define authentication header requirements
- **Idempotency Tests**: Define authorization re-checking on replay
- **Qualification Tests**: Define Remember's qualification decisions
- **All Other Categories**: Assume authorization works; focus on authorized-content behavior

### Future Considerations
- May add tests for role-based access control (RBAC) if implemented
- May add tests for attribute-based access control (ABAC) rules
- May add tests for authorization caching and cache invalidation
- May add tests for federated identity scenarios
