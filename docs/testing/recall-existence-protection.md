# RC-AUTH-15: Existence Protection Tests

**Test Case ID**: RC-AUTH-15  
**Issue**: [AET-33](https://linear.app/yu-xiao/issue/AET-33)  
**Priority**: P0 (Security Acceptance)

## Overview

Validates existence protection for two critical authorization scenarios:

1. **Normal Empty**: Authorized scope with no matching memories (legitimate empty result)
2. **Permission Blocked**: Matching objects exist but caller has no grants

Both scenarios must prevent:
- Restricted content leakage
- Restricted ID or reference disclosure
- Count or existence information leakage
- Distinguishable response patterns that could probe for object existence

## Test Scenarios

### Scenario 1: Normal Empty (F0)

**Setup**:
- Authorized scope with no memories
- Source is normal (not pending/failed)
- No matching objects exist for the query

**Validations**:
- No content leakage in response
- No existence information revealed
- Response format consistent with security requirements
- Distinguishable only through legal maintenance views

**Control**:
- Legal readable request with same scope must succeed
- Proves system functionality (not model/index failure)

### Scenario 2: Permission Blocked

**Setup**:
- Matching objects exist in scope
- Caller has valid authentication
- Caller has no grants for any matching objects

**Validations**:
- No content leakage in response
- No ID or reference disclosure
- No count information (even zero/non-zero)
- No existence revelation through response differences
- Indistinguishable from normal empty to ordinary callers

**Control**:
- Legal granted request with same scope must retrieve objects
- Maintenance view must show objects exist but grants are absent

## Q13 Public Response Strategy

**Status**: `blocked_requirement`

The public response contract for empty/blocked scenarios requires Q13 approval:

- HTTP status codes (200, 403, 404, etc.)
- Error codes (`FORBIDDEN`, `NOT_FOUND`, etc.)
- Response body format
- Reason field content
- Consistency requirements between scenarios

**Current Implementation**:
- Tests validate no leakage regardless of response format
- Tests mark Q13 assertions as `blocked_requirement`
- After Q13 approval, tests will validate specific response contracts

## Test Structure

### Support Module

`tests/recall_existence_protection_support.py`:

- `ExistenceScenario`: Defines a single test scenario
- `ExistenceProtectionCase`: Complete test case with both scenarios
- `assert_no_existence_leakage()`: Validates no forbidden information in response
- `assert_control_success()`: Verifies control request succeeds
- `execute_existence_test()`: Executes one scenario and collects evidence
- `collect_diagnostic_evidence()`: Gathers maintenance view evidence

### Integration Tests

`tests/integration/test_recall_existence_protection.py`:

- `test_existence_protection_control_success`: Validates control scenario
- `test_existence_protection_normal_empty`: Tests F0 normal empty
- `test_existence_protection_permission_blocked`: Tests grant absence
- `test_existence_protection_response_consistency`: Q13-dependent consistency
- `test_existence_protection_no_timing_leakage`: Q13-dependent timing validation
- `test_existence_protection_maintenance_evidence_isolation`: Validates evidence boundaries

## Evidence Collection

### Evidence Directory

External workspace: `E:/projects/codex/.agent-work/aether/workspace-support/AET-33/`

### Evidence Structure

```json
{
  "case_id": "RC-AUTH-15",
  "variant": "normal_empty" | "permission_blocked",
  "lane": "live-http",
  "scenario_type": "normal_empty" | "permission_blocked",
  "run_id": "<unique-run-identifier>",
  "source_sha": "<git-commit-sha>",
  "image_digest": "sha256:<digest>",
  "config_hash": "<configuration-hash>",
  "backend_binding": "<backend-digest>",
  "model_binding": "<model-digest>",
  "model_space": "<model-space-id>",
  "q13_policy_blocked": true,
  "control_memories": [...],
  "responses": [...],
  "blockers": [...],
  "acceptance": "not_evaluated" | "blocked_requirement_q13" | "passed"
}
```

### Execution Evidence

Each test execution records:
- Operation ID and job ID
- HTTP responses (status, headers, redacted body)
- Trace IDs for maintenance correlation
- Diagnostic evidence from maintenance views
- Validation results (no leakage, isolation verified)

### Blocker Types

- `blocked_requirement`: Q13 public response strategy not approved
- `blocked_fixture`: Missing credentials, control plane, or test infrastructure

## Fixture Requirements

### Environment Variables

**Fixture Manifest**:
- `P3_AUTH15_FIXTURE_FILE`: Path to test case manifest JSON

**Credentials**:
- `<scenario_empty.credential_env>`: Ordinary credential for empty scenario
- `<scenario_blocked.credential_env>`: Ordinary credential for blocked scenario
- `<control_credential_env>`: Ordinary credential for control success
- `<maintainer_env>`: Legal maintenance credential for diagnostic evidence

### Fixture Manifest Format

```json
{
  "base_url": "https://recall.service.local",
  "cases": {
    "normal_empty": {
      "run_id": "<unique-id>",
      "scenario_empty": { ... },
      "scenario_blocked": { ... },
      "control_principal": { ... },
      "control_credential_env": "P3_AUTH15_CONTROL_TOKEN",
      "control_memories": [ ... ],
      "maintainer_env": "P3_AUTH15_MAINTAINER_TOKEN",
      "configuration": { ... },
      "server_settings": { ... },
      "source_sha": "<40-hex-sha>",
      "image_digest": "sha256:<64-hex-digest>",
      "backend_binding": "<digest>",
      "model_binding": "<digest>",
      "model_space": "<model-space-id>",
      "evidence_directory": "/path/to/external/evidence",
      "q13_policy_blocked": true
    },
    "permission_blocked": { ... }
  }
}
```

## Security Validations

### No Content Leakage

Response payload must not contain:
- `pack`, `groups`, `items`, `candidates`
- `rendered_context`, `content`, `body`
- `memory_id`, `memory`, `result`, `result_ref`
- F1 text content (coffee/tea references)
- Credential tokens or secrets

### No Existence Leakage

Response must not reveal:
- `count`, `total`, `matches`, `candidate_count`
- Object IDs or references
- Distinguishing HTTP codes between scenarios (before Q13)
- Distinguishing error messages between scenarios (before Q13)

### Maintenance Evidence Isolation

- Restricted information appears only in legal maintenance views
- Ordinary callers cannot access diagnostic endpoints
- Maintenance views require proper authorization
- Evidence distinguishes test setup without exposing to callers

## Integration Points

### Real Backend Integration

- No authorization overrides
- No fixed sleep instead of Ready barriers
- Actual HTTP operations against running service
- Real model and index integration

### Strict Contract Substitutes

- Controlled embedding for reproducibility
- Exact F1 reference pairs (coffee/tea)
- Separate execution and statistics from real backend

## Acceptance Criteria

- [ ] Prepare independent F0 normal empty and permission-blocked scenarios
- [ ] Execute with similar legal requests, poll tasks, retrieve results
- [ ] Validate control scenario proves system functionality
- [ ] Assert no restricted content delivery
- [ ] Assert no restricted ID/count/existence leakage
- [ ] Distinguish setup through legal controlled phase evidence only
- [ ] Mark Q13 as blocked_requirement for public response assertions
- [ ] After Q13 approval: validate response format consistency
- [ ] Collect complete evidence: SHA, digest, traces, responses
- [ ] Record blockers for missing policy/fixture
- [ ] Mark blocked_requirement for incomplete Q13 validation

## Future Work (Post Q13 Approval)

1. **Response Contract Validation**:
   - Specific HTTP status codes
   - Specific error codes
   - Reason field format
   - Response body schema

2. **Timing Analysis**:
   - Statistical timing consistency
   - No distinguishable latency patterns
   - Timing leakage prevention

3. **Complete RC-AUTH-15 Variants**:
   - Additional edge cases
   - Multiple object counts
   - Different scope dimensions

## References

- Parent Issue: [AET-2](https://linear.app/yu-xiao/issue/AET-2) - Recall Authorization and Permission Tests
- Related: AET-32 (RC-AUTH-14) - Ownership Matrix
- Related: AET-31 (RC-AUTH-13) - Grant Revocation Before Reranker
- Test Source: Recall QA Document (2026-10-07) - Permission and Scope Chapter
