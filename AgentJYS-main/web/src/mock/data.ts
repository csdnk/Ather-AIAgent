import type {
  B1Status,
  B3CandidateResponse,
  ContextResponse,
  EmbeddingResponse,
  MemoryRecord,
  ScheduleHistoryItem,
  ScheduleRunResult,
  SystemStatusResponse,
  TaskStatus,
} from "../api/types";

export const mockSystemStatus: SystemStatusResponse = {
  p2_online: true,
  runtime_health: {
    runtime_profile: "development",
    overall: "P3_NORMAL",
    live: true,
    ready: true,
    checked_at: new Date().toISOString(),
    components: [
      { component: "P3", status: "HEALTHY", detail: "MemoryRuntime live", critical: true },
      { component: "B1", status: "HEALTHY", detail: "Sidecar ready", critical: true },
      { component: "Redis", status: "HEALTHY", detail: "Connected", critical: false },
      { component: "Milvus", status: "DEGRADED", detail: "Optional projection disabled", critical: false },
      { component: "Celery", status: "HEALTHY", detail: "Task queue available", critical: false },
      { component: "B3", status: "HEALTHY", detail: "Heuristic scheduler ready", critical: true },
    ],
  },
};

export const mockB1Status: B1Status = {
  status: "HEALTHY",
  endpoint: "mock://b1",
  health: {
    status: "ready",
    model: "BAAI/bge-small-zh-v1.5",
    backend: "openvino",
    engine: "OpenVINO",
    dimension: 512,
    dynamic_batch_enabled: true,
  },
  metrics: {
    effective_item_qps: 1326,
    http_request_qps: 82.8,
    vector_qps: 1326,
    request_latency_p99_ms: 28.6,
    batching_mode: "dynamic",
    dynamic_batch: { batch_items_avg: 14.8, dynamic_batch_enabled: true },
  },
  capabilities: {
    batching: { dynamic_enabled: true },
    cpu_runtime: { active_simd_policy: "mock runtime dispatch" },
  },
};

export const mockB1Embedding: EmbeddingResponse = {
  request_id: "mock-b1",
  trace_id: "mock-trace",
  source_id: "mock-source",
  status: "success",
  input_type: "query",
  latency_ms: 11.7,
  backend: "openvino",
  engine: "OpenVINO",
  model: "BAAI/bge-small-zh-v1.5",
  dimension: 512,
  records: [
    {
      request_id: "mock-b1",
      trace_id: "mock-trace",
      source_id: "mock-source",
      chunk_id: "mock-source:0000",
      chunk_text: "mock text",
      embedding_model: "BAAI/bge-small-zh-v1.5",
      vector: Array.from({ length: 32 }, (_, index) => Number((Math.sin(index) / 10).toFixed(6))),
    },
  ],
};

export const mockMemory: MemoryRecord = {
  id: "mem-mock-001",
  type: "semantic",
  state: "active",
  session_id: "demo-session",
  agent_id: "demo-agent",
  user_id: "demo-user",
  tenant_id: "demo-tenant",
  content: "Current discussion is about the B2 Web memory page.",
  source: "user",
  importance: 0.95,
  access_count: 3,
  created_at: new Date().toISOString(),
  updated_at: new Date().toISOString(),
  metadata: { category: "user_preference", storage: "Redis primary" },
};

export const mockContext: ContextResponse = {
  request: { query: "mock context" },
  memories: [mockMemory],
  total_tokens: 24,
  budget_tokens: 4096,
  recall_scores: { [mockMemory.id]: 0.91 },
  assembled_text: "[1] Current discussion is about the B2 Web memory page.",
  built_at: new Date().toISOString(),
  status: "ok",
  complete: true,
};

export const mockLongTextTask: TaskStatus = {
  task_id: "task-mock-001",
  state: "PROCESSING",
  memory_id: "mem-mock-long",
  trace_id: "mock-long-trace",
};

export const mockB3Candidates: B3CandidateResponse = {
  source: "mock-context-recall",
  objects: [
    {
      object_id: mockMemory.id,
      object_type: "semantic_memory",
      current_tier: "L3",
      size_bytes: mockMemory.content.length,
      access: {
        access_frequency: 0.3,
        recency_score: 0.97,
        hit_rate: 1,
        access_count: 3,
      },
      semantic: {
        semantic_relevance: 0.91,
        importance: 0.95,
        task_relevance: 0.91,
      },
      business_priority: 0.95,
      metadata: { memory_id: mockMemory.id, execution_type: "control-plane" },
    },
  ],
  resource_state: { tiers: {}, migration_cost_score: 0.5, network_available: true },
};

export const mockScheduleRun: ScheduleRunResult = {
  request_id: "sched-mock",
  trace_id: "sched-trace",
  actions: [
    {
      action_id: "action-mock",
      request_id: "sched-mock",
      action_type: "prefetch",
      object_id: mockMemory.id,
      object_type: "semantic_memory",
      source_tier: "L3",
      target_tier: "L2",
      priority: 82,
      reason: "score=0.820; frequency=0.475; semantic=0.930; recency=0.970; cost=0.500",
      trace_id: "sched-trace",
      policy_version: "heuristic-v1",
      expected_effect: "reduce future access latency on L2",
      callback_required: true,
      score: 0.82,
      score_frequency: 0.475,
      score_semantic: 0.93,
      score_decay: 0.97,
      score_cost: 0.5,
      metadata: { execution_type: "control-plane" },
    },
  ],
  entries: [
    {
      action: {} as any,
      feedback: {
        action_id: "action-mock",
        object_id: mockMemory.id,
        action_type: "prefetch",
        execute_status: "success",
        execute_latency_ms: 4.2,
        new_tier: "L2",
        trace_id: "sched-trace",
        metadata: { route_mode: "logical" },
      },
    },
  ],
};
mockScheduleRun.entries[0].action = mockScheduleRun.actions[0];

export const mockScheduleHistory: ScheduleHistoryItem[] = [
  {
    timestamp: new Date().toISOString(),
    source: "mock",
    action_id: "action-mock",
    request_id: "sched-mock",
    trace_id: "sched-trace",
    object_id: mockMemory.id,
    action_type: "prefetch",
    source_tier: "L3",
    target_tier: "L2",
    priority: 82,
    score: 0.82,
    score_frequency: 0.475,
    score_semantic: 0.93,
    score_decay: 0.97,
    score_cost: 0.5,
    reason: "score=0.820; frequency=0.475; semantic=0.930; recency=0.970; cost=0.500",
    policy_version: "heuristic-v1",
    execute_status: "success",
    execute_latency_ms: 4.2,
  },
];
