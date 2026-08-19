export type ComponentStatus = "HEALTHY" | "DEGRADED" | "UNAVAILABLE" | "BUSY" | "UNKNOWN";
export type SystemStatus = "Healthy" | "Degraded" | "Unavailable";

export interface RuntimeComponentHealth {
  component: string;
  status: ComponentStatus;
  detail?: string;
  latency_ms?: number | null;
  critical?: boolean;
  checked_at?: string;
  metadata?: Record<string, unknown>;
}

export interface RuntimeHealth {
  runtime_profile: string;
  overall: string;
  live: boolean;
  ready: boolean;
  checked_at: string;
  components: RuntimeComponentHealth[];
}

export interface SystemStatusResponse {
  p2_online?: boolean;
  p2_endpoint?: string;
  runtime_profile?: string;
  runtime_health?: RuntimeHealth;
  demo_enabled?: boolean;
  last_run?: string | null;
  last_status?: string;
  last_output?: string;
  flow_history?: unknown[];
  schedule_history?: ScheduleHistoryItem[];
}

export interface B1Metrics {
  uptime_seconds?: number;
  requests?: number;
  items?: number;
  success?: number;
  failed?: number;
  http_request_qps?: number;
  requests_per_second?: number;
  items_per_second?: number;
  effective_item_qps?: number;
  vector_qps?: number;
  request_latency_p99_ms?: number;
  average_request_latency_ms?: number;
  batching_mode?: string;
  dynamic_batch?: {
    dynamic_batch_enabled?: boolean;
    queue_depth?: number;
    queue_capacity?: number;
    batch_items_avg?: number;
    backend_inference_p99_ms?: number;
    queue_wait_p99_ms?: number;
  };
}

export interface B1Status {
  endpoint?: string;
  status: ComponentStatus;
  http_status?: number;
  health?: Record<string, any> | null;
  metrics?: B1Metrics | null;
  capabilities?: Record<string, any> | null;
  events?: Array<Record<string, any>>;
  error?: string | null;
}

export interface EmbeddingRecord {
  request_id: string;
  trace_id: string;
  source_id: string;
  object_id?: string | null;
  chunk_id: string;
  chunk_text: string;
  vector: number[];
  embedding_model?: string;
  metadata?: Record<string, any>;
}

export interface EmbeddingResponse {
  request_id: string;
  trace_id: string;
  source_id: string;
  status: string;
  input_type?: "query" | "passage";
  records: EmbeddingRecord[];
  latency_ms?: number;
  backend?: string;
  engine?: string;
  model?: string;
  dimension?: number;
  normalized?: boolean;
  quantization_type?: string;
}

export interface MemoryRecord {
  id: string;
  type: "working" | "episodic" | "semantic";
  state?: string;
  session_id: string;
  agent_id: string;
  user_id?: string | null;
  tenant_id?: string | null;
  task_id?: string | null;
  request_id?: string | null;
  trace_id?: string | null;
  source_id?: string | null;
  object_id?: string | null;
  content: string;
  metadata?: Record<string, any>;
  source?: string;
  created_at?: string;
  updated_at?: string;
  last_accessed_at?: string | null;
  access_count?: number;
  importance?: number;
  tags?: string[];
  compression_artifact_id?: string | null;
  compression_status?: string;
  compression_ratio?: number | null;
  embedding_status?: string;
  vector_projection_status?: string;
  scheduler_signal_status?: string;
}

export interface ContextResponse {
  request: Record<string, any>;
  memories: MemoryRecord[];
  total_tokens: number;
  budget_tokens: number;
  recall_scores: Record<string, number>;
  assembled_text: string;
  built_at: string;
  summary?: string;
  memory_refs?: string[];
  evidence_refs?: string[];
  status?: string;
  trace_id?: string | null;
  complete?: boolean;
  missing_sources?: string[];
  degradation_reasons?: Record<string, string>;
  source_latency_ms?: Record<string, number>;
}

export interface TaskStatus {
  task_id: string;
  state: "PENDING" | "PROCESSING" | "SUCCEEDED" | "FAILED" | string;
  memory_id?: string;
  trace_id?: string;
  request_id?: string;
  chunk_count?: number;
  p2_vector_count?: number;
  p2_collection?: string;
  compression_status?: string;
  compression_ratio?: number;
  error?: string;
  [key: string]: any;
}

export interface B2SearchItem {
  memory_id?: string;
  task_id?: string;
  chunk_id?: string;
  text?: string;
  score?: number;
  category?: string;
  keywords?: string[];
  content_ref?: string;
  trace_id?: string;
}

export interface B2SearchResponse {
  items: B2SearchItem[];
  backend?: string;
  collection?: string;
  query_model?: string;
  query_dimension?: number;
}

export interface B3CandidateResponse {
  request_id?: string;
  trace_id?: string | null;
  source: string;
  objects: SchedulableObject[];
  resource_state: ResourceState;
  context?: ContextResponse;
  notes?: string[];
}

export interface SchedulableObject {
  object_id: string;
  object_type: string;
  current_tier: string;
  size_bytes?: number;
  tenant_id?: string | null;
  namespace?: string | null;
  access: {
    access_frequency: number;
    recency_score: number;
    hit_rate: number;
    access_count: number;
    last_access_time?: string | null;
  };
  semantic: {
    semantic_relevance: number;
    importance: number;
    task_relevance: number;
  };
  business_priority?: number;
  migratable?: boolean;
  pinned?: boolean;
  expired?: boolean;
  metadata?: Record<string, any>;
}

export interface ResourceState {
  tiers: Record<string, unknown>;
  migration_cost_score: number;
  network_available: boolean;
}

export interface ScheduleAction {
  action_id: string;
  request_id: string;
  action_type: string;
  object_id: string;
  object_type: string;
  source_tier: string;
  target_tier?: string | null;
  priority: number;
  reason: string;
  trace_id: string;
  policy_version: string;
  expected_effect: string;
  callback_required: boolean;
  score: number;
  score_frequency: number;
  score_semantic: number;
  score_decay: number;
  score_cost: number;
  metadata?: Record<string, any>;
  created_at?: string;
}

export interface ExecutionFeedback {
  action_id: string;
  object_id: string;
  action_type: string;
  execute_status: string;
  execute_latency_ms: number;
  new_tier?: string | null;
  error_code?: string | null;
  failure_reason?: string | null;
  trace_id: string;
  metadata?: Record<string, any>;
  timestamp?: string;
}

export interface ScheduleEntry {
  action: ScheduleAction;
  feedback: ExecutionFeedback;
}

export interface ScheduleRunResult {
  request_id: string;
  trace_id: string;
  actions: ScheduleAction[];
  entries: ScheduleEntry[];
  started_at?: string;
  completed_at?: string;
}

export interface ScheduleHistoryItem {
  timestamp?: string;
  source?: string;
  action_id: string;
  request_id: string;
  trace_id: string;
  object_id: string;
  action_type: string;
  source_tier: string;
  target_tier?: string | null;
  priority?: number;
  score?: number;
  score_frequency?: number;
  score_semantic?: number;
  score_decay?: number;
  score_cost?: number;
  reason?: string;
  policy_version?: string;
  execute_status?: string;
  execute_latency_ms?: number;
}
