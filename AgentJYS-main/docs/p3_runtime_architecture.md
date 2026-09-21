# P3 Memory Runtime Architecture

## Scope

This refactor organizes P3 into six logical layers. It does not split the
system into six microservices. The first runtime shape remains:

- `p3-service`
- `b1-sidecar`
- `celery-worker`
- `redis`
- `milvus`
- `p2`

The migration follows a Strangler Pattern:

Legacy Services -> Adapter -> MemoryRuntime -> Integration

## Six Logical Layers

1. Agent Integration Layer

   HTTP and external agent entrypoints parse requests, validate schemas, create
   `RequestContext`, call `MemoryRuntime`, and serialize responses. Integration
   code must not own Redis, Milvus, Celery, B1 backend, or B3 heuristic logic.

2. Memory Runtime Layer

   `MemoryRuntime` is the P3 business orchestration entrypoint. It exposes:
   `embed`, `write_memory`, `submit_long_memory`, `get_task`, `search_memory`,
   `build_context`, `schedule`, and `record_access`.

3. Memory Formation Layer

   Formation owns the business path from raw memory event to durable memory
   fact and async projection tasks. Today it wraps existing B2 synchronous
   ingest and B2 Celery long-text submission.

4. Memory Retrieval & Context Layer

   Retrieval defines `RecallSource`, `WorkingRecallSource`,
   `LongTermRecallSource`, `RecallCandidate`, degraded recall, and the future
   context compression hook. The Runtime Composition Root wires
   `ContextRetrievalService` as the primary context-pack path; the legacy B2
   `MockContextPackBuilder` remains only as a compatibility fallback when no
   retrieval service is supplied.

5. Semantic Compute Layer

   B1 remains the shared semantic compute sidecar:

   Text / Chunk -> Embedding Vector

   B1 owns OpenVINO, AsyncInferQueue, dynamic batching, CPU streams, runtime
   details, fallback, and benchmark metrics. Runtime sees it through
   `EmbeddingPort`.

6. Adaptive Scheduling Layer

   B3 remains a side path. Runtime schedules through `SchedulerPort`, and
   normal memory write/context query does not require B3 success.

## Write Path

```text
Agent
-> Integration
-> RequestContext
-> MemoryRuntime.write_memory / submit_long_memory
-> Memory Fact
-> Formation
-> Celery
-> B1
-> P2 / optional Milvus projection
-> MemorySignal
```

Current state:

- IMPLEMENTED: HTTP entrypoints create/forward `RequestContext`.
- WIRED: `MemoryRuntime.write_memory` calls existing B2 `MemoryService.ingest`.
- WIRED: `MemoryRuntime.submit_long_memory` stores content through P2 adapter
  when needed and submits existing B2 Celery long-text task.
- IMPLEMENTED: B2 Celery writes the primary memory fact before compression,
  embedding, and projection.
- IMPLEMENTED: Formation status model and default event-to-memory policy;
  semantic extraction remains replaceable rather than being claimed as a
  deployed model capability.
- WIRED: primary Memory writes best-effort enqueue missing derived work through
  the provider-neutral ProjectionQueuePort. Queue failure does not roll back
  the authoritative fact.
- WIRED: `POST /api/v1/projections/reconcile` plans and enqueues missing
  projections for a scope; this is the recovery path for older data or queue
  outages.
- WIRED: `aether-p3-projection-worker` performs bounded queue drains as an
  independent process. Failed work is retained with an error and retried up to
  the worker's bounded attempt policy.
- WIRED: projection executors compare queued and authoritative revisions before
  provider work. Older work enters the terminal `SUPERSEDED` state
  instead of writing stale derived content or entering a retry loop. Session
  archive Context items use the owning Session revision.

## Read Path

```text
Agent
-> Integration
-> RequestContext
-> MemoryRuntime.build_context / search_memory
-> Retrieval
-> Redis + long-term source
-> Fusion
-> Context Pack
-> Agent
```

Current state:

- IMPLEMENTED: unified retrieval runs Working/Episodic/Semantic and optional
  long-document sources concurrently, with per-source timeout/degraded status.
- WIRED: Runtime records access traces best-effort after context/search hits.
  Result sets are submitted through `AccessTracePort.record_many()` so a recall
  does not synchronously rewrite each Memory or perform one Redis round trip per
  hit; older adapters that only expose `record()` remain compatible through a
  fallback.
- WIRED: `MemoryRecallPort` and `VectorSearchPort` keep B2 managers and P2 E1
  provider details behind replaceable retrieval-source boundaries.
- WIRED: the legacy context builder remains available only for injected/runtime
  compatibility paths; it is not the production Composition Root's primary
  context path.
- TODO: context compression.

## Scheduling Path

```text
AccessTrace
+ MemorySignal
+ ResourceState
-> B3
-> TierAction
-> Executor
-> Feedback
```

Current state:

- IMPLEMENTED: B3 heuristic scheduler and fallback KEEP behavior exist.
- WIRED: Runtime calls B3 through `SchedulerPort`.
- SKELETON: unified `AccessTrace` model.
- TODO: real Milvus -> Redis prefetch executor and physical tier movement.

## RequestContext

`RequestContext` carries:

- `request_id`: one logical business request.
- `trace_id`: one cross-module execution chain.
- `tenant_id`, `user_id`, `agent_id`, `session_id`, `task_id`: scope.
- `deadline`: runtime deadline propagated downward.
- `idempotency_key`: retry/idempotency boundary.
- `parent_request_id`: child-context lineage.

`RequestContext.child()` keeps the same `request_id` and `trace_id` by default.
Celery/background work can add a child task id without losing the original
trace.

## Fact vs Projection

Primary memory fact state is separate from derived projection state.

- Memory Fact: `ACTIVE`, `ARCHIVED`, `SUPERSEDED`, `DELETED`.
- Projection: `PENDING`, `PROCESSING`, `SUCCEEDED`, `FAILED`,
  `SUPERSEDED`, `NOT_APPLICABLE`, `NOT_IMPLEMENTED`.
  `SUPERSEDED` is a terminal delivery state for work targeting an
  older source revision; it is not a provider failure and is not retried.

Derived states include:

- `embedding_status`
- `compression_status`
- `vector_projection_status`
- `scheduler_signal_status`

The B2 long-text path persists the primary memory first. B1/P2/Milvus failure
marks projection or pipeline status failed; it must not delete the primary
memory fact.

Every derived projection carries the source revision used to produce it.
Memory fact updates use `MemoryStorePort.upsert_if_revision`; a worker
cannot replace a newer fact with an older projection result. Context semantic
hits carry the same revision and are rejected before fusion when they no longer
match the authoritative Catalog item. This is optimistic concurrency control
for derived work, not a new database or a change to the Northbound contract.
Session archives also carry their own immutable `source_revision`; later
extraction-status updates on the parent SessionRecord therefore do not
invalidate an already queued archive projection.

## Runtime Profile

`AETHER_RUNTIME_PROFILE` supports:

- `demo`: SQLite/mock paths are allowed.
- `local`: local development defaults; missing optional services degrade.
- `production`: Redis, Milvus/P2/Celery/B1 are expected where configured.

Health/status returns `runtime_profile` so a mock/demo run is not mistaken for
production acceptance evidence.

## Failure Propagation

- B1 foreground embedding failure returns an embedding error to the caller.
  It does not delete an already-written B2 memory fact.
- B2 Celery is the retry owner for background B1/P2 failures.
- B3 failure is side-path failure. It must not block `write_memory` or
  `build_context`.
- One recall source failure returns degraded context/retrieval, not full read
  failure.
- Runtime maps queue busy/unavailable paths to a unified runtime error model.

## Retry Owner

- Foreground P3 -> B1: at most a narrow client/backend retry; Runtime does not
  layer an unbounded retry loop.
- Background Celery -> B1/P2: Celery is the main retry owner.
- B1 backend: backend fallback is allowed; no infinite retry loop.
- Redis/Milvus: use existing conservative handling and bounded timeouts.

Avoid stacking API retry + Runtime retry + Celery retry + B1 retry into a retry
storm.

## Current Dependency Direction

```text
integration
-> runtime
-> memory formation / retrieval / scheduling
-> runtime ports
-> adapters
-> B1 / B2 / B3 / Redis / Milvus / P2 / Celery
```

Adapters must not import `MemoryRuntime`. B1 must not import B2. B3 must not
import HTTP controllers.

## Status Matrix

IMPLEMENTED:

- B1 dynamic batching/OpenVINO sidecar remains intact.
- Existing B2 sync memory ingest and degraded context behavior.
- Existing B2 Celery long-text path with primary fact first.
- Existing B3 heuristic and P2 logical route executor.
- `RequestContext`, runtime errors, status, health aggregation.

WIRED:

- HTTP entrypoints now call `MemoryRuntime` for primary APIs.
- Runtime wraps B1, B2 MemoryService, B2 Celery, Redis task status, P2 object
  store/search, and B3 scheduler through adapters.
- Access trace recording is best-effort. Production uses the Redis adapter and
  batches a result set through one pipeline; it is telemetry, not authoritative
  Memory state, and its failure never fails the foreground recall.

PARTIALLY IMPLEMENTED:

- Formation status/result model and default compatibility policy.
- Retrieval source model, typed candidates, and degraded result.
- Context compression hook.
- Reliability policy placeholders.
- Milvus active health probe.

TODO:

- Celery long-term memory physical compression acceptance at >=5x.
- Context compression implementation.
- B3 real Milvus -> Redis prefetch executor.
- Durable domain-event outbox beyond the current projection work queue.
- Circuit breaker and bulkhead enforcement.
- 72h fault-injection and HA validation.

## OpenViking-aligned P3 Context Kernel (current)

The context kernel now provides a provider-neutral logical namespace and does
not make P2 the owner of the Memory domain:

```text
Agent namespace
├── memories
│   ├── episodic / semantic   (Agent visibility)
│   └── working               (mounted from the current Session)
├── resources                 (Agent visibility)
├── skills                    (Agent visibility)
└── sessions/{session}
    ├── memories/working      (Session visibility)
    └── history                (Session visibility)
```

ContextItem exposes L0/L1/L2 representations, while MemoryStore, SessionStore,
and the Skill registry remain independent facts. Memory mappings always expose
a bounded deterministic L1 overview when an upstream semantic overview is not
available; the generator is explicitly marked so it can later be replaced by a
semantic projection worker. CompositeContextReader merges
these namespaces; ReindexService rebuilds derived indexes from Context Catalog
and Content ports. Both catalog traversal and semantic-index results are
rechecked against the requested visibility Scope before scoring or returning a
hit. `ReindexContextUseCase` and
`POST /api/v1/context/catalog/reindex` expose that recovery operation through
the Runtime with scope/root validation. Reindex is bounded by configurable
`AETHER_P3_CONTEXT_REINDEX_MAX_ITEMS` and
`AETHER_P3_CONTEXT_REINDEX_MAX_CHILDREN` limits and reports `complete=false`
when traversal or a layer projection is incomplete. HierarchicalContextSearchService
supports bounded recursive traversal and can merge an optional semantic-index
port; production wires `P2ContextSemanticIndexAdapter` through the existing B1
and P2 ports, while local/integration profiles use a deterministic reference
index. Index failures leave deterministic catalog search available but mark the
result degraded. Semantic hits are revalidated against the authoritative
catalog/content reader before being returned, so a lagging derived index cannot
serve stale text. P3 also keeps a durable logical tombstone for invalidated
derived entries; physical vector reclamation remains provider-dependent. The
default Northbound context path remains
behavior-compatible with the existing retrieval facade.

ProjectionWorkItem and ProjectionQueuePort separate missing Embedding, vector
index, and summary work from authoritative Memory facts. Production profiles
use the Redis-backed queue with leases. The application Worker now performs
bounded claim/execute/complete-or-fail passes, with concrete B1 embedding and
P2 vector-index adapters wired behind ports; summary generation remains an
explicit unconfigured boundary. New writes enqueue missing work best-effort;
scope reconciliation and the independent projection worker provide recovery
for pre-existing facts and transient queue/provider failures.

Current dependency graph:

```text
API / Integration
        ↓
MemoryRuntime facade
        ↓
Application use cases
        ↓
Memory Domain + Context/Session/Skill ports
        ↓
P3 adapters
   ├── MemoryStore / SessionStore / SkillStore
   ├── Context catalog + hierarchical search + optional semantic index
     ├── Context reindex application service / recovery API
    ├── Memory Projection queue + provider projection executor
    ├── Context Projection queue + provider projection executor
    │      └── Resource / Skill / Session Archive L0/L1 derived views
    └── B1 / B2 / B3 / P2 / Redis / Milvus / Celery bridges
```

Projection recovery is intentionally outside the synchronous Northbound write
path:

```text
Memory write
    ↓ best-effort enqueue
ProjectionQueue (Redis in production)
    ↓ lease / retry
aether-p3-projection-worker
    ↓ provider-neutral executor
B1 embedding + P2 vector projection + Context L0/L1 semantic projection
```

For a newly written Memory, the existing vector-index work also publishes its
L0/L1 Context projections after the provider vector write succeeds. This keeps
the Context semantic index fresh without adding a synchronous B1 call to the
Northbound write path. If the Context projection fails, the work remains
retryable and the authoritative Memory is not marked fully projected until the
derived step succeeds; `ReindexService` remains the recovery path for older
records and missed queue work. Its bounded traversal returns an opaque
`next_cursor` when the configured item or fan-out limit is reached; callers can
submit that cursor to continue without restarting from the root. The cursor is
bound to its root URI and requested layers and contains no provider credentials
or storage location.

P2 remains an adapter and is unchanged by this context-kernel work. B1, B2,
and B3 remain the existing compute, memory-processing, and scheduling modules;
the new namespace and queue contracts only provide stable P3 integration
boundaries around them.

Production Resource and Skill descriptors use Redis-backed scope indexes; local
and integration profiles retain bounded in-memory stores. This keeps the
provider-neutral Context Catalog durable in production without making Redis a
Memory Domain dependency. Resource L2 content can remain an external reference
and is resolved lazily through the ObjectStore port when a caller explicitly
reads detail content.

Context object registration is P3-owned as well:

```text
POST /api/v1/context/resources
POST /api/v1/context/skills
POST /api/v1/context/resources/{resource_id}/delete
POST /api/v1/context/skills/{skill_id}/delete
```

These routes persist provider-neutral descriptors and expose them through the
same catalog. Resource and Skill facts are persistent by default; the optional
`AETHER_P3_CONTEXT_FACT_TTL_SECONDS` setting must be chosen explicitly when an
installation wants expiration semantics. Delete removes the P3 descriptor and
logically invalidates its derived semantic entry. It does not delete an
externally owned P2 object, and physical vector reclamation remains a provider
maintenance concern. Skill instructions describe a capability but do not grant
execution permission by themselves.

Resource content uses `ResourceContentParserPort`. The built-in
`PlainTextResourceParser` handles text/plain, Markdown, CSV, and JSON. A
binary resource such as PDF or DOCX may be registered by reference, but its L2
remains `pending` until a corresponding parser adapter is deployed; the P3
catalog never labels unparsed binary content as ready text.

### Context Catalog application API

The provider-neutral catalog is also available through additive P3 read APIs;
the frozen Northbound v1 routes are unchanged:

```text
POST /api/v1/context/catalog/children
POST /api/v1/context/catalog/item
POST /api/v1/context/catalog/search
GET  /api/v1/context/traces/{trace_id}?tenant_id=...&user_id=...&agent_id=...
```

The trace endpoint is additive and scope-authorized. It exposes source timing,
candidate counts, selected URIs, and degraded reasons without exposing provider
credentials or direct Redis/Milvus handles. A missing or unavailable trace
store is returned through the standard runtime error model.
Trace authorization uses exact tenant/user/agent/session equality. Omitting
`session_id` does not grant Agent-wide access to traces created inside a
session because trace steps can contain session-specific query and URI data.

These routes accept an explicit tenant/user/agent/session scope, validate
canonical `aether://` URIs against that scope, and return directory, Memory,
Resource, Skill, Session-history, and L0/L1/L2 representations through
`MemoryRuntime`. They never expose Redis, Milvus, Celery, P2, or B1 client
objects to HTTP callers. The default Northbound `/api/v1/context` response
continues to use the existing `ContextPack` contract for compatibility.
Resource and Skill registration repeat the tenant/user/agent authorization at
the Application boundary: a registration scope that differs from its
`RequestContext` is rejected before revision allocation or persistence. This
keeps non-HTTP callers subject to the same isolation rule.
Session append, read, commit, and consolidation likewise require a complete
tenant/user/agent/session `RequestContext` before accessing `SessionStorePort`.
Projection planning and reconciliation accept `RequestContext` at the Runtime
facade and derive their Scope only after the same authorization checks; the
operational API cannot inject an independently constructed Store scope.
Memory event writes, long-memory ingestion, B2 search, and Context build also
compare their model/argument scope with the authenticated `RequestContext`
before idempotency claims, object writes, vector reads, or recall execution.

Session lifecycle is available through additive P3 routes:

```text
POST /api/v1/sessions/messages
POST /api/v1/sessions/current
POST /api/v1/sessions/commit
POST /api/v1/sessions/consolidate
```

`commit` moves older messages into an immutable, bounded Session Archive and
exposes deterministic L0 Abstract, L1 Overview, and L2 Transcript content in
the session history namespace. A successful commit automatically enqueues one
deduplicated Archive extraction job; `SessionExtractionWorker` claims it with
a lease and invokes the same idempotent consolidation use case outside the
session write path. `consolidate` remains an explicit synchronous trigger for
operators or tests. The formation boundary now exposes typed
`MemoryExtractionCandidate` values through the asynchronous
`MemoryExtractionPort`; the old synchronous `SessionMemoryExtractionPolicy`
is retained behind a compatibility adapter. The default provider still forms
one distilled Semantic Memory from the deterministic Archive abstract, so no
semantic model capability is claimed until a real provider is injected. An
optional `HttpMemoryExtractionAdapter` accepts a provider-neutral JSON response
validated into those typed candidates; it is enabled only when
`AETHER_P3_MEMORY_EXTRACTION_URL` is configured, and its model/API key/timeout
are supplied through the same `AppSettings` composition root.

The worker is deployable as the separate `aether-p3-session-worker` process
(the `p3-session-worker` Compose service). It shares Redis with the P3 host,
uses queue leases for crash recovery, and can be scaled horizontally without
changing Session or Memory semantics.

### Unified Context projection work

Memory projection and Context-object projection use separate queues because
their authoritative facts and retry semantics are different. Resource, Skill,
and Session Archive writes enqueue a typed `ContextProjectionWorkItem` after
their primary write succeeds; the enqueue carries only the canonical URI,
source revision, scope, and L0/L1 target layers. It never performs B1 or P2
work inside the request or Session CAS path.

```text
Resource / Skill / Session Archive fact
                ↓ best-effort enqueue
RedisContextProjectionQueue (production)
                ↓ lease / retry
aether-p3-context-projection-worker
                ↓ authoritative catalog/content read
SemanticIndexPort (P2 adapter in production)
```

`ProviderContextProjectionExecutor` rechecks URI and item visibility against
the work scope, reads the current authoritative item, and indexes only
available layers. A stale work item therefore cannot write its old payload;
the current catalog representation wins. It is marked `SUPERSEDED`
instead of retried. After the provider write, the executor reads the Catalog a
second time. If deletion, revision replacement, or visibility change raced the
write, it invalidates the derived URI again and supersedes the work. Queue
work whose fact was already deleted before claim is also superseded directly;
it is not retried as a transient projection failure. Queue failure does not roll back the
fact, and bounded `ReindexService` remains the recovery path for missed or
expired work. The dedicated queue namespace is documented in ADR-0011 and is
independent from the existing Memory Projection Queue and Session Extraction
Queue.
