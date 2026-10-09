# Recall Test Implementation Guide

**Project**: Agent Memory Management / Aether P3  
**Module**: Recall (召回)  
**Document Version**: 1.0  
**Date**: 2024-10-07  
**Status**: Design Complete, Ready for Implementation

## 1. Overview

This guide documents all design decisions for implementing the Recall test suite based on the test specification documents:
- `docs/refs/Recall_测试用例_精简版.md` (~150 test cases)
- `docs/refs/Recall_QA测试文档.md` (detailed execution guide)

The Recall module is responsible for retrieving relevant memories through query encoding, vector search, qualification, fusion, reranking, and budget-constrained assembly.

## 2. Test Architecture

### 2.1 Test Scope
- **Focus**: End-to-end integration tests through public HTTP API (`POST /p3/recall`, `GET /p3/recalls/{id}`)
- **Secondary**: Stage-level inspection via `DiagnosticClient` for internal verification
- **Out of Scope**: Remember (data preparation only) and Operate (event verification only)

### 2.2 Test Framework
- **Framework**: pytest with pytest-asyncio
- **Test Functions**: All async (`async def test_*`)
- **Execution**: Randomizable with `pytest-randomly` to catch hidden dependencies
- **Isolation**: Test function level with unique tenant namespacing

### 2.3 Project Structure
```
tests/
├── recall/
│   ├── __init__.py
│   ├── conftest.py                    # Shared fixtures and configuration
│   ├── config.py                      # Configuration management
│   ├── client.py                      # RecallTestClient implementation
│   ├── diagnostic_client.py           # DiagnosticClient for stage inspection
│   ├── assertions.py                  # Semantic assertion helpers
│   ├── evidence.py                    # Evidence collection & redaction
│   ├── blocked_requirements.yaml      # Registry of blocked tests
│   ├── token_utils.py                 # Token calculation utilities
│   ├── test_data_generators.py        # Data variation generators
│   │
│   ├── fixtures/
│   │   ├── __init__.py
│   │   ├── catalog.py                 # Fixture registry (F, U, B registries)
│   │   ├── f1_working_ready.py        # Working + longterm memories
│   │   ├── f2_longterm_ready.py       # Longterm memory fixture
│   │   ├── f4_cross_tenant.py         # Cross-tenant distractor data
│   │   └── ...                        # Other [F0]-[F12] fixtures
│   │
│   ├── identities/
│   │   ├── __init__.py
│   │   ├── factory.py                 # IdentityFactory implementation
│   │   └── personas.py                # [U01]-[U08] persona definitions
│   │
│   ├── contract_violations/
│   │   ├── __init__.py
│   │   ├── remember_violations.py     # Remember contract violation generators
│   │   └── embedding_violations.py    # Embedding contract violation generators
│   │
│   ├── barriers/
│   │   ├── __init__.py
│   │   ├── stage_hooks.py             # TestableStageRegistry implementation
│   │   └── barrier_definitions.py     # [B0]-[B7] barrier point definitions
│   │
│   ├── test_api_contract.py           # RC-API-01 through RC-API-12 (12 tests)
│   ├── test_idempotency.py            # RC-IDE-01 through RC-IDE-08 (8 tests)
│   ├── test_auth.py                   # RC-AUTH-01 through RC-AUTH-15 (15 tests)
│   ├── test_sources.py                # RC-SRC-01 through RC-SRC-14 (14 tests)
│   ├── test_embedding.py              # RC-EMB-01 through RC-EMB-14 (14 tests)
│   ├── test_search.py                 # RC-SEA-01 through RC-SEA-11 (11 tests)
│   ├── test_qualification.py          # RC-QUA-01 through RC-QUA-08 (8 tests)
│   ├── test_fusion.py                 # RC-FUS-01 through RC-FUS-08 (8 tests)
│   ├── test_reranking.py              # RC-RER-01 through RC-RER-08 (8 tests)
│   ├── test_body.py                   # RC-BODY-01 through RC-BODY-08 (8 tests)
│   ├── test_relations.py              # RC-REL-01 through RC-REL-07 (7 tests)
│   └── test_budget.py                 # RC-BUD-01 through RC-BUD-08 (8 tests)
│
└── plugins/
    ├── evidence_collector.py          # pytest plugin for auto evidence collection
    ├── traceability_reporter.py       # pytest plugin for traceability matrix
    └── timing_reporter.py             # pytest plugin for performance metrics
```

## 3. Configuration Management

### 3.1 Layered Configuration
Configuration priority (highest to lowest):
1. CLI arguments (`pytest --recall-url=...`)
2. Environment variables (`RECALL_API_URL`, `PG_TEST_DATABASE`)
3. Config file (`tests/config/test_environment.yaml`)
4. Defaults (in `tests/recall/config.py`)

### 3.2 Required Configuration
```yaml
# tests/config/test_environment.yaml
recall_api:
  base_url: "http://localhost:8080"
  timeout: 30.0

backends:
  postgresql:
    host: "localhost"
    port: 5432
    database: "aether_test"
    schema_prefix: "test_"
  redis:
    host: "localhost"
    port: 6379
    namespace_prefix: "test:"
  milvus:
    host: "localhost"
    port: 19530
    database: "aether_test"
  ceph:
    endpoint: "http://localhost:9000"
    bucket_prefix: "test-"
  temporal:
    host: "localhost"
    port: 7233
    namespace: "test"

models:
  embedding:
    name: "BGE-small-zh-v1.5"
    dimensions: 512
    max_query_length: 512
  tokenizer:
    name: "o200k_base"
  reranker:
    enabled: true
    max_pair_length: 1024

policies:
  top_k_per_source: 3
  max_pages: 5
  max_chunks: 100
  max_rounds: 3
  default_token_budget: 1000
  min_token_budget: 100
  max_token_budget: 10000
```

## 4. Fixture System

### 4.1 Naming Convention
- **Hybrid naming**: `{code}_{descriptive_name}` (e.g., `f1_working_ready`, `u01_tenant_a_read`)
- **Registry access**: Import from catalog (`from tests.recall.fixtures import F`) → `F.F1`

### 4.2 Fixture Categories

#### Data Fixtures ([F0]-[F12])
```python
# tests/recall/fixtures/catalog.py
class FixtureRegistry:
    """Registry for accessing fixtures by spec code."""
    
    @property
    def F0(self) -> Fixture:
        """Empty authorized scope - no memories."""
        return self._registry['f0_empty_scope']
    
    @property
    def F1(self) -> Fixture:
        """Working memories: coffee (no sugar) + tea (oolong), v1/g1 Ready."""
        return self._registry['f1_working_ready']
    
    # ... F2 through F12

F = FixtureRegistry()
```

#### Identity Fixtures ([U01]-[U08])
```python
# tests/recall/identities/factory.py
class IdentityFactory:
    """Creates and tracks test identities."""
    
    def create(self, 
               tenant: str,
               application: str,
               user: str,
               agent: str,
               permissions: list[str]) -> Identity:
        """Create identity with specified attributes."""
        
    def get(self, code: str) -> Identity:
        """Get identity by spec code (U01, U02, etc.)."""
        
    def revoke_permission(self, identity: Identity, permission: str):
        """Revoke permission and increment auth epoch."""
        
    def increment_epoch(self, identity: Identity):
        """Increment identity's auth_epoch (tenant suspend/resume)."""
```

#### Barrier Fixtures ([B0]-[B7])
```python
# tests/recall/barriers/barrier_definitions.py
class BarrierPoints:
    """Stage execution barrier points."""
    B0 = "before_candidates"  # Before candidate discovery starts
    B1 = "after_candidates"   # After candidates discovered
    B2 = "after_qualify"      # After Remember qualification
    B3 = "after_body_load"    # After full body loading
    B4 = "before_rerank"      # Before reranking
    B5 = "before_final_guard" # Before final authorization guard
    B6 = "after_assembly"     # After context pack assembly
    B7 = "before_commit"      # Before result commit
```

### 4.3 Fixture Scope Strategy
- **Default**: Function-scoped (fresh per test)
- **Expensive fixtures**: Session-scoped with explicit declaration
  - `f4_cross_tenant` (requires multiple tenants with distractor data)
  - Embedding model loading
  - Test environment validation

### 4.4 Fixture Lifecycle
```python
@pytest.fixture
async def f1_working_ready(remember_client, identity_factory):
    """Working coffee + tea memories, v1/g1 Ready.
    
    Creates:
    - Memory 1: "用户喝咖啡不加糖。" (User drinks coffee without sugar)
    - Memory 2: "用户喜欢乌龙茶。" (User likes oolong tea)
    
    Both indexed and Ready in working source.
    """
    identity = identity_factory.get("U01")
    
    # Create memories in parallel
    memories = await asyncio.gather(
        remember_client.create_memory(
            identity, 
            content="用户喝咖啡不加糖。",
            memory_type="Working"
        ),
        remember_client.create_memory(
            identity,
            content="用户喜欢乌龙茶。",
            memory_type="Working"
        )
    )
    
    # Wait for indexing to complete (Ready state)
    await remember_client.wait_for_ready(memories, timeout=30.0)
    
    fixture = Fixture(
        memories=memories,
        identity=identity,
        source="working"
    )
    
    yield fixture
    
    # Cleanup: delete memories if test passed
    if not pytest_runtest_outcome.failed:
        await remember_client.delete_memories(memories)
```

## 5. Test Client Architecture

### 5.1 RecallTestClient
```python
# tests/recall/client.py
class RecallTestClient:
    """HTTP client for Recall API with evidence tracking."""
    
    def __init__(self, base_url: str, evidence_collector: EvidenceCollector):
        self.base_url = base_url
        self.evidence = evidence_collector
        self._client = httpx.AsyncClient(timeout=30.0)
    
    async def submit_recall(
        self,
        identity: Identity,
        request: RecallRequest,
        operation_id: str | None = None
    ) -> RecallSubmissionResponse:
        """Submit recall request with automatic evidence capture."""
        
    async def get_recall(
        self,
        identity: Identity,
        recall_id: str
    ) -> RecallRecord:
        """Get recall record."""
        
    async def get_result(
        self,
        identity: Identity,
        recall_id: str
    ) -> ContextPack:
        """Get recall result with authorization check."""
        
    async def wait_for_completion(
        self,
        identity: Identity,
        recall_id: str,
        timeout: float = 60.0
    ) -> ContextPack:
        """Poll until recall completes or timeout."""
```

### 5.2 DiagnosticClient
```python
# tests/recall/diagnostic_client.py
class DiagnosticClient:
    """Access to internal execution state for verification."""
    
    def __init__(self, identity: Identity, ledger: ExecutionLedger):
        if "DIAGNOSE" not in identity.permissions:
            raise PermissionError("Requires DIAGNOSE permission")
        self.identity = identity
        self.ledger = ledger
    
    async def get_stage_output(
        self,
        recall_id: str,
        stage: str
    ) -> dict:
        """Get output from specific execution stage."""
        
    async def get_qualification_decisions(
        self,
        recall_id: str
    ) -> list[QualificationDecision]:
        """Get Remember qualification decisions with evidence."""
        
    async def get_events(
        self,
        recall_id: str,
        event_type: str | None = None
    ) -> list[Event]:
        """Get execution events."""
        
    async def get_trace(
        self,
        recall_id: str
    ) -> ExecutionTrace:
        """Get complete execution trace."""
```

## 6. Test Implementation Patterns

### 6.1 Basic Test Structure
```python
@pytest.mark.p0
@pytest.mark.api_contract
@pytest.mark.requires_milvus
async def test_rc_api_01_working_request_e2e(
    recall_client: RecallTestClient,
    f1_working_ready: Fixture,
    u01_tenant_a_read: Identity
):
    """RC-API-01: Working request end-to-end verification.
    
    Spec: docs/refs/Recall_测试用例_精简版.md#case-rc-api-01
    Priority: P0
    Dependencies: S01, S03
    
    Preconditions:
    - Working coffee and tea memories v1/g1 Ready
    - Authorized caller with READ permission
    - Specified real session
    - Query: "用户的咖啡加糖习惯是什么？"
    - sources=working, token_budget=1000
    
    Expected:
    - Result contains target Ref's complete authoritative content
    - Source is working
    - Total pack tokens <= 1000
    - Result associated with original request
    - Query encoding, search, guard stages have evidence
    """
    # Prepare request
    request = RecallRequest(
        query="用户的咖啡加糖习惯是什么？",
        selection={"session_id": f1_working_ready.session_id},
        sources="working",
        token_budget=1000
    )
    
    # Submit recall
    response = await recall_client.submit_recall(
        identity=u01_tenant_a_read,
        request=request,
        operation_id=f"test-rc-api-01-{uuid.uuid4()}"
    )
    
    # Wait for completion
    result = await recall_client.wait_for_completion(
        identity=u01_tenant_a_read,
        recall_id=response.recall_id,
        timeout=60.0
    )
    
    # Verify successful recall
    assert_successful_recall(
        result=result,
        expected_refs=f1_working_ready.memory_refs,
        token_budget=1000,
        expected_source="working"
    )
    
    # Verify stages completed
    diagnostic = DiagnosticClient(u01_tenant_a_read, recall_client.ledger)
    trace = await diagnostic.get_trace(response.recall_id)
    
    assert "query_encoding" in trace.completed_stages
    assert "vector_search" in trace.completed_stages
    assert "final_guard" in trace.completed_stages
```

### 6.2 Parametrized Test Pattern
```python
@pytest.mark.p1
@pytest.mark.api_contract
@pytest.mark.parametrize("query_variant,expected_behavior", [
    (None, "reject_missing"),
    ("", "reject_empty"),
    ("   ", "reject_whitespace"),
    (123, "reject_type"),
    (["array"], "reject_type"),
], ids=["missing", "empty", "whitespace", "number", "array"])
async def test_rc_api_02_query_validation(
    recall_client: RecallTestClient,
    f1_working_ready: Fixture,
    u01_tenant_a_read: Identity,
    query_variant,
    expected_behavior
):
    """RC-API-02: Query null value and type validation.
    
    Spec: docs/refs/Recall_测试用例_精简版.md#case-rc-api-02
    Priority: P1
    Dependencies: Q01
    """
    request_data = {
        "selection": {"session_id": f1_working_ready.session_id},
        "sources": "working",
        "token_budget": 1000
    }
    
    if query_variant is not None:
        request_data["query"] = query_variant
    
    if expected_behavior.startswith("reject"):
        with pytest.raises(httpx.HTTPStatusError) as exc_info:
            await recall_client.submit_recall(
                identity=u01_tenant_a_read,
                request=request_data
            )
        
        # Verify proper rejection
        assert exc_info.value.response.status_code in [400, 422]
        error = exc_info.value.response.json()
        assert "query" in error.get("message", "").lower()
    else:
        # Should succeed with this variant
        response = await recall_client.submit_recall(
            identity=u01_tenant_a_read,
            request=request_data
        )
        assert response.recall_id is not None
```

### 6.3 Concurrent Test Pattern
```python
@pytest.mark.p0
@pytest.mark.idempotency
@pytest.mark.concurrent
async def test_rc_ide_03_concurrent_admission(
    recall_client: RecallTestClient,
    f1_working_ready: Fixture,
    u01_tenant_a_read: Identity,
    barrier_registry: BarrierRegistry
):
    """RC-IDE-03: Concurrent admission with same ID and body.
    
    Spec: docs/refs/Recall_测试用例_精简版.md#case-rc-ide-03
    Priority: P0
    Dependencies: S02, S07
    """
    # Set up barrier before candidates stage
    barrier = asyncio.Event()
    barrier_registry.register("candidates", "before", barrier)
    
    # Prepare identical requests
    operation_id = f"test-rc-ide-03-{uuid.uuid4()}"
    request = RecallRequest(
        query="用户的咖啡加糖习惯是什么？",
        selection={"session_id": f1_working_ready.session_id},
        sources="working",
        token_budget=1000
    )
    
    # Launch concurrent submissions
    async def submit_client(client_id: str):
        return await recall_client.submit_recall(
            identity=u01_tenant_a_read,
            request=request,
            operation_id=operation_id
        )
    
    # Both clients submit simultaneously
    client1_task = asyncio.create_task(submit_client("client1"))
    client2_task = asyncio.create_task(submit_client("client2"))
    
    # Wait a bit for both to reach barrier
    await asyncio.sleep(0.5)
    
    # Release barrier
    barrier.set()
    
    # Get responses
    response1, response2 = await asyncio.gather(
        client1_task, 
        client2_task,
        return_exceptions=True
    )
    
    # Both should reference the same operation
    assert response1.recall_id == response2.recall_id
    
    # Wait for completion
    result = await recall_client.wait_for_completion(
        identity=u01_tenant_a_read,
        recall_id=response1.recall_id
    )
    
    # Verify single result exists
    assert_successful_recall(result, f1_working_ready.memory_refs, 1000)
    
    # Verify no duplicate execution
    diagnostic = DiagnosticClient(u01_tenant_a_read, recall_client.ledger)
    events = await diagnostic.get_events(response1.recall_id, event_type="packed")
    assert len(events) == 1, "Should have exactly one packed event"
```

### 6.4 Authorization Test Pattern
```python
@pytest.mark.p0
@pytest.mark.auth
@pytest.mark.security
async def test_rc_auth_03_cross_tenant_isolation(
    recall_client: RecallTestClient,
    f4_cross_tenant: CrossTenantFixture,
    u01_tenant_a_read: Identity,
    u04_tenant_b_read: Identity
):
    """RC-AUTH-03: Cross-tenant same-name ID isolation.
    
    Spec: docs/refs/Recall_测试用例_精简版.md#case-rc-auth-03
    Priority: P0
    Dependencies: S03, S08
    
    Preconditions:
    - Tenant A and B have memories with same user/session/memory IDs
    - Valid target score lower than external tenant objects
    - Server K=1
    """
    # Same query for both tenants
    query = "咖啡加糖习惯"
    
    # Submit from Tenant A
    result_a = await recall_client.submit_and_wait(
        identity=u01_tenant_a_read,
        request=RecallRequest(
            query=query,
            selection={"session_id": f4_cross_tenant.shared_session_id},
            sources="working",
            token_budget=1000
        )
    )
    
    # Submit from Tenant B
    result_b = await recall_client.submit_and_wait(
        identity=u04_tenant_b_read,
        request=RecallRequest(
            query=query,
            selection={"session_id": f4_cross_tenant.shared_session_id},
            sources="working",
            token_budget=1000
        )
    )
    
    # Verify tenant isolation
    assert_no_cross_tenant_leakage(
        tenant_a_result=result_a,
        tenant_a_memories=f4_cross_tenant.tenant_a_memories,
        tenant_b_result=result_b,
        tenant_b_memories=f4_cross_tenant.tenant_b_memories
    )
    
    # Verify no collision in job/recall IDs despite same operation semantics
    assert result_a.recall_id != result_b.recall_id
```

### 6.5 Contract Violation Test Pattern
```python
@pytest.mark.p0
@pytest.mark.qualification
@pytest.mark.contract_violation
async def test_rc_qua_02_missing_qualification_evidence(
    recall_client: RecallTestClient,
    f1_working_ready: Fixture,
    u01_tenant_a_read: Identity,
    remember_violations: RememberViolationFactory,
    injectable_remember: InjectableRememberClient
):
    """RC-QUA-02: Allowed decision with missing/mismatched evidence.
    
    Spec: docs/refs/Recall_测试用例_精简版.md#case-rc-qua-02
    Priority: P0
    Dependencies: S02, S04
    
    Tests that qualification cannot succeed without proper evidence,
    even if decision=allowed.
    """
    # Inject violated responses
    violations = [
        remember_violations.missing_qualification_manifest(),
        remember_violations.missing_qualification_guard(),
        remember_violations.mismatched_qualification_hash()
    ]
    
    for violation in violations:
        injectable_remember.inject_response(
            "qualify",
            violation
        )
        
        # Submit recall
        with pytest.raises(RecallExecutionError) as exc_info:
            await recall_client.submit_and_wait(
                identity=u01_tenant_a_read,
                request=RecallRequest(
                    query="咖啡加糖习惯",
                    selection={"session_id": f1_working_ready.session_id},
                    sources="working",
                    token_budget=1000
                )
            )
        
        # Verify contract error (not just excluded candidate)
        assert exc_info.value.error_code == ErrorCode.CONTRACT_VIOLATION
        assert "qualification" in exc_info.value.message.lower()
        assert "evidence" in exc_info.value.message.lower()
```

## 7. Assertion Helpers

### 7.1 Success Assertions
```python
# tests/recall/assertions.py

def assert_successful_recall(
    result: ContextPack,
    expected_refs: list[MemoryRef],
    token_budget: int,
    expected_source: str | None = None
):
    """Verify successful recall with complete result.
    
    Checks:
    - Result contains expected memory references
    - Token count within budget
    - Source matches if specified
    - All required fields present
    """
    assert result.items, "Result should contain context items"
    
    # Verify token budget
    actual_tokens = count_tokens(result.rendered_context)
    assert actual_tokens <= token_budget, \
        f"Token count {actual_tokens} exceeds budget {token_budget}"
    
    # Verify expected refs present
    result_refs = {item.memory_ref for item in result.items}
    expected_ref_ids = {ref.memory_id for ref in expected_refs}
    assert expected_ref_ids.issubset(result_refs), \
        f"Missing expected refs: {expected_ref_ids - result_refs}"
    
    # Verify source if specified
    if expected_source:
        sources = {item.source for item in result.items}
        assert sources == {expected_source}, \
            f"Expected source {expected_source}, got {sources}"
```

### 7.2 Authorization Assertions
```python
def assert_authorization_denied(
    response: httpx.Response | Exception,
    should_not_leak: list[str] | None = None
):
    """Verify proper authorization denial without leakage.
    
    Checks:
    - Appropriate status code (401/403)
    - Error structure is valid
    - No sensitive information leaked
    """
    if isinstance(response, httpx.Response):
        assert response.status_code in [401, 403]
        error = response.json()
    else:
        assert isinstance(response, AuthorizationError)
        error = response.error_details
    
    # Verify no leakage
    error_text = json.dumps(error).lower()
    if should_not_leak:
        for sensitive in should_not_leak:
            assert sensitive not in error_text, \
                f"Leaked sensitive info: {sensitive}"

def assert_no_cross_tenant_leakage(
    tenant_a_result: ContextPack,
    tenant_a_memories: list[Memory],
    tenant_b_result: ContextPack,
    tenant_b_memories: list[Memory]
):
    """Verify complete tenant isolation.
    
    Checks:
    - Tenant A result contains only A's memories
    - Tenant B result contains only B's memories
    - No memory ID overlap
    - No content leakage
    """
    a_result_ids = {item.memory_ref.memory_id for item in tenant_a_result.items}
    a_expected_ids = {m.memory_id for m in tenant_a_memories}
    b_result_ids = {item.memory_ref.memory_id for item in tenant_b_result.items}
    b_expected_ids = {m.memory_id for m in tenant_b_memories}
    
    # A's result should only contain A's memories
    assert a_result_ids.issubset(a_expected_ids), \
        f"Tenant A result contains foreign IDs: {a_result_ids - a_expected_ids}"
    
    # B's result should only contain B's memories
    assert b_result_ids.issubset(b_expected_ids), \
        f"Tenant B result contains foreign IDs: {b_result_ids - b_expected_ids}"
    
    # No overlap
    assert not (a_result_ids & b_result_ids), \
        f"Cross-tenant memory leakage: {a_result_ids & b_result_ids}"
```

### 7.3 Idempotency Assertions
```python
def assert_idempotent_retry(
    first_response: RecallSubmissionResponse,
    second_response: RecallSubmissionResponse,
    first_result: ContextPack,
    second_result: ContextPack
):
    """Verify idempotent retry returns same operation.
    
    Checks:
    - Same recall_id returned
    - Same result content
    - Same result hash
    - No duplicate execution
    """
    assert first_response.recall_id == second_response.recall_id, \
        "Idempotent retry should return same recall_id"
    
    assert first_result.pack_hash == second_result.pack_hash, \
        "Idempotent retry should return identical result"
    
    # Content should match exactly
    assert first_result.rendered_context == second_result.rendered_context
    assert len(first_result.items) == len(second_result.items)
```

## 8. Evidence Collection

### 8.1 Automatic Collection on Failure
```python
# tests/plugins/evidence_collector.py

class EvidenceCollectorPlugin:
    """Pytest plugin for automatic evidence collection."""
    
    def pytest_runtest_makereport(self, item, call):
        """Collect evidence when test fails."""
        if call.when == "call" and call.excinfo is not None:
            evidence = self.collect_test_evidence(item, call)
            self.save_evidence(item.nodeid, evidence)
    
    def collect_test_evidence(self, item, call) -> Evidence:
        """Collect comprehensive test evidence."""
        return Evidence(
            test_id=item.nodeid,
            timestamp_utc=datetime.now(timezone.utc),
            timestamp_shanghai=datetime.now(ZoneInfo("Asia/Shanghai")),
            failure_info=self.extract_failure_info(call),
            http_requests=self.get_captured_requests(item),
            http_responses=self.get_captured_responses(item),
            recall_ids=self.get_recall_ids(item),
            operation_ids=self.get_operation_ids(item),
            diagnostic_traces=self.get_diagnostic_traces(item),
            backend_bindings=self.get_backend_bindings(item),
            stage_outputs=self.get_stage_outputs(item),
            events=self.get_events(item)
        )
    
    def save_evidence(self, test_id: str, evidence: Evidence):
        """Save evidence to JSON with redaction."""
        redacted = redact_evidence(evidence)
        
        output_dir = Path("test_results/evidence")
        output_dir.mkdir(parents=True, exist_ok=True)
        
        filename = f"{test_id.replace('::', '_')}_{evidence.timestamp_utc.isoformat()}.json"
        filepath = output_dir / filename
        
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(redacted.model_dump(), f, indent=2, ensure_ascii=False)
```

### 8.2 Evidence Redaction
```python
# tests/recall/evidence.py

def redact_evidence(evidence: Evidence) -> Evidence:
    """Redact sensitive information while preserving structure."""
    
    redacted = evidence.model_copy(deep=True)
    
    # Redact Bearer tokens
    for req in redacted.http_requests:
        if 'Authorization' in req.headers:
            token = req.headers['Authorization']
            if token.startswith('Bearer '):
                # Keep prefix and last 4 chars
                req.headers['Authorization'] = mask_token(token)
    
    # Hash tenant/user IDs to stable pseudonyms
    for trace in redacted.diagnostic_traces:
        trace.tenant_id = hash_to_pseudonym(trace.tenant_id)
        trace.user_id = hash_to_pseudonym(trace.user_id)
    
    # Truncate content to first 100 chars
    for item in redacted.stage_outputs:
        if 'body' in item:
            item['body'] = truncate_content(item['body'], max_length=100)
    
    return redacted

def mask_token(token: str) -> str:
    """Mask token keeping structure visible."""
    if len(token) <= 20:
        return "Bearer ***"
    return f"{token[:10]}***{token[-4:]}"

def hash_to_pseudonym(identifier: str) -> str:
    """Hash identifier to stable pseudonym."""
    hash_bytes = hashlib.sha256(identifier.encode()).digest()
    return f"ID-{hash_bytes.hex()[:8]}"

def truncate_content(content: str, max_length: int = 100) -> str:
    """Truncate content with indicator."""
    if len(content) <= max_length:
        return content
    return f"{content[:max_length]}... [truncated, {len(content)} total chars]"
```

## 9. Blocked Requirements

### 9.1 Registry Format
```yaml
# tests/recall/blocked_requirements.yaml

Q01:
  description: "Request contract details: token_budget min/max, selection defaults, sources default/case-sensitivity, HTTP method mappings, operation ID format requirements"
  blocked_tests:
    - RC-API-04  # token_budget boundaries
    - RC-API-05  # sources default and case handling
    - RC-API-06  # selection defaults
    - RC-API-11  # HTTP method requirements
    - RC-IDE-05  # operation ID format/generation
  priority: high
  blocking_since: "2024-10-07"
  status: awaiting_spec
  notes: "Need formal API schema with constraints"

Q07:
  description: "Idempotency signature: JSON canonicalization, Unicode normalization, whitespace handling, field ordering, duplicate key policy"
  blocked_tests:
    - RC-IDE-08  # JSON expression differences
    - RC-API-10  # Duplicate JSON keys
  priority: medium
  blocking_since: "2024-10-07"
  status: awaiting_spec
  notes: "Need signing algorithm specification"

Q09:
  description: "Coverage policy: pending/failed index handling, partial results allowed/denied, wait vs immediate failure"
  blocked_tests:
    - RC-SRC-07  # pending index behavior
    - RC-SRC-09  # mixed ready+pending
  priority: high
  blocking_since: "2024-10-07"
  status: awaiting_spec
  notes: "Need coverage completeness policy"

Q13:
  description: "Empty vs blocked distinction: public error responses for 'no matches' vs 'all excluded by permissions'"
  blocked_tests:
    - RC-AUTH-15  # Authorization denial vs normal empty
  priority: high
  blocking_since: "2024-10-07"
  status: security_decision_needed
  notes: "Balance between user experience and information disclosure"

Q18:
  description: "Identifier not found vs no permission: HTTP status and response structure for non-existent vs unauthorized resource access"
  blocked_tests:
    - RC-API-12  # Non-existent/invalid recall_id
  priority: medium
  blocking_since: "2024-10-07"
  status: awaiting_spec
  notes: "Standard error response patterns needed"
```

### 9.2 Using Blocked Registry
```python
# tests/recall/conftest.py

def pytest_configure(config):
    """Load blocked requirements registry."""
    with open('tests/recall/blocked_requirements.yaml') as f:
        blocked = yaml.safe_load(f)
    config.blocked_requirements = blocked

def pytest_collection_modifyitems(config, items):
    """Auto-skip blocked test variants."""
    blocked = config.blocked_requirements
    
    for item in items:
        # Extract test case ID from markers or docstring
        test_id = extract_test_case_id(item)
        
        if test_id:
            # Check if test is blocked
            for question_id, info in blocked.items():
                if test_id in info['blocked_tests']:
                    reason = f"Blocked by {question_id}: {info['description']}"
                    item.add_marker(pytest.mark.skip(reason=reason))
                    item.add_marker(pytest.mark.blocked(question_id=question_id))
```

## 10. Test Markers

### 10.1 Marker Definitions
```ini
# pytest.ini

[pytest]
markers =
    # Priority markers
    p0: Priority 0 - Critical functionality
    p1: Priority 1 - Important functionality  
    p2: Priority 2 - Nice to have
    
    # Category markers
    api_contract: Request contract validation tests
    idempotency: Idempotency and admission tests
    auth: Authorization and permission tests
    sources: Source selection and coverage tests
    embedding: Query embedding and model space tests
    search: Vector search and candidate tests
    qualification: Remember qualification tests
    fusion: RRF fusion tests
    reranking: CrossEncoder reranking tests
    body: Body retrieval tests
    relations: Relationship group tests
    budget: Token budget tests
    
    # Infrastructure markers
    requires_milvus: Requires Milvus vector database
    requires_temporal: Requires Temporal workflow engine
    requires_ceph: Requires Ceph object storage
    requires_remember: Requires Remember service
    
    # Execution markers
    slow: Test takes >5 seconds
    concurrent: Tests concurrent execution behavior
    security: Security-critical test
    contract_violation: Tests contract violation handling
    
    # Status markers
    blocked: Test is blocked awaiting specification
    flaky: Test has known intermittent failures
```

### 10.2 Marker Usage Examples
```bash
# Run all P0 tests
pytest -m p0

# Run P0 tests excluding slow ones
pytest -m "p0 and not slow"

# Run all auth and security tests
pytest -m "auth or security"

# Run only tests that don't require external services
pytest -m "not (requires_milvus or requires_temporal)"

# Run P0 contract and auth tests
pytest -m "p0 and (api_contract or auth)"
```

## 11. Test Execution

### 11.1 Running Tests
```bash
# Run all recall tests
pytest tests/recall/

# Run specific category
pytest tests/recall/test_auth.py

# Run specific test
pytest tests/recall/test_auth.py::test_rc_auth_03_cross_tenant_isolation

# Run with specific markers
pytest -m "p0 and security"

# Run with detailed output
pytest tests/recall/ -v --tb=short

# Run with evidence collection
pytest tests/recall/ --collect-evidence

# Run in parallel (if tests are truly independent)
pytest tests/recall/ -n auto

# Run with coverage
pytest tests/recall/ --cov=aether_agent_memory.recall --cov-report=html
```

### 11.2 Test Reports
After test execution, generate reports:

```bash
# Generate traceability matrix
pytest tests/recall/ --generate-traceability

# Generate blocked requirements report
pytest tests/recall/ --report-blocked

# Generate timing report
pytest tests/recall/ --report-timing

# Generate HTML report with all sections
pytest tests/recall/ --html=test_results/report.html --self-contained-html
```

## 12. Cleanup Strategy

### 12.1 Cleanup Registry
```python
# tests/recall/conftest.py

@pytest.fixture
def cleanup_registry():
    """Track resources for cleanup."""
    registry = CleanupRegistry()
    yield registry
    
    # Cleanup only if test passed
    if not pytest_current_test_outcome.failed:
        await registry.cleanup_all()
    else:
        # Preserve for debugging
        logger.info(f"Preserving resources for failed test: {registry.resources}")

class CleanupRegistry:
    """Registry of test resources to clean up."""
    
    def __init__(self):
        self.memories: list[str] = []
        self.operations: list[str] = []
        self.recalls: list[str] = []
        self.tenants: list[str] = []
    
    def register_memory(self, memory_id: str):
        """Register memory for cleanup."""
        self.memories.append(memory_id)
    
    async def cleanup_all(self):
        """Best-effort cleanup of all registered resources."""
        errors = []
        
        # Clean up in reverse dependency order
        for recall_id in self.recalls:
            try:
                await self._delete_recall(recall_id)
            except Exception as e:
                errors.append(f"Failed to delete recall {recall_id}: {e}")
        
        for memory_id in self.memories:
            try:
                await self._delete_memory(memory_id)
            except Exception as e:
                errors.append(f"Failed to delete memory {memory_id}: {e}")
        
        if errors:
            logger.warning(f"Cleanup errors: {errors}")
```

### 12.2 Database Transaction Isolation
```python
# tests/recall/conftest.py

@pytest.fixture
async def isolated_database():
    """Provide database transaction isolation where possible."""
    async with database.transaction() as tx:
        yield tx
        # Rollback at end of test
        await tx.rollback()
```

## 13. Implementation Priority

### Phase 1: Foundation (Week 1)
1. Test infrastructure setup
   - `config.py` - configuration management
   - `client.py` - RecallTestClient
   - `conftest.py` - basic fixtures
2. Fixture catalog
   - `fixtures/catalog.py` - registry structure
   - `fixtures/f1_working_ready.py` - basic data fixture
   - `identities/factory.py` - identity management
3. Basic assertions
   - `assertions.py` - success assertions
4. First test category
   - `test_api_contract.py` - RC-API-01 (basic e2e)

### Phase 2: Core Tests (Week 2)
1. Complete API contract tests (RC-API-01 through RC-API-12)
2. Idempotency tests (RC-IDE-01 through RC-IDE-08)
3. Evidence collection plugin
4. Diagnostic client implementation

### Phase 3: Security & Authorization (Week 3)
1. Authorization tests (RC-AUTH-01 through RC-AUTH-15)
2. Cross-tenant isolation verification
3. Security assertion helpers
4. Contract violation factory

### Phase 4: Recall Core Logic (Week 4-5)
1. Source selection tests (RC-SRC-01 through RC-SRC-14)
2. Embedding tests (RC-EMB-01 through RC-EMB-14)
3. Search tests (RC-SEA-01 through RC-SEA-11)
4. Qualification tests (RC-QUA-01 through RC-QUA-08)

### Phase 5: Advanced Features (Week 6)
1. Fusion tests (RC-FUS-01 through RC-FUS-08)
2. Reranking tests (RC-RER-01 through RC-RER-08)
3. Body retrieval tests (RC-BODY-01 through RC-BODY-08)
4. Relations tests (RC-REL-01 through RC-REL-07)
5. Budget tests (RC-BUD-01 through RC-BUD-08)

### Phase 6: Polish & Documentation (Week 7)
1. Complete all blocked requirement handling
2. Generate traceability matrix
3. Performance optimization
4. Documentation and examples

## 14. Success Criteria

The test implementation is complete when:

✅ All 150+ test cases implemented with proper spec traceability  
✅ All P0 tests (60+) pass consistently  
✅ Evidence collection working for all failed tests  
✅ Traceability matrix maps all test cases to spec requirements  
✅ Blocked requirements documented with skip markers  
✅ Security tests verify no cross-tenant/cross-user leakage  
✅ Idempotency tests verify proper operation ID handling  
✅ Contract violation tests verify proper error handling  
✅ All fixtures create realistic test data via Remember  
✅ Test execution time <30 minutes for full suite  
✅ Test suite can run in parallel without conflicts  
✅ HTML report generation with custom sections working  
✅ README documentation complete for each test category

## 15. Next Steps

You are now ready to begin implementation. Start with:

1. **Create the basic structure**:
   ```bash
   mkdir -p tests/recall/{fixtures,identities,barriers,contract_violations}
   mkdir -p tests/plugins
   touch tests/recall/{conftest,config,client,assertions,evidence}.py
   ```

2. **Implement configuration management** (`tests/recall/config.py`)

3. **Implement RecallTestClient** (`tests/recall/client.py`)

4. **Create first fixture** (`tests/recall/fixtures/f1_working_ready.py`)

5. **Write first test** (`tests/recall/test_api_contract.py::test_rc_api_01`)

6. **Verify test runs** and evidence collection works

7. **Iterate** through remaining tests following the priority order

Refer back to this guide for patterns and decisions. Good luck with the implementation!
