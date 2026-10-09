# Spec: Recall Relationship Groups and Token Budget Tests

## Problem Statement

The Recall system must handle two final critical concerns before committing results: (1) complete relationship groups where memories must be delivered together with their required relationships intact, and (2) token budget constraints that limit the total context pack size. Without comprehensive testing, the system risks delivering incomplete relationship groups (violating semantic coherence), exceeding token budgets (causing downstream model errors), or incorrectly rejecting content that should fit.

## Solution

Implement comprehensive tests covering:
- **Relationship Groups (RC-REL-01 through RC-REL-07)**: Relationship completion for unhit members, missing member handling, uniqueness across candidates, revision consistency, runtime additions, overlapping relationships, and group integration with reranking
- **Token Budget (RC-BUD-01 through RC-BUD-08)**: Whole-pack budget validation, overflow continuation to next groups, budget exhaustion handling, reference/source/separator overhead, group atomicity, token counting accuracy, tokenizer isolation, and fallback handling

## User Stories

### Relationship Groups User Stories

1. As a user, I want related memories to be delivered together, so that semantic relationships are preserved
2. As a developer, I want unhit relationship members to be fetched automatically, so that groups are complete
3. As a security engineer, I want all relationship members to be individually authorized, so that one authorized member doesn't grant access to restricted members
4. As a system operator, I want groups with missing members to be skipped entirely, so that incomplete relationships aren't delivered
5. As a user with other safe content available, I want safe memories to be delivered even when some groups are incomplete, so that I'm not blocked unnecessarily
6. As a developer, I want the reason for group exclusion to be observable, so that I can troubleshoot authorization issues
7. As a system operator, I want missing members due to deletion to be distinguishable from missing due to permissions, so that different failures can be diagnosed
8. As a user whose only relevant memories are in an incomplete group, I want clear indication of why recall is empty, so that I understand the result
9. As a developer, I want multiple candidates sharing the same group to not duplicate the group in results, so that output is clean
10. As a system operator, I want relationship group output to appear once with proper member accounting, so that token budget is calculated correctly
11. As a developer, I want groups to include all required members according to relationship definition, so that semantic completeness is guaranteed
12. As a security engineer, I want relationship snapshots to include all member guards, so that authorization is verified for all members
13. As a developer, I want relationship revision to be validated at final guard, so that concurrent relationship changes are detected
14. As a system operator, I want relationship additions during assembly to trigger re-validation, so that stale groups aren't delivered
15. As a developer, I want new required members exceeding budget to cause proper failure, so that incomplete groups aren't silently delivered
16. As a system operator, I want overlapping or cyclic relationships to be detected, so that infinite expansion is prevented
17. As a developer, I want relationship depth/member limits to be enforced, so that pathological graphs don't cause resource exhaustion
18. As a user with reranking enabled, I want relationship groups to participate correctly in reranking, so that semantic units are scored appropriately

### Token Budget User Stories

19. As a user with token_budget=1000, I want my entire context pack to not exceed 1000 tokens including all metadata, so that downstream models accept it
20. As a developer, I want to test exact budget boundaries (T-1, T, T+1), so that off-by-one errors are caught
21. As a user with multiple groups where the first exceeds budget, I want later groups that fit to be included, so that I get maximum content within budget
22. As a developer, I want budget exhaustion to be distinguishable from no results, so that sizing guidance can be provided
23. As a system operator, I want all groups to be too large to return clear BUDGET_TOO_SMALL error, so that the problem is actionable
24. As a developer, I want reference markers, source attribution, and separators to count toward budget, so that actual rendered output is within limits
25. As a user, I want relationship groups to be budget-evaluated atomically, so that I don't get partial groups
26. As a developer, I want group atomicity to prevent splitting members or removing relationship descriptions, so that semantic coherence is preserved
27. As a system operator, I want Unicode text to be measured in tokens not bytes, so that multilingual content is fairly budgeted
28. As a developer, I want the context pack tokenizer to be independent of embedding tokenizer, so that different models can be used
29. As a system operator, I want tokenizer unavailability to fail clearly, so that byte-based fallback doesn't silently produce wrong counts

## Implementation Decisions

### Test Infrastructure
- **Test files**: 
  - `tests/recall/test_relations.py` - RC-REL-01 through RC-REL-07
  - `tests/recall/test_budget.py` - RC-BUD-01 through RC-BUD-08
- **Test markers**: Category markers plus priority markers
- **Token counting**: Use production tokenizer for actual token counts

### Test Fixtures

**Relationship Fixtures**:
- Complete group G={M3, M5} with relationship r1
- Incomplete group where M5 is missing/unauthorized/failed
- Multiple candidates sharing same group
- Nested relationships (if supported)

**Budget Fixtures**:
- Single complete group with known token count T
- Multiple groups of varying sizes
- Memories with Chinese/English/emoji for Unicode testing
- Long memories that individually exceed any reasonable budget

### Relationship Testing Strategy

**Completion Testing** (RC-REL-01):
- Setup: Group G={M3, M5}, M3 hit by search, M5 not hit
- Submit recall
- Verify: Both M3 and M5 in result
- Verify: Relationship r1 described
- Verify: M5 fetched with authorization check
- Verify: M5 doesn't count as main candidate toward K

**Missing Member Handling** (RC-REL-02, RC-REL-03):
- Test variants:
  - M5 has no grant (authorization failure)
  - M5 body missing (storage failure)
  - M5 no longer Ready (lifecycle failure)
- For each variant:
  - Verify: Entire group skipped
  - Verify: No half-group delivered
  - Verify: If other content exists, that content still delivered
  - Verify: If only content, appropriate error/empty returned

**Shared Group** (RC-REL-04):
- Setup: M3 and M5 both hit by search, both reference G={M3, M5}
- Verify: Group output exactly once
- Verify: Both counted as main candidates
- Verify: Group member content not duplicated

**Revision Validation** (RC-REL-05):
- Setup: Pause at B6 (after assembly)
- Inject: Relationship snapshot with wrong guards/refs/revision
- Release to final guard
- Verify: Snapshot validation fails
- Verify: Stale groups rejected

**Runtime Addition** (RC-REL-06):
- Setup: Pause at B5 (before final guard)
- Setup: Group G={M3, M5} assembled in plan
- Inject: Relationship r2 adds required member M6
- Test variants:
  - M6 would fit in budget: verify re-validation or rejection per policy
  - M6 exceeds budget: verify rejection
- Verify: Can't deliver old group with new relationship requirement

### Budget Testing Strategy

**Exact Boundary Testing** (RC-BUD-01):
- Setup: Single complete group
- Measure actual rendered token count T (including refs, sources, separators)
- Test budgets: T-1, T, T+1
- Verify:
  - T and T+1: group included
  - T-1: group excluded (BUDGET_TOO_SMALL)
- Use production tokenizer to calculate T

**Overflow Continuation** (RC-BUD-02):
- Setup: Groups ordered by rank: H (too large), S (fits)
- Set budget allowing S but not H
- Verify: H skipped, S included
- Verify: Result not marked as error (has content)
- Verify: Budget exclusion logged (not source failure)

**All Groups Too Large** (RC-BUD-03):
- Setup: All qualified groups individually exceed budget
- Verify: BUDGET_TOO_SMALL error
- Verify: Distinguishable from "no matches" and "execution timeout"

**Overhead Accounting** (RC-BUD-04):
- Setup: Memory body that fits in budget
- Calculate full rendered output with:
  - Reference numbers [1], [2], etc.
  - Source attribution
  - Relationship descriptions
  - Section separators
- Verify: If total exceeds budget, group excluded
- Verify: Overhead counted, not just body tokens

**Group Atomicity** (RC-BUD-05):
- Setup: Group G={M3, M5}
- Setup: Single member fits, full group doesn't
- Verify: Entire group excluded (not partial delivery)
- Verify: Can't remove relationship description to fit
- Test with subsequent group S that fits
- Verify: S still delivered (exclusion continues)

**Unicode Token Counting** (RC-BUD-06):
- Setup: Chinese, English, emoji, mixed content
- Calculate expected tokens using production tokenizer
- Verify: Counts match (not byte counts)
- Verify: Same logical content in different languages may have different token counts (accepted, documented)

**Tokenizer Isolation** (RC-BUD-07):
- Setup: Different tokenizers for embedding (HF tokenizer) vs packing (o200k_base)
- Verify: Budget calculated with pack tokenizer
- Verify: Embedding tokenizer not used for budget
- Test with content that has different token counts in each

**Tokenizer Unavailable** (RC-BUD-08):
- Inject: Tokenizer load failure or unavailable
- Verify: Request fails clearly
- Verify: No byte-based fallback claiming success

### Token Counting Utilities

**Dynamic Calculation**:
```python
def calculate_expected_tokens(
    memories: list[Memory],
    relationships: list[Relationship],
    tokenizer: Tokenizer
) -> int:
    # Render full output as it would appear in context pack
    rendered = render_context_pack(memories, relationships)
    # Count tokens using production tokenizer
    return tokenizer.count_tokens(rendered)
```

**Tolerance**:
- Allow small tolerance (±2 tokens) for floating-point precision in calculations
- Document exact tokenizer used and version
- Tests verify actual rendered output matches calculation

## Testing Decisions

### What Makes a Good Test
- Tests use **real tokenizers** to calculate expected token counts
- Tests verify **actual rendered output**, not just component counts
- Tests verify **atomicity** (groups never partially delivered)
- Tests verify **continuation** (first-group failure doesn't stop all groups)

### Test Structure Patterns

**Relationship Test**:
```
async def test_rc_rel_01_completion():
    # Setup: Group G={M3, M5}, M3 hit, M5 not hit
    # Submit recall
    # Verify: Both M3 and M5 in result
    # Verify: Relationship described
    # Verify: M5 authorization checked
    # Count candidates: M3 counts, M5 doesn't
```

**Budget Test**:
```
async def test_rc_bud_01_exact_boundary():
    # Setup: Single group
    # Render and count actual tokens T
    # Test budgets T-1, T, T+1
    # Verify: T and T+1 include group
    # Verify: T-1 excludes with BUDGET_TOO_SMALL
```

**Overflow Test**:
```
async def test_rc_bud_02_overflow_continuation():
    # Setup: Ranked groups H (large), S (small)
    # Budget allows S but not H
    # Verify: H skipped, S included
    # Verify: Not error (has content)
```

### Modules to Test
- **Relationships**: Relationship completion logic, revision validation
- **Budget**: Token counting, rendering, group atomicity
- **Assembly**: Plan generation with relationships and budget constraints

### Assertion Helpers
- `assert_complete_group(result, expected_group)` - verify all members present
- `assert_relationship_described(result, group, relationship)` - verify relationship info
- `assert_token_budget(result, max_budget, tokenizer)` - verify actual tokens
- `assert_group_atomic(excluded_groups)` - verify no partial groups

### Prior Art
- Relationship handling from graph database systems
- Token counting from LLM context management systems
- Budget enforcement from resource quota systems

## Out of Scope

- **Relationship semantics**: What relationships mean, how they're defined
- **Relationship creation**: How Remember establishes relationships
- **Token budget policy**: What the right default budget is
- **Rendering format**: Exact template used for context pack rendering
- **Performance**: Relationship fetch parallelization, token counting optimization

## Further Notes

### Relationship Complexity
- Tests document maximum supported relationship depth/breadth
- Cyclic relationship detection is implementation-specific
- Tests verify limits are enforced (no infinite expansion)

### Token Counting Precision
- Production tokenizer used for all counts
- Small tolerance (±2 tokens) allowed for FP precision
- Documented tokenizer version for reproducibility

### Budget Guidance
Tests document budget sizing:
- Minimum budget to deliver single typical memory: ~200 tokens
- Recommended budget for meaningful results: 1000+ tokens
- Maximum useful budget: depends on model context window

### Blocked Requirements
- Exact relationship depth/member limits
- Group ranking rules when multiple candidates share group
- Will document observed behavior

### Performance Considerations
- Relationship tests may trigger additional Remember fetches
- Token counting is CPU-intensive for large packs
- Estimated time: ~2-3 minutes for relations, ~2-3 minutes for budget

### Related Specs
- **Qualification Tests**: Define member authorization
- **Fusion Tests**: Define candidate ranking before budget application
- **Body Tests**: Define body retrieval for relationship members
- **All Earlier Stages**: Provide candidates and bodies to budget against

### Future Considerations
- May add tests for relationship priority/weighting
- May add tests for conditional relationships (if supported)
- May add tests for relationship cycles and DAG validation
- May add tests for budget allocation strategies (per-group vs global)
