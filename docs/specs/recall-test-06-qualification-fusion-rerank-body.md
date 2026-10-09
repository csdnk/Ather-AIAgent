# Spec: Recall Qualification, Fusion, Reranking, and Body Retrieval Tests

## Problem Statement

After vector search identifies candidates, the Recall system must: (1) verify authorization through Remember qualification, (2) fuse candidates from multiple sources using RRF, (3) optionally rerank using CrossEncoder, and (4) retrieve complete authoritative bodies. Each stage has critical correctness requirements, contract dependencies, and failure modes that must be thoroughly tested to ensure recall quality and security.

## Solution

Implement comprehensive tests covering four interconnected stages:
- **Qualification (RC-QUA-01 through RC-QUA-08)**: Remember authorization verification, evidence validation, decision handling, conflict resolution, state lifecycle
- **Fusion (RC-FUS-01 through RC-FUS-08)**: RRF score calculation, duplicate handling, cross-source merging, single-source ordering, post-qualification fusion
- **Reranking (RC-RER-01 through RC-RER-08)**: CrossEncoder execution control, score correspondence, required vs fallback modes, illegal output handling, query-body pairing, timeout handling
- **Body Retrieval (RC-BODY-01 through RC-BODY-08)**: Complete body loading, payload vs authority distinction, version consistency, integrity validation, storage failures, expiration, deletion, event recording

## User Stories

### Qualification User Stories

1. As a security engineer, I want qualification to request Remember authorization for exact candidate targets (Ref/version/hash/space/chunk), so that authorization is precise
2. As a developer, I want allowed decisions to include complete manifest and guard evidence, so that authorization is auditable
3. As a security engineer, I want allowed decisions without evidence to be rejected, so that authorization requires proof
4. As a system operator, I want qualification responses to cover all requested targets, so that partial responses are detected
5. As a developer, I want excluded decisions to distinguish from unverifiable, so that different authorization failures are observable
6. As a system operator, I want unverifiable status to affect coverage reporting, so that qualification uncertainty is visible
7. As a developer, I want conflicting evidence for the same memory to trigger bounded retry, so that transient inconsistency is tolerated
8. As a security engineer, I want persistent conflicts to exclude candidates, so that ambiguous authorization is denied
9. As a developer, I want old versions and pending states to be excluded based on authoritative Remember state, so that only current Ready content is recalled
10. As a security engineer, I want qualification to use consistent authority snapshot within a batch, so that concurrent changes don't create inconsistency

### Fusion User Stories

11. As a developer, I want RRF fusion to use rank/(60+rank) formula starting from rank 1, so that fusion scores are calculated correctly
12. As a user, I want memories appearing in both sources to get combined RRF contributions, so that cross-source candidates rank higher
13. As a developer, I want same-source duplicates to not inflate scores, so that repeated chunks don't bias ranking
14. As a security engineer, I want only qualified candidates to participate in fusion, so that excluded content doesn't affect ranking
15. As a developer, I want version mismatches across sources to not merge, so that old and new versions remain distinct
16. As a user, I want single-source candidate order to be preserved in fusion output, so that search ranking is respected
17. As a system operator, I want fusion scores to be deterministic given same inputs, so that results are reproducible

### Reranking User Stories

18. As a developer with reranking disabled, I want no CrossEncoder calls and clear disabled status, so that I can verify configuration
19. As a user with reranking enabled, I want query-body pairs to be scored accurately, so that semantic relevance is refined
20. As a developer, I want reranking input positions to correspond exactly to output scores, so that candidate-score binding is correct
21. As a system operator with required reranking, I want model unavailability to fail the request, so that degraded results aren't delivered
22. As a developer with fallback reranking, I want model failures to fall back to fusion order with degraded flag, so that service continues
23. As a security engineer, I want contract violations in reranking output to not silently degrade, so that errors are visible
24. As a developer, I want query+body pair length to be validated against tokenizer limits, so that truncation is avoided
25. As a system operator, I want timeout reranking inference to not hold slots indefinitely, so that capacity is released

### Body Retrieval User Stories

26. As a user whose search matched a middle chunk, I want the complete memory body retrieved, so that I get full context
27. As a security engineer, I want authoritative Remember body to be used, not vector payload, so that content integrity is guaranteed
28. As a developer, I want body hash to match qualification manifest, so that content hasn't changed since authorization
29. As a system operator, I want version changes between qualification and body load to be detected, so that stale data isn't delivered
30. As a user, I want storage failures to be reported clearly, so that transient issues can be retried
31. As a developer, I want memories with explicit expiration to be blocked after expiry, so that TTL is enforced
32. As a security engineer, I want logical deletion to block access before physical cleanup, so that residual data doesn't leak
33. As a system operator, I want successful body reads to be recorded in events, so that access is auditable

## Implementation Decisions

### Test Infrastructure
- **Test files**: 
  - `tests/recall/test_qualification.py` - RC-QUA-01 through RC-QUA-08
  - `tests/recall/test_fusion.py` - RC-FUS-01 through RC-FUS-08
  - `tests/recall/test_reranking.py` - RC-RER-01 through RC-RER-08
  - `tests/recall/test_body.py` - RC-BODY-01 through RC-BODY-08
- **Test markers**: Category markers plus priority markers
- **Injectable providers**: Use contract violation factories for Remember and reranker responses

### Test Fixtures

**Qualification Fixtures**:
- Standard fixtures (f1, f2) with known authorized candidates
- Contract violation factory for missing/malformed evidence
- Multi-version fixture for version conflict testing

**Fusion Fixtures**:
- Fixed candidate lists: W=[A v2, B v1], L=[C v1, A v2]
- Known RRF scores calculable by hand for verification
- Reranking disabled to isolate fusion behavior

**Reranking Fixtures**:
- Reranker model (session-scoped)
- Controlled score provider for deterministic testing
- Query+body pairs at length boundaries

**Body Fixtures**:
- Multi-chunk memory (BEGIN/MIDDLE/END)
- Memories with explicit expiration
- Logically deleted memories with residual index entries

### Qualification Testing Strategy

**Evidence Validation** (RC-QUA-01, RC-QUA-02):
- Submit qualification request with precise targets
- Verify request includes all required fields (Ref/version/hash/space/chunk_index/vector_id)
- Test allowed with complete evidence → accept
- Test allowed with missing manifest → reject
- Test allowed with missing guard → reject
- Test allowed with mismatched hash → reject

**Decision Handling** (RC-QUA-05):
- Test allowed → candidate proceeds
- Test excluded → candidate removed, reason logged
- Test unverifiable → affects coverage, candidate removed
- Verify unverifiable distinguished from excluded in logs

**Conflict Resolution** (RC-QUA-06):
- Setup: Injectable Remember returns conflicting evidence for same memory
- Verify: One retry attempted
- If still conflicting after retry: candidate excluded, coverage degraded
- Verify: Retry count limited (no infinite loop)

### Fusion Testing Strategy

**RRF Calculation** (RC-FUS-01):
- Setup: Known candidate lists W=[A v2, B v1], L=[C v1, A v2]
- Calculate expected scores:
  - A: 1/(60+1) + 1/(60+2) = 1/61 + 1/62
  - C: 1/(60+1) = 1/61
  - B: 1/(60+2) = 1/62
- Expected order: A > C > B
- Verify: Actual fusion matches calculation within floating-point tolerance

**Duplicate Handling** (RC-FUS-02):
- Test: W=[A, A, B], L=[C, A]
- Verify: A gets exactly two contributions (one from each source), not three
- Verify: B remains at rank 2 (not pushed to rank 3 by duplicate A)

**Cross-Version Non-Merge** (RC-FUS-03):
- Test: W=[A v2], L=[A v1, A v2]
- Verify: v1 excluded by qualification
- Verify: Only v2 merged (not v1 score added to v2)

### Reranking Testing Strategy

**Disabled Mode** (RC-RER-01):
- Config: rerank=disabled
- Verify: No CrossEncoder model calls
- Verify: Stage marked as disabled (not completed)
- Verify: Result uses fusion order

**Required Mode Failure** (RC-RER-03):
- Config: rerank=required
- Inject: Model unavailable
- Verify: Request fails (no result delivered)
- Verify: No fallback to fusion order

**Fallback Mode** (RC-RER-04):
- Config: rerank=fallback
- Inject: Model timeout
- Verify: Falls back to fusion order
- Verify: Result marked as degraded with reason

**Contract Violation** (RC-RER-05):
- Inject: NaN scores, wrong count, mismatched positions
- Verify: Treated as contract error (not fallback)
- Verify: Request fails rather than silently using bad scores

### Body Retrieval Testing Strategy

**Complete Body Loading** (RC-BODY-01):
- Setup: Multi-chunk memory, search matches MIDDLE chunk only
- Verify: Complete body retrieved (all chunks)
- Verify: Body hash matches candidate metadata
- Verify: No chunk concatenation (single authoritative body)

**Payload vs Authority** (RC-BODY-02):
- Setup: Milvus payload contains different text than Remember body
- Verify: Remember body used (not payload)
- Verify: Payload text doesn't appear in result

**Version Consistency** (RC-BODY-03):
- Setup: Pause between qualification and body load
- Change memory version during pause
- Verify: Version change detected
- Verify: Stale body rejected or safe re-qualification triggered

**Storage Failure** (RC-BODY-05):
- Inject: Ceph unavailable, timeout, or corrupt object
- Verify: Failure affects coverage (not silent empty)
- Verify: Other candidates still processed (failure isolated)

**Expiration** (RC-BODY-06):
- Setup: Memory with expires_at timestamp
- Test before expiry: succeeds
- Test after expiry: blocked for new recall and result retrieval
- Test without expires_at: never auto-expires

**Logical Deletion** (RC-BODY-07):
- Setup: Memory logically deleted, vector residue remains
- Verify: New recall blocked
- Verify: Old result retrieval blocked
- Verify: Physical cleanup lag doesn't grant access

## Testing Decisions

### What Makes a Good Test
- Tests verify **contract compliance** between Recall and Remember
- Tests use **injectable providers** for controlled fault injection
- Tests verify **security properties** (authorization required, integrity checked)
- Tests verify **degradation behavior** (when fallback allowed, when required to fail)

### Test Structure Patterns

**Qualification Test**:
```
async def test_rc_qua_02_missing_evidence():
    # Setup: f1_working_ready
    # Inject: allowed decision without manifest
    # Submit recall
    # Verify: Contract error (not just excluded)
    # Verify: No successful result
```

**Fusion Test**:
```
async def test_rc_fus_01_rrf_calculation():
    # Setup: Fixed lists W=[A, B], L=[C, A]
    # Calculate expected RRF scores manually
    # Submit recall (reranking disabled)
    # Verify: Fusion scores match calculation
    # Verify: Order is A > C > B
```

**Reranking Test**:
```
async def test_rc_rer_03_required_failure():
    # Setup: rerank=required, f1_working_ready
    # Inject: Model unavailable
    # Submit recall
    # Verify: Request fails
    # Verify: No fallback result
```

**Body Test**:
```
async def test_rc_body_01_complete_body():
    # Setup: Multi-chunk memory
    # Search returns MIDDLE chunk only
    # Verify: Complete body in result
    # Verify: Hash matches
    # Verify: All chunks present
```

### Modules to Test
- **Qualification**: Remember qualification integration
- **Fusion**: RRF calculation logic
- **Reranking**: CrossEncoder integration
- **Body**: Remember body retrieval, integrity validation

### Assertion Helpers
- `assert_qualification_complete(targets, results)` - verify all targets covered
- `assert_rrf_scores(candidates, expected_scores, tolerance)` - verify fusion calculation
- `assert_reranking_correspondence(input_refs, output_scores)` - verify position matching
- `assert_complete_body(result_item, expected_chunks)` - verify full body retrieved

### Prior Art
- Qualification patterns from Remember authorization tests
- RRF fusion from search system tests
- ML model integration patterns from similar services
- Body retrieval from content management system tests

## Out of Scope

- **Remember implementation**: How Remember stores bodies and checks permissions
- **RRF algorithm choice**: Why RRF vs other fusion methods
- **Reranker model quality**: Whether CrossEncoder scores reflect true relevance
- **Storage implementation**: How Ceph stores and retrieves objects
- **Performance**: Qualification latency, reranking throughput, body fetch parallelization

## Further Notes

### Contract Dependencies
- Tests assume Remember qualification contract from project docs
- Tests assume CrossEncoder contract for reranking
- Tests document actual behavior when contracts are ambiguous

### Injectable Providers
- `InjectableRememberClient`: Returns controlled qualification/body responses
- `InjectableReranker`: Returns controlled scores or errors
- Injection allows testing contract violations without breaking Remember

### Blocked Requirements
- Exact floating-point tolerance for RRF scores
- Tie-breaking rules for same fusion scores
- Will document observed behavior and mark blocked

### Performance Considerations
- Body retrieval tests may be slower (actual Ceph access)
- Reranking tests may be slower (actual model inference)
- Consider session-scoped model loading
- Estimated time: ~8-12 minutes for all four categories

### Related Specs
- **Authorization Tests**: Define permission checking in qualification
- **Sources Tests**: Define which candidates qualify per source
- **Relations Tests**: Define relationship groups that qualify together
- **Budget Tests**: Define which qualified+ranked content fits in budget
