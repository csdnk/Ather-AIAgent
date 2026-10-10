# JSON Canonicalization for Idempotency Tests (AET-87)

## Overview

This test suite implements RC-IDE-08 from the Recall idempotency specification, focusing on JSON field ordering, whitespace, and Unicode normalization to ensure idempotency signatures correctly identify semantically equivalent vs. different requests.

## Test Cases

### RC-IDE-08: JSON Canonicalization for Idempotency

**test_rc_ide_08_same_semantic_json_different_field_order**
- Tests whether different JSON field ordering produces same idempotency signature
- Verifies canonical ordering is applied if specified
- Ensures same semantic content is recognized as idempotent

**test_rc_ide_08_different_queries_different_signatures**
- Verifies different query content produces different signatures
- Ensures signature calculation is sensitive to semantic differences
- Confirms IDEMPOTENCY_CONFLICT raised for different content

**test_rc_ide_08_whitespace_normalization**
- Tests whether leading/trailing/internal whitespace affects signatures
- Checks if whitespace is normalized or considered significant
- Handles both normalized and non-normalized behaviors

**test_rc_ide_08_unicode_nfc_vs_nfd_normalization**
- Tests Unicode normalization forms (NFC vs NFD)
- Example: "café" as composed (é) vs decomposed (e + ́)
- Skips test if Unicode policy not specified (Q07)

**test_rc_ide_08_chinese_unicode_variants**
- Tests Chinese text with different Unicode sequences
- Verifies consistent handling of CJK characters
- Checks NFC vs NFD for Chinese text

**test_rc_ide_08_signature_binds_to_execution_semantics**
- Ensures different execution parameters produce different signatures
- Tests token_budget, sources, scope differences
- Verifies signatures reflect actual execution semantics

**test_rc_ide_08_json_indentation_differences**
- Tests JSON with different indentation/formatting
- Verifies formatting differences are normalized
- Ensures pretty-printed vs minified JSON treated as equivalent

**test_rc_ide_08_no_accidental_cache_reuse**
- Ensures different inputs don't accidentally reuse cached results
- Tests sensitivity of signature calculation
- Prevents false positive cache hits

**test_rc_ide_08_empty_vs_missing_fields_handled_correctly**
- Tests distinction between empty string and missing field
- Verifies null vs "" vs missing are handled correctly
- Ensures signature calculation accounts for field presence

## Implementation Details

### Unicode Normalization

Python's `unicodedata` module provides normalization:

```python
import unicodedata

# NFC: Composed form (café with composed é)
text_nfc = "café"

# NFD: Decomposed form (café with e + combining accent)
text_nfd = unicodedata.normalize("NFD", text_nfc)

# Check if they're different byte representations
assert text_nfc != text_nfd  # Different bytes
assert unicodedata.normalize("NFC", text_nfd) == text_nfc  # Same meaning
```

### JSON Canonicalization

Standard approaches to JSON canonicalization:

1. **RFC 8785 (JCS)**: Deterministic JSON serialization
   - Sorted object keys
   - No whitespace
   - Specific number formatting
   - UTF-8 encoding

2. **Field Ordering**: Alphabetical key sorting
3. **Whitespace Removal**: Strip all unnecessary whitespace
4. **Unicode Normalization**: NFC or NFD consistently applied

### Test Scenarios

**Scenario 1: Field Order**
```json
// Request A
{"query": "test", "budget": 128}

// Request B (different order, same content)
{"budget": 128, "query": "test"}

// Expected: Same signature if canonicalized
```

**Scenario 2: Whitespace**
```
Query A: "用户的咖啡加糖习惯是什么？"
Query B: " 用户的咖啡加糖习惯是什么？ "  (with spaces)

Expected: Different signatures if whitespace not normalized
         Same signature if whitespace trimmed/normalized
```

**Scenario 3: Unicode**
```
Query A: "café" (NFC: composed é)
Query B: "café" (NFD: decomposed e + ́)

Expected: Same signature if Unicode normalized
         Different signatures if byte-level comparison
```

## Expected Behavior

1. **Semantic Equivalence**: Same content with different formatting → same signature
2. **Semantic Differences**: Different content → different signatures
3. **No False Positives**: Different requests don't accidentally match
4. **No False Negatives**: Same requests always match (after normalization)
5. **Execution Semantics**: Signature reflects what will actually execute

## Running the Tests

```bash
pytest tests/unit/recall/test_json_canonicalization.py -v
```

For specific tests:
```bash
pytest tests/unit/recall/test_json_canonicalization.py::test_rc_ide_08_unicode_nfc_vs_nfd_normalization -v
```

## Dependencies

These tests build on:
- AET-82: Basic idempotency signature calculation
- Existing admission service request fingerprint logic

Related tickets:
- AET-83: Concurrent admission (concurrent requests with same signature)
- AET-84: Operation ID namespacing
- AET-85: Response loss recovery
- AET-86: Authorization changes

## Spec Clarifications (Q07)

Some behaviors depend on spec clarification:

1. **Unicode Normalization**: Should NFC/NFD be normalized? Which form?
2. **Whitespace**: Should leading/trailing/internal whitespace be normalized?
3. **JSON Field Order**: Must be canonicalized or can vary?
4. **Empty vs Missing**: Are empty strings equivalent to missing fields?
5. **Number Formatting**: Should 1.0 == 1 in signatures?

Tests mark these scenarios with `pytest.skip("blocked_requirement Q07")` when behavior is undefined.

## Production Considerations

In production, JSON canonicalization requires:

1. **Deterministic Serialization**: Use RFC 8785 or similar standard
2. **Unicode Policy**: Document and enforce NFC or NFD consistently
3. **Whitespace Handling**: Decide on normalization strategy
4. **Performance**: Canonicalization adds overhead - optimize for hot path
5. **Backward Compatibility**: Changing canonicalization breaks existing signatures

## Testing Limitations

These unit tests verify signature calculation logic but don't test:
- Raw HTTP JSON parsing (different parsers may normalize differently)
- Large JSON documents (performance implications)
- Binary data encoding (base64, etc.)
- Complex nested structures with multiple Unicode strings

Integration tests would be needed to verify end-to-end JSON handling across HTTP boundaries.

## Reference Standards

- **RFC 8785**: JSON Canonicalization Scheme (JCS)
- **Unicode Standard**: Normalization forms (NFC, NFD, NFKC, NFKD)
- **JSON RFC 8259**: JSON data interchange format
- **UTF-8 RFC 3629**: UTF-8 encoding
