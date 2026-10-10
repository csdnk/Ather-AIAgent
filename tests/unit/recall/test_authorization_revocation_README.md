# Authorization Changes During Replay Tests (AET-86)

## Overview

This test suite implements RC-IDE-07 from the Recall idempotency specification, focusing on security boundaries when permissions are revoked after successful recall completion.

## Test Cases

### RC-IDE-07: Authorization Changes During Replay

**test_rc_ide_07_permission_revoked_after_recall_completion**
- Permission revoked after successful recall completion
- Idempotent POST with same operation ID after revocation should not leak old content
- Verifies SCOPE_DENIED or AUTHORIZATION error is raised

**test_rc_ide_07_idempotent_replay_enforces_current_authorization**
- Even though operation was successful before, revoked permissions prevent replay
- System checks current authorization, not cached authorization decision
- Ensures security boundaries are maintained across time

**test_rc_ide_07_get_result_after_revocation_denied**
- GET original result after revocation enforces current permissions
- Retrieving previously successful result fails if permissions revoked
- Verifies authorization is checked at retrieval time

**test_rc_ide_07_no_content_leakage_through_http_cache**
- Cannot bypass current authorization with HTTP cache from before revocation
- System must not replay pre-revocation HTTP responses
- Authorization check count increases even when denied

**test_rc_ide_07_internal_read_events_preserved_content_not_delivered**
- Internal read events may be preserved for audit/logging
- Content must not be delivered to client after revocation
- Distinction between internal records and client delivery

**test_rc_ide_07_partial_revocation_working_memory_only**
- Partial revocation (e.g., working memory only) is respected
- If working memory READ is revoked but long-term granted, request is restricted
- Tests granular permission enforcement

**test_rc_ide_07_revocation_different_tenant_unaffected**
- Revocation for one tenant doesn't affect other tenants
- Security boundary: permission changes are tenant-scoped
- Ensures proper isolation across tenants

## Implementation Details

### Authorization Revocation Simulation

Tests simulate permission revocation by modifying the `Authority.evidence`:

```python
# Before revocation - full access
auth.evidence = RecallAuthorization(
    principal_ref="tenant-a/user-1/agent-1",
    working_eligible=True,
    long_term_eligible=True,
    working_scope=scope(tenant_id="tenant-a"),
    long_term_scope=scope(tenant_id="tenant-a"),
)

# After revocation - no access
auth.evidence = RecallAuthorization(
    principal_ref="tenant-a/user-1/agent-1",
    working_eligible=False,  # READ revoked
    long_term_eligible=False,  # READ revoked
    working_scope=scope(tenant_id="tenant-a"),
    long_term_scope=scope(tenant_id="tenant-a"),
)
```

### Security Scenarios

**Scenario 1: Complete Revocation**
```
Time T0: User has READ permission
         POST /recall -> Success (recall_id=123)
Time T1: User's READ permission revoked
         POST /recall (same operation ID) -> SCOPE_DENIED
         GET /recall/123 -> SCOPE_DENIED
```

**Scenario 2: Partial Revocation**
```
Time T0: User has working + long-term READ
         POST /recall -> Success (uses both sources)
Time T1: Working memory READ revoked, long-term still granted
         POST /recall (same operation ID) -> Restricted or denied
```

**Scenario 3: Cross-Tenant Isolation**
```
Tenant A: Permission revoked
          POST /recall -> SCOPE_DENIED
Tenant B: Permission still granted
          POST /recall -> Success (unaffected)
```

### Expected Behavior

1. **No Content Leakage**: Revoked permissions prevent access to previously successful results
2. **Current Authorization**: Every retrieval checks current authorization, not cached decisions
3. **No HTTP Cache Bypass**: Cannot replay pre-revocation HTTP responses to bypass current auth
4. **Internal vs. External**: Internal read events may be preserved for audit, but content not delivered
5. **Granular Enforcement**: Partial revocations (working only, long-term only) are respected
6. **Tenant Isolation**: Permission changes for one tenant don't affect others

## Running the Tests

```bash
pytest tests/unit/recall/test_authorization_revocation.py -v
```

For specific priority tests:
```bash
pytest tests/unit/recall/test_authorization_revocation.py -v -m p0
```

## Dependencies

These tests build on:
- AET-82: Basic serial idempotency (foundational behavior)
- AET-85: Response loss recovery (provides context for retrieval scenarios)
- Existing admission service authorization enforcement

Related tickets:
- AET-83: Concurrent admission deduplication
- AET-84: Operation ID namespacing (cross-tenant isolation)
- AET-87: JSON canonicalization

## Security Implications

These tests verify critical security properties:

1. **Temporal Security**: Authorization is not "locked in" at operation creation time; it's checked on every access
2. **No Privilege Escalation**: Cannot use old operation IDs to access content after permissions revoked
3. **Defense in Depth**: Multiple layers check authorization (admission, retrieval, replay)
4. **Audit Trail**: Internal events may be preserved for compliance without leaking content
5. **Tenant Isolation**: Permission changes are scoped per tenant/principal

## Production Considerations

In a production system, authorization revocation requires:

1. **Real-time Authorization Service**: Must check current permissions, not cached
2. **Authorization Caching Strategy**: Balance performance with security (short TTLs)
3. **Audit Logging**: Record when revoked permissions prevent access to existing operations
4. **Grace Periods**: Consider whether to allow brief grace period or immediate enforcement
5. **Result Cleanup**: Consider whether to expire/delete results when permissions revoked

## Testing Limitations

These unit tests verify admission layer authorization enforcement but don't test:
- Integration with real authorization service (e.g., OAuth, IAM)
- Authorization cache invalidation across distributed systems
- Authorization decision latency under load
- Edge cases around permission grant/revoke timing races

Integration and end-to-end tests would be needed for full authorization flow validation.
