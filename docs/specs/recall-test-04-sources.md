# Spec: Recall Source Selection and Coverage Tests

## Problem Statement

The Recall system supports multiple memory sources (working memory and long-term memory) with different characteristics and selection strategies. The system must correctly select sources based on request parameters, handle source availability issues, report coverage status accurately, and gracefully degrade when sources are partially or completely unavailable. Without comprehensive source selection tests, the system risks incorrect source selection, improper fallback behavior, and confusing coverage reporting.

## Solution

Implement comprehensive source selection tests (RC-SRC-01 through RC-SRC-14) that verify:
- Working memory vector search execution
- Long-term memory independent retrieval  
- Both sources execution and fusion
- Auto source selection with/without context
- Explicit source specification enforcement
- Index pending/failed state handling
- Mixed availability coverage reporting
- Single-source failure degradation
- Required source failure handling
- Complete unavailability error reporting
- Normal empty vs degraded distinction
- Source completion ordering independence

## User Stories

1. As a user requesting working memory, I want only working memory to be searched using real vector retrieval, so that I get current context
2. As a developer, I want to verify working source actually executes query encoding and vector search, so that it's not just listing recent memories
3. As a system operator, I want working source requests to never fall back to long-term sources, so that source selection is explicit
4. As a user requesting long-term memory, I want only long-term sources to be searched, so that I get historical knowledge
5. As a security engineer, I want source filtering to happen before scope filtering, so that authorization boundaries are respected per source
6. As a developer, I want long-term requests to never include working memory as fallback, so that source semantics are clear
7. As a user requesting both sources, I want both working and long-term to be searched independently, so that I get comprehensive results
8. As a developer, I want both-source results to preserve source attribution, so that I know where each memory came from
9. As a system operator, I want identical memories in both sources to appear only once in results, so that duplication is avoided
10. As a developer, I want both-source execution to not be marked complete if only one source executed, so that coverage is accurate
11. As a user with session context, I want auto source selection to choose both sources, so that I get comprehensive results
12. As a user with task context, I want auto source selection to choose both sources, so that task-relevant memories are included
13. As a developer, I want auto selection reasoning to be traceable, so that I can understand why sources were chosen
14. As a system operator, I want auto selection to not be overridden by failure or hit counts, so that selection logic is deterministic
15. As a user without session or task context, I want auto selection to choose long-term only, so that I don't get irrelevant working memory
16. As a developer, I want auto selection to not automatically search working memory just because recent memories exist, so that context is required
17. As a user requesting working source when only long-term is ready, I want to get legitimate empty results, so that I know working memory is empty
18. As a system operator, I want explicit source requests to never silently switch sources, so that behavior is predictable
19. As a developer, I want to verify long-term source is actually searchable when working returns empty, so that empty is truly empty not broken
20. As a user, I want to know when indexing is still in progress, so that I can wait for complete results
21. As a system operator, I want pending state to be distinguishable from legitimate empty, so that I can monitor indexing progress
22. As a developer, I want pending status to include the original indexing job ID, so that I can track what's pending
23. As a system operator, I want pending state to not fall back to full-text listing, so that vector search semantics are preserved
24. As a user, I want to know when indexing has failed, so that I can report the issue
25. As a system operator, I want failed indexing to report the actual failure reason, so that I can troubleshoot
26. As a developer, I want failed status to be distinguishable from legitimate empty and pending, so that I can handle each appropriately
27. As a system operator, I want index failure to not fall back to keyword search claiming success, so that failure is visible
28. As a user with mixed ready and pending sources, I want available results to be returned if policy allows, so that I'm not blocked completely
29. As a system operator, I want pending status to be observable in results, so that I know results are incomplete
30. As a developer, I want coverage to not be marked complete when some sources are pending, so that status is accurate
31. As a system operator with degradation policy enabled, I want single-source failure in both-mode to return the successful source, so that service continues
32. As a user, I want degraded results to be marked as degraded with reason, so that I know what's missing
33. As a developer, I want degraded results to not claim complete "both" coverage, so that status is honest
34. As a security engineer, I want degradation to not return unauthorized or expired candidates as filler, so that fallback respects authorization
35. As a system operator with strict policy, I want both-source failure of one required source to fail the entire request, so that incomplete results aren't delivered
36. As a developer, I want required-source failure to not be masked by having some valid candidates, so that failure is clear
37. As a system operator, I want required-source failure to not generate successful packed results, so that downstream systems know the request failed
38. As a user, I want complete vector source unavailability to fail clearly, so that I can retry or report the issue
39. As a developer, I want vector unavailability to not fall back to keyword search, so that failure modes are explicit
40. As a security engineer, I want body availability to not be used as proof of successful vector recall, so that different failure modes are distinguished
41. As a system operator, I want normal empty (all sources searched, no matches) to be distinguishable from degraded empty (some sources failed, no matches), so that monitoring can distinguish health states
42. As a developer, I want normal empty to only be reported when all selected sources completed successfully with no matches, so that coverage status is accurate
43. As a user, I want both-source results to not depend on which source completes first, so that results are deterministic
44. As a developer, I want source completion order to not affect fusion scores or ranking, so that timing doesn't influence relevance
45. As a system operator, I want late-arriving results past deadline to not modify committed results, so that results are immutable once delivered

## Implementation Decisions

### Test Infrastructure
- **Test framework**: pytest with pytest-asyncio
- **Test file**: `tests/recall/test_sources.py` containing all 14 source selection tests (RC-SRC-01 through RC-SRC-14)
- **Test markers**: `@pytest.mark.sources` and priority markers
- **Diagnostic access**: Tests use `DiagnosticClient` to inspect query encoding, search source tags, and coverage status

### Test Fixtures

**Data Fixtures**:
- `f1_working_ready`: Working memories (coffee + tea) in Ready state
- `f2_longterm_ready`: Long-term memory in Ready state
- `f3_both_ready`: Both working and long-term with known candidates (W=[A v2, B v1], L=[C v1, A v2])
- `f7_pending_only`: Working memory in pending state (indexing not complete)
- `f7_failed_only`: Working memory in failed state (indexing failed)

**Identity Fixtures**:
- `u01_tenant_a_read`: Standard user with READ permission
- `u06_read_diagnose`: User with READ + DIAGNOSE for trace inspection

### Source Selection Testing Strategy

**Explicit Source Verification**:
- Tests verify actual execution by checking:
  - Query encoding stage output (model usage, vector hash)
  - Vector search provider binding
  - Source filter applied to search
  - Ref types in final results

**Coverage Reporting**:
- Tests verify coverage status structure:
  - `working`: Ready | Pending | Failed | Empty
  - `long_term`: Ready | Pending | Failed | Empty
- Tests verify coverage matches actual execution (can't claim Ready if not executed)

**Auto Selection Logic**:
- Test with session_id present → expect both
- Test with task_id present → expect both  
- Test with both present → expect both
- Test with neither → expect long_term
- Verify selection stored in result and traceable

### Degradation Testing

**Single-Source Failure** (RC-SRC-10):
- Setup: both-source request, working ready, long-term unavailable, policy allows partial
- Inject: Network timeout or service unavailable for long-term
- Verify:
  - Result contains working source only
  - `selected_sources` shows both requested
  - `degraded: true` with reason
  - Coverage shows working=Ready, long_term=Unavailable

**Required-Source Failure** (RC-SRC-11):
- Same setup but policy requires complete both
- Verify:
  - Request fails (no successful result)
  - No packed event generated
  - Error indicates required source unavailable

### State Handling Testing

**Pending State** (RC-SRC-07):
- Use fixture creation without waiting for indexing completion
- Pause indexing at projection publishing step
- Submit working-source request
- Verify:
  - Not returned as normal Empty
  - Coverage indicates pending with original job reference
  - No fallback to body listing

**Failed State** (RC-SRC-08):
- Inject indexing failure for source
- Verify:
  - Coverage indicates failed with reason
  - Distinguished from pending and empty
  - No fallback to keyword search

### Completion Order Testing (RC-SRC-14)

**Test Pattern**:
- Setup: Fixed candidate set for both sources
- Test variant 1: Make working complete first
- Test variant 2: Make long-term complete first  
- Verify: Same final candidates, same fusion scores, same coverage
- Use controlled delays at search provider level to manipulate timing

### Error Injection

**Vector Source Unavailability** (RC-SRC-12):
- Inject Milvus connection failure for all selected sources
- Verify: Request fails with vector dependency error
- Verify: Does not fall back to Remember body listing
- Verify: Remember body endpoint still works (proves isolation)

**Network Timeout**:
- Use Toxiproxy or similar to inject latency/timeout
- Verify: Timeout treated as source unavailable
- Verify: Deadline enforcement at source level

## Testing Decisions

### What Makes a Good Test
- Tests verify **actual source execution**, not just API parameters
- Tests use **diagnostic access** to inspect query encoding and search binding
- Tests verify **coverage accuracy** matches reality
- Tests verify **failure isolation** (one source failure doesn't affect orthogonal sources)

### Test Structure Pattern

**Source Execution Test**:
```
async def test_rc_src_01_working_vector_search():
    # Setup: f1_working_ready + f2_longterm_ready
    # Request: sources="working"
    # Submit and get result
    # Diagnostic: Check query encoding happened
    # Diagnostic: Check vector search executed with source filter
    # Verify: Result contains only working source
    # Verify: No long-term content
```

**Degradation Test**:
```
async def test_rc_src_10_degradation_allowed():
    # Setup: f3_both_ready, policy allows partial
    # Inject: Long-term timeout
    # Request: sources="both"
    # Verify result: Contains working only
    # Verify degraded flag: True with reason
    # Verify coverage: working=Ready, long_term=Unavailable
```

### Modules to Test
- **Primary**: Source selection logic in recall stages
- **Secondary**: Coverage calculation, degradation policies
- **Diagnostic**: Query encoding stage, vector search stage, fusion stage
- **Remember integration**: Source filtering in qualification

### Assertion Helpers
- `assert_source_execution(trace, expected_sources: list[str])` - verify sources actually executed
- `assert_coverage_accurate(result, expected_coverage: dict)` - verify coverage matches execution
- `assert_degraded(result, missing_source: str, reason_pattern: str)` - verify degradation markers
- `assert_source_attribution(result, expected_sources_per_item: dict)` - verify each item's source

### Prior Art
- Source selection patterns from existing retrieval systems
- Degradation testing from service mesh or API gateway tests
- Coverage reporting patterns from search system tests

### Evidence Collection
- Source selection test failures collect:
  - Query encoding details (model, usage, vector)
  - Vector search requests (filters, bindings)
  - Coverage status at each stage
  - Source completion timing
  - Degradation reasons

## Out of Scope

The following are explicitly out of scope for source selection tests:
- **Ranking quality**: Whether working memories rank higher than long-term (covered by fusion/reranking tests)
- **Indexing implementation**: How Remember creates and maintains vector indices
- **Source semantics**: Business rules for what goes in working vs long-term memory
- **Performance**: Source execution time, parallel execution efficiency
- **Cache behavior**: Source-level caching strategies

## Further Notes

### Blocked Requirements
- **Q09**: Coverage policy details (pending behavior, partial results allowed/denied)
  - Tests RC-SRC-07, RC-SRC-09 may be partially blocked
  - Will document observed behavior

### Source Strategy Guidance
Tests document proper source selection for clients:
- Use `working` for current conversation context
- Use `long_term` for historical knowledge
- Use `both` when session/task context exists
- Use `auto` when unsure (system will choose based on context)

### Indexing Coordination
- Tests require ability to pause/fail indexing for state tests
- May need test mode in Remember indexing pipeline
- Alternatively, use injectable Remember client for controlled responses

### Temporal Considerations
- Source searches may be parallel Temporal activities
- Completion order tests verify activity scheduling doesn't affect fusion
- Tests remain valid regardless of execution model (parallel activities, sequential, etc.)

### Related Specs
- **Fusion Tests**: Define how sources are combined when both execute
- **Search Tests**: Define vector search behavior per source
- **Qualification Tests**: Define Remember's source-scoped authorization
- **Budget Tests**: Define how source results compete for budget

### Future Considerations
- May add tests for additional sources (e.g., cached, external)
- May add tests for source-specific policies (different K per source)
- May add tests for source priority/weighting in fusion
