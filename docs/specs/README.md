# Recall Test Specifications - Index

## Overview

This directory contains comprehensive test specifications for the Recall module of the Agent Memory Management system. The specifications are organized by test category, with each spec covering a distinct aspect of the Recall functionality.

## Specification Documents

### 1. [API Contract Tests](./recall-test-01-api-contract.md)
**Test Cases**: RC-API-01 through RC-API-12 (12 tests)  
**Priority**: P0 (7 tests), P1 (5 tests)  
**Focus**: HTTP API contract validation, input validation, security boundaries

Covers request/response contracts including:
- End-to-end successful requests
- Query parameter validation (null, empty, type mismatches)
- Unicode and multilingual input handling
- Token budget boundaries
- Source enumeration validation
- Injection attack prevention
- Malformed request handling
- HTTP method/route validation

### 2. [Idempotency and Admission Tests](./recall-test-02-idempotency.md)
**Test Cases**: RC-IDE-01 through RC-IDE-08 (8 tests)  
**Priority**: P0 (6 tests), P1 (2 tests)  
**Focus**: Operation ID handling, retry behavior, concurrent admission

Covers idempotency guarantees including:
- Serial retry with same operation ID
- Conflict detection for mismatched request bodies
- Concurrent admission deduplication
- Cross-principal operation ID namespacing
- Response loss recovery
- Authorization changes during replay
- JSON normalization for signatures

### 3. [Authorization and Permission Tests](./recall-test-03-authorization.md)
**Test Cases**: RC-AUTH-01 through RC-AUTH-15 (15 tests)  
**Priority**: P0 (13 tests), P1 (2 tests)  
**Focus**: Multi-tenant isolation, permission enforcement, security boundaries

Covers authorization including:
- Authentication and credential validation
- Permission-based access (READ, WRITE, DIAGNOSE)
- Multi-tenant and cross-application isolation
- User and agent separation
- Index-based authorization verification
- Shared grant lifecycle
- Permission revocation and epoch handling
- In-flight authorization changes

### 4. [Source Selection and Coverage Tests](./recall-test-04-sources.md)
**Test Cases**: RC-SRC-01 through RC-SRC-14 (14 tests)  
**Priority**: P0 (8 tests), P1 (6 tests)  
**Focus**: Source selection logic, coverage reporting, degradation

Covers source selection including:
- Working vs long-term source execution
- Both-source fusion
- Auto source selection logic
- Index pending/failed state handling
- Degradation policies
- Complete unavailability handling
- Source completion ordering independence

### 5. [Query Embedding and Vector Search Tests](./recall-test-05-embedding-search.md)
**Test Cases**: RC-EMB-01 through RC-EMB-14 (14 tests), RC-SEA-01 through RC-SEA-11 (11 tests)  
**Priority**: P0 (15 tests), P1 (7 tests), P2 (3 tests)  
**Focus**: Query encoding, vector validation, candidate discovery

Covers embedding and search including:
- Query vector generation and validation
- Model space binding
- Usage prefix handling
- Input length boundaries
- Illegal vector detection
- Filtering before TopK
- Multi-chunk deduplication
- Version isolation
- Pagination and limits

### 6. [Qualification, Fusion, Reranking, and Body Tests](./recall-test-06-qualification-fusion-rerank-body.md)
**Test Cases**: RC-QUA-01 through RC-QUA-08 (8 tests), RC-FUS-01 through RC-FUS-08 (8 tests), RC-RER-01 through RC-RER-08 (8 tests), RC-BODY-01 through RC-BODY-08 (8 tests)  
**Priority**: P0 (24 tests), P1 (6 tests), P2 (2 tests)  
**Focus**: Authorization verification, candidate fusion, reranking, body retrieval

Covers middle pipeline stages including:
- Remember qualification with evidence
- RRF fusion calculation
- CrossEncoder reranking
- Complete body retrieval
- Version consistency
- Storage failure handling
- Expiration and deletion

### 7. [Relationship Groups and Token Budget Tests](./recall-test-07-relations-budget.md)
**Test Cases**: RC-REL-01 through RC-REL-07 (7 tests), RC-BUD-01 through RC-BUD-08 (8 tests)  
**Priority**: P0 (11 tests), P1 (4 tests)  
**Focus**: Relationship completion, budget enforcement, group atomicity

Covers final assembly including:
- Relationship group completion
- Missing member handling
- Revision validation
- Token budget boundaries
- Overflow continuation
- Group atomicity
- Unicode token counting
- Tokenizer isolation

## Test Statistics

- **Total Test Cases**: 121 tests across 7 categories
- **Priority Breakdown**:
  - P0 (Critical): 84 tests (~69%)
  - P1 (Important): 30 tests (~25%)
  - P2 (Nice-to-have): 7 tests (~6%)

## Test Organization

### By Priority
- **P0 Tests**: Core functionality, security boundaries, data integrity
- **P1 Tests**: Edge cases, error handling, configuration validation
- **P2 Tests**: Advanced features, nice-to-have validations

### By Category
Tests are organized to minimize dependencies:
1. **API Contract** - Can run independently
2. **Idempotency** - Depends on API Contract
3. **Authorization** - Can run independently (foundational security)
4. **Sources** - Depends on basic API and Auth
5. **Embedding/Search** - Depends on Sources
6. **Qual/Fusion/Rerank/Body** - Depends on Search
7. **Relations/Budget** - Depends on all previous stages

## Implementation Order

Based on dependencies and priority:

**Phase 1** (Week 1-2): Foundation
- API Contract Tests (all 12)
- Idempotency Tests (all 8)
- Authorization Tests (P0 only: 13 tests)

**Phase 2** (Week 3-4): Core Retrieval
- Source Selection Tests (P0 only: 8 tests)
- Embedding Tests (P0 only: 9 tests)
- Search Tests (P0 only: 6 tests)

**Phase 3** (Week 5-6): Pipeline Stages
- Qualification Tests (all 8)
- Fusion Tests (all 8)
- Reranking Tests (P0 only: 6 tests)
- Body Tests (P0 only: 6 tests)

**Phase 4** (Week 7): Final Assembly
- Relations Tests (all 7)
- Budget Tests (all 8)
- P1/P2 tests from all categories

## Common Patterns

### Test Structure
All specs follow consistent patterns:
- **Problem Statement**: User-facing problem being solved
- **Solution**: High-level approach
- **User Stories**: Extensive list covering all aspects
- **Implementation Decisions**: Technical details, fixtures, strategies
- **Testing Decisions**: What makes good tests, structure patterns
- **Out of Scope**: Explicit exclusions
- **Further Notes**: Blocked requirements, performance, related specs

### Fixture System
Standardized fixtures across all tests:
- **Data Fixtures**: `f0` through `f12` (e.g., `f1_working_ready`)
- **Identity Fixtures**: `u01` through `u08` (e.g., `u01_tenant_a_read`)
- **Barrier Fixtures**: `b0` through `b7` (e.g., `b5_before_final_guard`)

### Evidence Collection
All tests automatically collect evidence on failure:
- HTTP requests/responses
- Operation/recall/job IDs
- Execution traces
- Authorization decisions
- Event logs
- Automatic redaction of sensitive data

## Blocked Requirements

Several test aspects await specification clarification:

- **Q01**: API contract details (token_budget min/max, selection defaults, HTTP mappings)
- **Q07**: Idempotency signature (JSON canonicalization, Unicode normalization)
- **Q09**: Coverage policy (pending/failed behavior, partial results)
- **Q13**: Empty vs authorization-denied distinction
- **Q18**: Not-found vs unauthorized response patterns

Tests blocked by these will be skipped with clear markers until specifications are provided.

## Related Documentation

- **Implementation Guide**: `docs/RECALL_TEST_IMPLEMENTATION_GUIDE.md` - Complete implementation guide with all design decisions
- **Test Specification**: `docs/refs/Recall_测试用例_精简版.md` - Original Chinese test case specification
- **QA Documentation**: `docs/refs/Recall_QA测试文档.md` - Detailed execution guide and test procedures

## Getting Started

1. Review the [Implementation Guide](../RECALL_TEST_IMPLEMENTATION_GUIDE.md) for complete design decisions
2. Read specs for test categories you'll implement
3. Start with API Contract Tests (foundational, no dependencies)
4. Follow the implementation phases outlined above
5. Reference blocked requirements registry when encountering skipped tests

## Success Criteria

Test implementation is complete when:
- ✅ All 121 test cases implemented with spec traceability
- ✅ All P0 tests pass consistently
- ✅ Evidence collection working for failures
- ✅ Traceability matrix maps tests to specs
- ✅ Blocked requirements documented and skipped
- ✅ Test suite runs in <30 minutes

## Contributing

When adding new test cases:
1. Identify which category the test belongs to
2. Update the corresponding spec document
3. Add user stories explaining the need
4. Follow established test patterns
5. Update this index with new test counts

## Questions?

For questions about test specifications:
- Check the Implementation Guide for design decisions
- Review related specs for cross-cutting concerns
- Check blocked requirements registry for known gaps
- Refer to original Chinese specification documents for context
