export type RunState = "queued" | "running" | "passed" | "failed" | "blocked" | "unconfirmed";
export type StepState = Exclude<RunState, "queued"> | "pending" | "skipped";
export const scenarioIds = [
  "library-basic", "library-full", "weather-weekend", "preference-update",
  "learning-review", "forget-sources",
] as const;
export type ScenarioId = typeof scenarioIds[number];
export type CoverageState = "unexecuted" | "called" | "passed" | "failed" | "blocked";
export interface ApiCall {
  method: string; path: string; status_code: number | null; elapsed_ms: number;
  operation_id: string | null;
}
export interface CoverageEntry {
  method: string; path: string; state: CoverageState; calls: number;
  http_statuses: number[]; step_ids: number[]; detail: string;
}
export interface Diagnostics {
  state: "pending" | "complete" | "incomplete";
  task_count: number; trace_count: number; log_record_count: number; incident_count: number;
  notes: string[];
}
export interface Scope {
  tenant_id: string; application_id: string; user_id: string; agent_id: string;
  session_id: string | null; task_id: string | null;
}
export interface MemoryRef { scope: Scope; memory_id: string; version: number }
export interface SourceRef { source_id: string; source_version: number; content_hash: string; locator: string }
export interface ContextPack {
  schema_version: "p3/1";
  recall_id: string; scope: Scope; outcome: "available" | "empty" | "degraded";
  selected_sources: ("working" | "long_term")[];
  coverage: Record<"working" | "long_term", "complete" | "partial" | "unavailable" | "not_requested">;
  groups: {
    group_id: string;
    items: { memory: MemoryRef; content: string; sources: SourceRef[]; representation: "original" | "compressed"; artifact_id: string | null }[];
    conflict: { group_id: string; members: MemoryRef[]; explanation: string; state: "unresolved" } | null;
  }[];
  rendered_context: string; token_budget: number; tokens_used: number;
  tokenizer_id: string; policy_version: string; degradation_reasons: string[]; committed_at: string;
}
export interface Evidence {
  method: "GET" | "POST" | "PUT"; path: string; operation_id: string;
  job_id: string | null; memories: MemoryRef[]; source_ids: string[]; task_ids: string[];
  recall_id: string | null; elapsed_ms: number;
  processing: { memory_id: string; memory_status: string; projection_state: string; processing_state: string }[];
  context: ContextPack | null;
  calls: ApiCall[];
  tasks: { task_id: string; kind: string; state: string; effect_status: string;
    error_code: string | null; completed_parts: number }[];
  cleanup_state: string | null;
}
export interface StepSnapshot {
  id: number; user_text: string; state: StepState; response_text: string;
  checks: { name: string; passed: boolean; detail: string }[]; evidence: Evidence | null;
}
export interface Mode {
  profile: string; embedding: string; semantic_processing: string;
  object_storage: string; scheduling: string; executor: string;
}
export interface RunSnapshot {
  run_id: string; scenario_id: ScenarioId; state: RunState; current_step: number;
  total_steps: number; mode: Mode | null; steps: StepSnapshot[];
  error: { status: number; code: string; message: string; operation_id: string | null; write_outcome: "not_applicable" | "unconfirmed" } | null;
  coverage: CoverageEntry[]; diagnostics: Diagnostics;
}
export interface Scenarios {
  items: { id: ScenarioId; title: string; description: string; total_steps: number }[];
  enabled: boolean; unavailable_reason: string | null;
}
