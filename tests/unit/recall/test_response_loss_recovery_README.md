# Response Loss Recovery Tests (AET-85)

## Overview

This test suite implements RC-IDE-06 from the Recall idempotency specification, focusing on response loss recovery at the HTTP layer (B7 stage - after commit, before delivery).

## Test Cases

### RC-IDE-06: Response Loss Recovery

**test_rc_ide_06_response_loss_preserves_original_pack**
- Simulates response dropped at B7 (after commit, before HTTP delivery)
- Verifies original Pack/packed is preserved after response loss
- Client retries with same operation ID and receives original result

**test_rc_ide_06_query_operation_status_via_recall_id**
- Tests querying operation status via job/recall ID
- Verifies recall_id can be used to retrieve operation state
- Ensures client can check status after response loss

**test_rc_ide_06_retry_returns_original_result_without_reexecution**
- Verifies retry returns original result without re-execution
- Confirms no duplicate Pack or packed events generated
- Validates authorization is re-checked on retry

**test_rc_ide_06_unsent_response_not_marked_acknowledged**
- Tests distinction between response committed vs. delivered
- Response at B7 is committed but not yet acknowledged by client
- System should allow retry because client never received original

**test_rc_ide_06_get_result_enforces_current_authorization**
- Verifies GET result enforces current authorization even for cached results
- Authorization must be checked at retrieval time, not just at creation
- Prevents security issues from replaying old authorization decisions

**test_rc_ide_06_multiple_retries_after_response_loss**
- Tests multiple retries after response loss all return same result
- Simulates client uncertainty about delivery (retrying multiple times)
- Verifies consistency across all retry attempts

**test_rc_ide_06_response_loss_different_queries_conflict**
- After response loss, retry with different query raises IDEMPOTENCY_CONFLICT
- Ensures idempotency validation still applies during recovery
- Prevents client from accidentally changing request during retry

## Implementation Details

### B7 Stage Simulation

The tests simulate the B7 stage where:
1. **Before B7**: Request is being processed
2. **At B7**: Result is committed to durable storage (Pack/packed saved)
3. **After B7**: HTTP response would be sent to client
4. **Response Loss**: Network failure, timeout, or other issue prevents delivery

The key insight: once B7 commits, the operation is durable and idempotency kicks in for any retry.

### Recovery Scenarios

**Scenario 1: Simple Retry**
```
Client -> POST (op-001)
Server -> [commits at B7] 
Network -> [drops response]
Client -> POST (op-001) [retry]
Server -> Returns original result (no re-execution)
```

**Scenario 2: Query by ID**
```
Client -> POST (op-001) -> receives recall_id
Network -> [drops response]
Client -> Query by recall_id
Server -> Returns operation status
```

**Scenario 3: Authorization Re-check**
```
Client -> POST (op-001) [authorized]
Server -> [commits at B7]
[Time passes, authorization might change]
Client -> POST (op-001) [retry]
Server -> Re-checks current authorization before returning cached result
```

### Expected Behavior

1. **Idempotency Preservation**: Retry with same operation ID returns original result
2. **No Duplicate Execution**: Original Pack/packed preserved, no re-execution or duplicate events
3. **Authorization Enforcement**: Current authorization checked on every retrieval
4. **Conflict Detection**: Different request body with same operation ID raises IDEMPOTENCY_CONFLICT
5. **Consistency**: Multiple retries all return identical result

## Running the Tests

```bash
pytest tests/unit/recall/test_response_loss_recovery.py -v
```

For specific priority tests:
```bash
pytest tests/unit/recall/test_response_loss_recovery.py -v -m p0
```

## Dependencies

These tests build on:
- AET-82: Basic serial idempotency (foundational behavior)
- Existing admission service commit and recovery logic

Related tickets:
- AET-83: Concurrent admission deduplication
- AET-84: Operation ID namespacing and validation
- AET-86: Authorization changes during replay (extends this scenario)
- AET-87: JSON canonicalization

## Production Considerations

In a production system, response loss recovery requires:
1. **Durable Storage**: Results must be committed before HTTP response
2. **Operation Status Endpoint**: Separate endpoint to query by recall_id/job_id
3. **Client Acknowledgment Tracking**: Distinguish between committed vs. delivered
4. **Retry Logic**: Clients must use exponential backoff with same operation ID
5. **Timeout Handling**: Clients should query status rather than infinite retry

## Testing Limitations

These unit tests verify the admission layer logic but don't test:
- Actual HTTP layer response dropping
- Network timeout simulation
- Database transaction commit durability
- Cross-service recovery coordination

Integration tests would be needed to verify end-to-end response loss recovery in a deployed system.
