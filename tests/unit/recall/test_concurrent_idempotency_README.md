# Concurrent Idempotency Tests (AET-83)

## Overview

This test suite implements RC-IDE-03 from the Recall idempotency specification, focusing on concurrent admission deduplication.

## Test Cases

### RC-IDE-03: Concurrent Admission Deduplication

**test_rc_ide_03_concurrent_admission_same_operation_id_deduplicates**
- Launches two concurrent clients with the same operation ID and identical request body
- Uses barrier synchronization at B0 (before candidates stage) to coordinate timing
- Verifies both clients reference the same recall_id (deduplication occurred)
- Verifies only one Pack event would be generated (same execution object)
- Verifies both clients receive reference to the same operation result

**test_rc_ide_03_concurrent_different_operation_ids_creates_separate_results**
- Launches two concurrent clients with different operation IDs
- Verifies that different operation IDs create separate recall_ids
- Ensures concurrent processing doesn't incorrectly deduplicate different operations

**test_rc_ide_03_three_concurrent_clients_same_operation_id**
- Tests with three concurrent clients all using the same operation ID
- Verifies all three clients reference the same recall_id
- Ensures deduplication works correctly with more than two concurrent clients

## Implementation Details

### Barrier Synchronization

The tests use `asyncio.Event()` as a barrier to coordinate concurrent clients:

1. Each client signals it's ready via a separate event
2. All clients wait at the barrier
3. Once all clients are ready, the barrier is released
4. All clients proceed simultaneously to submit their requests

This simulates the scenario described in the spec where multiple clients reach B0 (before candidates stage) at the same time.

### Concurrency Model

- Uses `asyncio.create_task()` to launch concurrent clients
- Uses `asyncio.gather()` to wait for all clients to complete
- Brief sleep ensures all clients are actually waiting before barrier release

### Expected Behavior

When multiple clients submit with the same operation ID and request body:
- The admission service should detect the duplicate
- Only one execution should be created
- All clients should receive a reference to the same recall_id
- No duplicate Pack or packed events should be generated

## Running the Tests

```bash
pytest tests/unit/recall/test_concurrent_idempotency.py -v
```

For specific priority tests:
```bash
pytest tests/unit/recall/test_concurrent_idempotency.py -v -m p0
```

## Dependencies

These tests build on AET-82 (basic serial idempotency) and verify concurrent behavior. They test the admission layer's ability to deduplicate concurrent requests.

Further concurrency and idempotency tests are tracked in:
- AET-84: RC-IDE-04 & RC-IDE-05 (Operation ID namespacing and validation)
- AET-85: RC-IDE-06 (Response loss recovery)
- AET-86: RC-IDE-07 (Authorization changes during replay)
- AET-87: RC-IDE-08 (JSON canonicalization)

## Notes

The current implementation uses in-memory `AzureRecords` which may not fully simulate the database-level locking and transaction isolation of a production system. These tests verify the logical behavior at the service layer.
