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
   context compression hook. The current production context pack still reuses
   the existing B2 `MockContextPackBuilder`.

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
- SKELETON: Formation status model.
- TODO: durable outbox and reconciliation for projection retries.

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

- IMPLEMENTED: existing B2 context builder degrades when one memory manager
  fails or times out.
- WIRED: Runtime records access traces best-effort after context/search hits.
- SKELETON: standalone `MemoryRetrievalService` and recall source interfaces.
- TODO: replace legacy context direct manager reads with unified Redis/Milvus/P2
  retrieval sources.
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
  `NOT_APPLICABLE`, `NOT_IMPLEMENTED`.

Derived states include:

- `embedding_status`
- `compression_status`
- `vector_projection_status`
- `scheduler_signal_status`

The B2 long-text path persists the primary memory first. B1/P2/Milvus failure
marks projection or pipeline status failed; it must not delete the primary
memory fact.

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
- Access trace recording is best-effort and in-memory.

SKELETON:

- Formation status/result model.
- Retrieval source model and degraded result.
- Context compression hook.
- Reliability policy placeholders.
- Milvus active health probe.

TODO:

- Celery long-term memory physical compression acceptance at >=5x.
- Unified Redis/Milvus/P2 retrieval sources replacing legacy direct manager
  recall.
- Context compression implementation.
- B3 real Milvus -> Redis prefetch executor.
- Durable outbox and reconciliation.
- Circuit breaker and bulkhead enforcement.
- 72h fault-injection and HA validation.
