# Idempotency Tests (AET-82)

## Overview

This test suite implements RC-IDE-01 and RC-IDE-02 from the Recall idempotency specification.

## Test Cases

### RC-IDE-01: Serial Retry Returns Cached Result

**test_rc_ide_01_same_operation_id_same_request_returns_cached_result**
- Submits a recall request with operation ID "operation-001"
- Submits the exact same request again
- Verifies the second request returns the same recall_id (cached result)
- Verifies no duplicate Pack events are generated
- Verifies authorization is still checked on retry

**test_rc_ide_01_idempotent_retry_checks_current_authorization**
- Verifies that even when returning cached results, the admission service still checks current authorization
- This ensures revoked permissions are enforced even for cached operations

### RC-IDE-02: Conflict Detection for Different Request Bodies

**test_rc_ide_02_same_operation_id_different_query_returns_conflict**
- Submits a recall request with operation ID "operation-002" and a specific query
- Attempts to submit with the same operation ID but different query text
- Verifies `IDEMPOTENCY_CONFLICT` error is raised

**test_rc_ide_02_same_operation_id_different_scope_returns_conflict**
- Tests conflict detection when selection scope differs (different session_id)
- Verifies `IDEMPOTENCY_CONFLICT` error is raised

**test_rc_ide_02_same_operation_id_different_sources_returns_conflict**
- Tests conflict detection when retrieval sources differ (Working vs Semantic)
- Verifies `IDEMPOTENCY_CONFLICT` error is raised

**test_rc_ide_02_same_operation_id_different_budget_returns_conflict**
- Tests conflict detection when token budget differs
- Verifies `IDEMPOTENCY_CONFLICT` error is raised

## Implementation Details

The tests use:
- `AzureRecords` as the storage backend (in-memory for unit tests)
- `RecallAdmissionService` as the system under test
- Test fixtures from `tests.unit.recall.helpers` for creating test data
- `Authority()` fixture provides authorization evidence
- `policy()` fixture provides recall policy configuration

## Error Handling

The admission service computes a `request_fingerprint` from the semantic request content (query, scope, sources, budget, etc.). When an idempotency_key is reused:
- If the fingerprint matches → returns cached result
- If the fingerprint differs → raises `RecallError("IDEMPOTENCY_CONFLICT")`

## Running the Tests

```bash
pytest tests/unit/recall/test_idempotency.py -v
```

For specific priority tests:
```bash
pytest tests/unit/recall/test_idempotency.py -v -m p0
```

## Dependencies

These tests verify the admission layer only. Full integration tests covering:
- Concurrent admission (RC-IDE-03)
- Cross-principal namespacing (RC-IDE-04)
- Response loss recovery (RC-IDE-06)
- Authorization changes (RC-IDE-07)
- JSON canonicalization (RC-IDE-08)

are tracked in separate tickets (AET-83 through AET-87).
