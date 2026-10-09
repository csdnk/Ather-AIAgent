# Spec: Recall Query Embedding and Vector Search Tests

## Problem Statement

The Recall system relies on query embedding and vector search as its core retrieval mechanism. The embedding process must correctly encode queries using the proper model, space, and usage settings, while vector search must find relevant candidates with correct filtering, deduplication, and pagination. Without comprehensive tests, the system risks incorrect embeddings, space mismatches, search ranking errors, and candidate processing failures that directly impact recall quality.

## Solution

Implement comprehensive tests covering:
- **Query Embedding (RC-EMB-01 through RC-EMB-14)**: Query vector generation, space binding, usage prefixes, input length boundaries, configuration validation, illegal vector handling, encoding contract verification, batch processing, model availability, timeout handling, revocation during encoding, and inference idempotency
- **Vector Search (RC-SEA-01 through RC-SEA-11)**: Filtering before TopK, source binding, multi-chunk deduplication, version isolation, qualified-only scoring, bounded pagination, search limits, duplicate handling, metadata validation, illegal scores, and tie-breaking

## User Stories

### Query Embedding User Stories

1. As a user submitting a Chinese query, I want it encoded with BGE-small-zh-v1.5 producing a 512-dimensional normalized vector, so that semantic search works correctly
2. As a developer, I want query vectors to use Query usage prefix, so that they're compatible with Passage embeddings
3. As a system operator, I want query usage/model/weights to be traceable, so that I can verify configuration
4. As a developer, I want query embedding to not apply Passage prefix by mistake, so that search results are accurate
5. As a system operator, I want duplicate query submissions to use stable input fingerprints, so that caching works correctly
6. As a user submitting a long query, I want clear rejection if it exceeds tokenizer limits, so that I can shorten it
7. As a security engineer, I want query truncation to be rejected not silently applied, so that semantic meaning isn't lost
8. As a developer, I want to know whether model wrapper tokens count toward length limits, so that I can validate inputs correctly
9. As a system administrator, I want space ID to uniquely identify model configuration, so that incompatible spaces are rejected
10. As a security engineer, I want same space ID with different weights to be rejected, so that index poisoning is prevented
11. As a developer, I want dimension compatibility to not be mistaken for space compatibility, so that semantically different models don't mix
12. As a system operator, I want illegal vectors (NaN, Infinity, zero) to be rejected before search, so that Milvus doesn't receive invalid data
13. As a developer, I want normalization requirements to be enforced with clear tolerances, so that vectors meet contract requirements
14. As a system operator, I want encoding results to bind to request identity, so that cross-request result pollution is prevented
15. As a developer, I want batch encoding to preserve index correspondence, so that query vectors match their inputs
16. As a security engineer, I want dimension mismatches in batch results to be rejected, so that contract violations are caught
17. As a developer, I want model unavailability to fail clearly, so that I don't get keyword fallback claiming success
18. As a system operator, I want CPU inference slots to be properly managed, so that timeouts don't leak capacity
19. As a developer, I want slow inference to respect overall deadline, so that late results don't bypass timeout
20. As a security engineer, I want revocation during encoding to prevent result use, so that authorization changes are enforced

### Vector Search User Stories

21. As a user in Tenant A, I want scope/source/space filtering before TopK, so that unauthorized high-scoring results don't displace mine
22. As a developer, I want to verify filtering happens at search time, so that post-search filtering doesn't report wrong candidates
23. As a user with long memories, I want chunks of the same memory to count as one candidate, so that one memory doesn't dominate results
24. As a developer, I want candidate score to be the highest qualified chunk score, so that relevance is maximized
25. As a system operator, I want different versions of the same memory to be isolated, so that old high scores don't apply to new content
26. As a developer, I want qualified chunks only to contribute to candidate scores, so that excluded chunks don't affect ranking
27. As a security engineer, I want excluded chunk IDs to not be leaked to callers, so that unauthorized content remains hidden
28. As a developer with K=3, I want pagination to fetch additional pages if first page is dominated by one memory, so that K distinct memories are attempted
29. As a system operator, I want pagination limits (pages, chunks, rounds) to be enforced, so that searches terminate
30. As a developer, I want duplicate pages to be detected, so that infinite loops are prevented
31. As a system operator, I want search metadata (Ref, version, hash, generation) to be complete, so that qualification can verify identity
32. As a developer, I want illegal metadata to cause candidate rejection, so that contract violations don't propagate
33. As a system operator, I want search scores to be finite numbers, so that NaN/Inf don't corrupt ranking
34. As a developer, I want distance-to-similarity conversion to be consistent, so that different metrics can be compared
35. As a system operator, I want tie-breaking to be deterministic, so that same input produces same ranking

## Implementation Decisions

### Test Infrastructure
- **Test files**: 
  - `tests/recall/test_embedding.py` - RC-EMB-01 through RC-EMB-14
  - `tests/recall/test_search.py` - RC-SEA-01 through RC-SEA-11
- **Test markers**: `@pytest.mark.embedding` / `@pytest.mark.search` plus priority markers
- **Model access**: Tests use real BGE model and tokenizer (not mocked)

### Test Fixtures

**Embedding Fixtures**:
- Real BGE-small-zh-v1.5 model loaded (session-scoped for performance)
- Real tokenizer for length validation
- Test queries: Chinese, English, mixed, emoji, boundary-length

**Search Fixtures**:
- `f1_working_ready`: Standard memories for basic search
- `f4_cross_tenant`: Cross-tenant high-scoring distractors for filter tests
- Multi-chunk fixture: Long memory split into BEGIN/MIDDLE/END chunks
- Multi-version fixture: Same memory ID with v1/g1 and v2/g2

### Embedding Testing Strategy

**Vector Validation**:
- Check dimensions (must be 512 for BGE-small-zh-v1.5)
- Check normalization (L2 norm should be 1.0 within tolerance)
- Check finiteness (no NaN, no Inf, no zero vector)
- Use production tokenizer to count actual tokens

**Usage and Prefix Testing**:
- Compare model input with/without prefix
- Verify Query usage applied correctly
- Test both Query and Passage to verify distinction
- Check input hash stability across repeated calls

**Length Boundary Testing**:
- Use Hypothesis to generate length-boundary test cases
- Test L-1, L, L+1 where L is tokenizer max_length
- Include model wrapper tokens in count
- Verify rejection (not truncation) for over-length

**Contract Violation Testing**:
- Use injectable embedding provider for controlled responses
- Generate: NaN vector, Inf vector, wrong dimensions, wrong operation_id
- Verify each causes proper rejection

### Search Testing Strategy

**Filtering Verification**:
- Setup: High-scoring memory in wrong tenant/source/space
- Verify: Doesn't appear in candidates despite high score
- Use diagnostic client to inspect search filter expressions
- Compare with direct Milvus query to verify filter correctness

**Multi-chunk Handling**:
- Create memory with 3+ chunks (BEGIN/MIDDLE/END)
- Search returning only MIDDLE chunk
- Verify: One candidate entry, score from MIDDLE, all chunks loaded in body

**Pagination Testing**:
- Setup: First page dominated by one memory ID (multiple chunks)
- Mock search results to return controlled pages
- Verify: Pagination continues to find distinct memory IDs
- Verify: Stops at page/chunk/round limits

**Metadata Validation**:
- Use contract violation factory to return incomplete metadata
- Test: missing Ref, missing version, missing hash, missing generation
- Verify: Candidate rejected, error distinguishable from no-match

### Performance and Timeout Testing

**Inference Slot Management** (RC-EMB-10):
- Setup: Configure N inference slots
- Start slow inference job (doesn't complete)
- Submit N more requests
- Verify: New requests wait (don't exceed capacity)
- Release slow job
- Verify: Slot reclaimed

**Deadline Consumption** (RC-EMB-11):
- Setup: Request with tight deadline
- Consume time in parameter construction
- Verify: Remaining deadline passed to inference
- Verify: Late result rejected if past deadline

### Revocation During Encoding (RC-EMB-12)

**Test Pattern**:
- Start encoding (pause in CPU inference)
- Revoke permissions and wait for propagation
- Release encoding to complete
- Verify: Late-arriving vector doesn't produce successful result
- Verify: Authorization check at commitment prevents delivery

## Testing Decisions

### What Makes a Good Test
- Tests use **real models and tokenizers** to validate actual behavior
- Tests verify **contract compliance** at boundaries
- Tests verify **error isolation** (bad input rejected, doesn't corrupt state)
- Tests verify **security properties** (filtering enforced, revocation respected)

### Test Structure Patterns

**Embedding Test**:
```
async def test_rc_emb_01_native_query_vector():
    # Setup: Known query, real BGE model
    # Encode query
    # Verify: 512 dimensions
    # Verify: Normalized (L2 norm ≈ 1.0)
    # Verify: All components finite
    # Verify: Usage = Query
    # Verify: Space binding correct
```

**Search Test**:
```
async def test_rc_sea_01_filtering_before_topk():
    # Setup: f4_cross_tenant (high score in wrong tenant)
    # Direct Milvus query: Verify distractor scores higher
    # Recall with proper identity
    # Verify: Only authorized candidates returned
    # Verify: Lower-scoring authorized found
```

**Contract Violation Test**:
```
async def test_rc_emb_06_illegal_vectors():
    # Inject: NaN vector from provider
    # Submit recall
    # Verify: Rejected before Milvus
    # Verify: No successful pack generated
    # Repeat for: Inf, zero vector, wrong norm
```

### Modules to Test
- **Embedding**: Query encoding logic, vector validation
- **Search**: Milvus integration, filter construction, candidate aggregation
- **Diagnostic**: Encoding evidence, search traces

### Assertion Helpers
- `assert_valid_vector(vector, expected_dim, expected_norm)` - validate vector properties
- `assert_search_filtered(trace, expected_filters)` - verify filter application
- `assert_multi_chunk_candidate(candidate, expected_chunk_count)` - verify chunk handling

### Prior Art
- Vector validation from Remember indexing tests
- Search filtering from existing retrieval system tests
- Timeout handling from other async service tests

## Out of Scope

- **Model training**: How BGE model was trained
- **Embedding quality**: Semantic accuracy of embeddings
- **Search ranking quality**: Whether similarity scores reflect true relevance
- **Model serving**: How embedding service is deployed and scaled
- **Index maintenance**: How Milvus indices are built and updated

## Further Notes

### Model Configuration
Tests document actual model configuration:
- BGE-small-zh-v1.5: 512 dimensions, L2 normalized
- Usage prefixes: Query vs Passage
- Max input length: 512 tokens (including wrapper)
- Tokenizer: specific HuggingFace tokenizer

### Performance Expectations
- Embedding tests may be slower due to real model inference
- Consider session-scoped model loading
- Estimated time: ~3-5 minutes for embedding tests, ~2-3 minutes for search tests

### Blocked Requirements
- Normalization tolerance (exact threshold for L2 norm validation)
- Tie-breaking rules (deterministic ordering for same scores)
- Will document observed values

### Related Specs
- **Sources Tests**: Define which vector spaces to search
- **Qualification Tests**: Define post-search authorization
- **Fusion Tests**: Define how candidates from multiple sources combine
