/** The monitor uses only the authenticated unified P3 API. No mock fallback. */
export type Resource<T> = { data?: T; error?: string };
export type Observation = {
  name: string;
  category: string;
  state: string;
  checked_at: string;
  fresh_until: string;
  elapsed_ms: number;
  reason_code: string;
};
export type Health = {
  checked_at: string;
  readiness: string;
  config_version: string;
  observations: Observation[];
};
export type Worker = {
  worker_id: string;
  state: string;
  execution_class: string;
  age_seconds: number;
  last_seen: string;
};
export type Runtime = {
  state: string;
  worker_state: string;
  workers: Worker[];
  pending_tasks: number;
  oldest_task_age_seconds: number;
  unacknowledged_deliveries: number;
  expired_lease_task_ids: string[];
  attention_task_ids: string[];
  unknown_action_ids: string[];
  http_worker: string;
  worker_lanes: Record<string, string>;
  identity_configuration: string;
};
export type Ref = {
  owner: string;
  object_type: string;
  object_id: string;
  scope: Record<string, string>;
};
export type Task = {
  task_id: string;
  kind: string;
  owner_flow: string;
  state: string;
  attempt: number;
  max_attempts: number;
  effect_status: string;
  error_code: string | null;
  subject: Ref;
  created_at: string;
  trace_id: string | null;
  next_run_at: string | null;
  deadline_at: string;
};
export type TaskPage = { items: Task[]; next_cursor: string | null };
export type Operation = Task & {
  temporal?: {
    workflow_id: string | null;
    binding?: { namespace: string; first_run_id: string; current_run_id: string } | null;
    diagnostic?: { reason_code: string; technical_state: string } | null;
  };
};
export type Trace = {
  trace_id: string;
  started_at: string;
  last_seen: string;
  entry_node: string;
  flow: string;
  record_count: number;
  span_count: number;
  failed_span_count: number;
};
export type TracePage = {
  items: Trace[];
  next_before: number | null;
  coverage: string;
};
export type Log = {
  sequence: number;
  occurred_at: string;
  trace_id: string;
  span_id: string;
  parent_span_id: string | null;
  node: string;
  flow: string;
  phase: string;
  level: string;
  elapsed_ms: number | null;
  reason_code: string | null;
  request_id: string;
  operation_id: string;
  task_id: string | null;
};
export type LogPage = {
  trace_id: string;
  records: Log[];
  next_after: number | null;
  coverage: string;
  local_dropped_records: number;
  last_pruned_at: string | null;
  overview: {
    span_count: number;
    failed_span_count: number;
    open_span_count: number;
  };
};
export type Incident = {
  incident_id: string;
  rule_id: string;
  state: string;
  verification: string;
  opened_at: string;
  updated_at: string;
  operation_id: string | null;
  subject: Ref;
  evidence_refs: Ref[];
  verification_refs: Ref[];
};
export type Capabilities = {
  profile: string;
  embedding: string;
  semantic_processing: string;
  object_storage: string;
  scheduling: string;
  executor: string;
};

// One console per page. Tenant selection lives in memory and is always checked by P3.
let activeTenant = "";
export function setActiveTenant(tenant: string) { activeTenant = tenant; }

export async function get<T>(
  path: string,
  token: string,
  signal?: AbortSignal,
): Promise<T> {
  // Same origin: a reverse proxy owns the service address, never a query-string URL.
  const timeout = AbortSignal.timeout(15000);
  const response = await fetch(path, {
    headers: { Authorization: `Bearer ${token}`, Accept: "application/json", ...(activeTenant ? { "X-P3-Tenant": activeTenant } : {}) },
    signal: signal ? AbortSignal.any([signal, timeout]) : timeout,
    cache: "no-store",
    redirect: "error",
  });
  if (response.status === 401)
    throw new Error("凭据无效或已失效，请重新连接。");
  if (response.status === 403) throw new Error("当前身份没有此诊断权限。");
  if (!response.ok)
    throw new Error(`服务请求失败（HTTP ${response.status}）。`);
  if (
    !(response.headers.get("content-type") ?? "").includes("application/json")
  )
    throw new Error("未收到 JSON，请检查 /p3 反向代理配置。");
  return response.json() as Promise<T>;
}

export function message(error: unknown): string {
  if (error instanceof Error && error.name === "TimeoutError")
    return "请求超时，请检查服务状态后重试。";
  return error instanceof Error && error.name !== "TypeError"
    ? error.message
    : "无法连接服务，请检查服务进程和网络。";
}
