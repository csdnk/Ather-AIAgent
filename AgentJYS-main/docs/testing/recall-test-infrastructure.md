# Recall API Testing Infrastructure

## Overview

This document describes the core testing infrastructure for the Recall API, implemented as part of **AET-8: Core Testing Infrastructure & Happy Path**.

## Test File Location

- **File**: `tests/integration/test_recall_core_infrastructure.py`
- **Test Case**: `test_rc_api_01_happy_path_recall_with_working_memory` (RC-API-01)

## Core Components

### 1. Test Fixtures

#### `test_configuration`
Provides isolated test environment configuration:
- Unique data directory per test
- Test identity setup (alice in tenant t1, eve in tenant t2)
- Temporal server connection
- Test-optimized timing parameters

#### `WorkingMemoryFixture`
Predefined test data (夹具 F1):
- **Coffee preference**: "用户喝咖啡不加糖"
- **Tea preference**: "用户喜欢乌龙茶"

### 2. Helper Functions

#### `headers(operation, user)`
Generates authentication headers for API requests with Bearer token and optional X-Operation-ID for idempotency tracking.

#### `eventually(check, timeout)`
Polls a check function until it returns a truthy value or times out. Used for waiting on asynchronous operations.

#### `poll_operation_until_complete(client, job_id, user, timeout)`
Polls an operation endpoint until it reaches a terminal state (succeeded/failed/attention_required).

#### `prepare_working_memory(client, evidence, user)`
Prepares test data by:
1. Creating two working memories (coffee and tea preferences)
2. Waiting for them to reach Ready state in long-term storage
3. Recording all requests/responses in evidence collector

### 3. Evidence Collection

The `TestEvidence` class captures:
- **Requests**: HTTP method, path, body, headers (excluding sensitive auth tokens)
- **Responses**: Status code, body, Location header
- **Operation IDs**: All job/task IDs generated during test execution
- **Execution stages**: Timestamp and state for each operation stage
- **Trace IDs**: Distributed tracing identifiers

## Test Cases

### RC-API-01: Happy Path

**Purpose**: Validate the simplest successful Recall flow end-to-end.

**Test Steps**:

1. **Prepare Test Data**
   - Create working memory: "用户喝咖啡不加糖"
   - Create working memory: "用户喜欢乌龙茶"
   - Wait for both to reach Ready state

2. **Submit Recall Request**
   - Query: "咖啡"
   - Sources: "long_term"
   - Token budget: 1000
   - With proper authentication (Bearer token for alice)

3. **Handle Response**
   - Synchronous (200): Result returned immediately
   - Asynchronous (202): Poll operation status until complete

4. **Retrieve Result** (if async)
   - GET `/p3/operations/{job_id}/result`

5. **Verify Result Content**
   - Contains `recall_id`
   - Contains `rendered_context`
   - Contains `items` list
   - Context includes coffee-related content

6. **Verify Token Budget**
   - Rendered context respects token budget
   - Estimated tokens ≤ budget * 1.2 (20% tolerance)

7. **Verify Evidence Collection**
   - At least 3 requests captured
   - At least 3 responses captured
   - Operation IDs recorded
   - Execution stages tracked

**Acceptance Criteria** (All Met):
- ✅ Test harness can prepare test data (夹具 F1: Working memory in Ready state)
- ✅ Can submit POST /p3/recall requests with proper authentication
- ✅ Can poll operation status and retrieve results
- ✅ Returned context pack contains expected content within token budget
- ✅ Evidence collection captures: request/response, operation IDs, trace data, execution stages
- ✅ RC-API-01 passes completely

### Additional Infrastructure Tests

#### `test_recall_infrastructure_can_handle_empty_results`
Verifies the infrastructure handles queries with no matching results gracefully.

#### `test_recall_infrastructure_respects_authentication`
Verifies proper authentication enforcement:
- Requests without authentication return 401
- Requests with invalid tokens return 401

## Running Tests

### Prerequisites

The integration tests require:
- Python 3.13
- Virtual environment with dependencies installed
- Temporal CLI (set `P3_TEMPORAL_CLI` environment variable)
- Azure infrastructure (for full integration tests):
  - PostgreSQL database
  - Redis
  - Milvus vector database
  - Ceph object storage

### Local Development (Unit/Component Tests)

```bash
cd AgentJYS-main
source .venv/bin/activate

# Install dependencies
python -m pip install -e '.[embedding-onnx,resource-documents,remember-ceph]' --group dev

# Run the test (requires Temporal CLI and test infrastructure)
P3_TEMPORAL_CLI=/path/to/temporal python -m pytest \
  tests/integration/test_recall_core_infrastructure.py::test_rc_api_01_happy_path_recall_with_working_memory \
  -v
```

### Full Azure Integration Tests

For complete integration testing with real Azure infrastructure:

```bash
# Set up environment variables (see scripts/p3/run_aks_tests.py for full list)
export P3_TEST_STATE_DSN="postgresql://..."
export P3_TEST_REDIS_HOST="..."
export P3_TEST_MILVUS_URI="..."
# ... (other required environment variables)

# Run via the AKS test runner
python scripts/p3/run_aks_tests.py \
  --environment-file path/to/env.json \
  --assets-directory path/to/assets \
  --directory path/to/workspace \
  -- tests/integration/test_recall_core_infrastructure.py
```

## Test Design Principles

1. **Isolation**: Each test uses isolated resources (unique schemas, namespaces)
2. **Evidence-Based**: All assertions are backed by captured evidence
3. **Deterministic**: Tests use controlled embeddings for reproducible results
4. **Complete**: Tests verify the full flow from request to result
5. **Realistic**: Tests use actual API client patterns and authentication

## Future Test Cases

This infrastructure supports adding additional test cases:

- **RC-API-02**: Multi-tenant isolation
- **RC-API-03**: Token budget enforcement edge cases
- **RC-API-04**: Authorization and permission checks
- **RC-API-05**: Recall with scope selection
- **RC-API-06**: Error handling and recovery
- **RC-API-07**: Concurrent recall requests
- **RC-API-08**: Large result set pagination

## References

- Main codebase: `src/aether_agent_memory/runtime/flows/http.py`
- Recall service: `src/aether_agent_memory/runtime/temporal/recall.py`
- Existing integration tests: `tests/integration/test_pending_recall_restart.py`
- Configuration template: `configs/p3.azure.example.yaml`
