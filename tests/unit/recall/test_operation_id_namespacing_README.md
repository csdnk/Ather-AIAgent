# Operation ID Namespacing and Validation Tests (AET-84)

## Overview

This test suite implements RC-IDE-04 and RC-IDE-05 from the Recall idempotency specification, focusing on operation ID namespacing per tenant/principal and header validation.

## Test Cases

### RC-IDE-04: Cross-Principal Operation ID Isolation

**test_rc_ide_04_same_operation_id_different_tenants_isolated**
- Two different tenants use the same literal operation ID
- Verifies results are completely separate (different recall_ids)
- Verifies no cross-tenant result leakage or metadata exposure
- Confirms principal refs and request refs are different

**test_rc_ide_04_same_operation_id_different_users_same_tenant_isolated**
- Two different users in the same tenant use the same operation ID
- Verifies results remain isolated per user/principal
- Confirms different principal_refs lead to separate operations

**test_rc_ide_04_no_metadata_leakage_across_principals**
- Verifies no confidential information leaks across principals
- Ensures queries, scope digests, and request fingerprints remain isolated
- Tests that tenant A's data doesn't appear in tenant B's results

### RC-IDE-05: Operation ID Header Validation

**test_rc_ide_05_missing_operation_id_generates_stable_id**
- Tests behavior when operation ID is None/missing
- Verifies either rejection or auto-generation with stable ID

**test_rc_ide_05_empty_operation_id_rejected**
- Tests behavior with empty string operation ID
- Expects rejection or treatment as missing (auto-generation)

**test_rc_ide_05_malformed_operation_id_format_validation**
- Tests various malformed operation IDs (spaces, newlines, special characters)
- Verifies clear, actionable error messages
- Error messages should mention "operation", "idempotency", or "id"

**test_rc_ide_05_very_long_operation_id_handling**
- Tests operation ID with 1000 characters
- Verifies graceful handling with clear error about length limits

## Implementation Details

### Principal Namespacing

Operation IDs are namespaced by combining:
- `principal_ref` (tenant/user/agent)
- `scope_digest` (hash of scope including tenant_id)
- `idempotency_key` (the operation ID)

This ensures the same literal operation ID used by different principals creates completely separate operations.

### Authorization Setup

Tests create multiple `Authority` instances with different `RecallAuthorization` evidence:
- Different `principal_ref` values
- Different tenant scopes
- Separate service instances per principal

### Validation Patterns

The tests cover:
- **Missing IDs**: `None` or omitted
- **Empty IDs**: Empty string `""`
- **Malformed IDs**: Special characters, whitespace, invalid formats
- **Long IDs**: Exceeding reasonable length limits

## Expected Behavior

### Cross-Principal Isolation
- Same literal operation ID from different principals → separate operations
- No data leakage across tenant boundaries
- No metadata exposure to unauthorized principals

### Header Validation
- Clear, actionable error messages for invalid operation IDs
- Consistent handling of missing/empty/malformed IDs
- Graceful degradation or rejection for edge cases

## Running the Tests

```bash
pytest tests/unit/recall/test_operation_id_namespacing.py -v
```

For specific priority tests:
```bash
pytest tests/unit/recall/test_operation_id_namespacing.py -v -m p0
```

## Dependencies

These tests build on:
- AET-82: Basic serial idempotency (request fingerprint validation)
- Existing admission service namespacing logic

Related tickets:
- AET-83: Concurrent admission deduplication
- AET-85: Response loss recovery
- AET-86: Authorization changes during replay
- AET-87: JSON canonicalization

## Security Considerations

The cross-principal isolation tests verify critical security boundaries:
- Tenant isolation must be absolute (no information disclosure)
- Principal namespacing prevents unauthorized access to operation results
- Metadata like queries, scopes, and fingerprints must not leak

These tests help ensure compliance with multi-tenant security requirements.
