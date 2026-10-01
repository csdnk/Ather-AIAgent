import { scenarioIds, type RunSnapshot, type ScenarioId, type Scenarios } from "./types";

const base = "/p4-api/api/v1/demo";
const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const states = ["queued", "running", "passed", "failed", "blocked", "unconfirmed"];
const messages: Record<string, string> = {
  connection: "连接中断，请核对原运行；不会自动重发写入。",
  protocol: "返回格式不符合演示协议，请核对原运行。",
  invalid_id: "运行标识无效，请人工核对；不会自动开始。",
  rejected: "请求被服务端明确拒绝，本次没有开始新运行。",
  run_active: "已有运行或待核对任务，请先核对服务端状态。",
  run_limit: "已保留 10 轮，请核对任务后重启 P4 演示服务。",
  demo_unavailable: "演示未启用，请检查 P4 的本地演示配置。",
};
export class DemoApiError extends Error {
  constructor(public code: string, public status = 0, public rejected = false) {
    super(messages[code] ?? messages.connection);
    this.name = "DemoApiError";
  }
}
function record(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
const strings = (value: unknown): value is string[] => Array.isArray(value) && value.every(v => typeof v === "string");
const count = (value: unknown): value is number => Number.isInteger(value) && Number(value) >= 0;
const nullableText = (value: unknown) => value === null || typeof value === "string";
const route = (value: unknown) => typeof value === "string" && /^\/p3\/[a-z0-9_{}/-]+$/.test(value);
const scenario = (value: unknown): value is ScenarioId => scenarioIds.includes(value as ScenarioId);
function validCoverage(value: unknown, total: number): boolean {
  return Array.isArray(value) && value.every(row => record(row)
    && ["GET", "POST", "PUT"].includes(String(row.method)) && route(row.path)
    && ["unexecuted", "called", "passed", "failed", "blocked"].includes(String(row.state))
    && count(row.calls) && typeof row.detail === "string"
    && Array.isArray(row.http_statuses) && row.http_statuses.every(n => count(n) && n >= 100 && n <= 599)
    && Array.isArray(row.step_ids) && row.step_ids.every(n => count(n) && n <= total));
}
function validDiagnostics(value: unknown): boolean {
  return record(value) && ["pending", "complete", "incomplete"].includes(String(value.state))
    && ["task_count", "trace_count", "log_record_count", "incident_count"].every(k => count(value[k]))
    && strings(value.notes);
}
function validEvidence(value: unknown): boolean {
  if (value === null) return true;
  if (!record(value)) return false;
  return ["GET", "POST", "PUT"].includes(String(value.method)) && route(value.path)
    && typeof value.operation_id === "string" && (value.job_id === null || typeof value.job_id === "string")
    && Array.isArray(value.memories) && value.memories.every(m => record(m) && typeof m.memory_id === "string" && typeof m.version === "number" && record(m.scope))
    && strings(value.source_ids) && strings(value.task_ids)
    && (value.recall_id === null || typeof value.recall_id === "string") && typeof value.elapsed_ms === "number" && Number.isFinite(value.elapsed_ms)
    && Array.isArray(value.processing) && value.processing.every(p => record(p)
      && ["memory_id", "memory_status", "projection_state", "processing_state"].every(k => typeof p[k] === "string"))
    && (value.context === null || record(value.context))
    && (value.calls === undefined || (Array.isArray(value.calls) && value.calls.every(c => record(c)
      && typeof c.method === "string" && route(c.path) && nullableText(c.operation_id)
      && (c.status_code === null || count(c.status_code)) && typeof c.elapsed_ms === "number" && Number.isFinite(c.elapsed_ms))))
    && (value.tasks === undefined || (Array.isArray(value.tasks) && value.tasks.every(t => record(t)
      && ["task_id", "kind", "state", "effect_status"].every(k => typeof t[k] === "string")
      && nullableText(t.error_code) && count(t.completed_parts))))
    && (value.cleanup_state === undefined || nullableText(value.cleanup_state));
}
function decodeRun(value: unknown, id: string, expectedScenario?: ScenarioId): RunSnapshot {
  if (!record(value) || value.run_id !== id || !scenario(value.scenario_id)
    || (expectedScenario !== undefined && value.scenario_id !== expectedScenario)
    || !states.includes(String(value.state)) || !count(value.total_steps) || value.total_steps < 1 || value.total_steps > 12
    || !count(value.current_step) || value.current_step > value.total_steps
    || !(value.mode === null || (record(value.mode) && ["profile", "embedding", "semantic_processing", "object_storage", "scheduling", "executor"].every(k => typeof (value.mode as Record<string, unknown>)[k] === "string")))
    || !(value.error === null || (record(value.error) && typeof value.error.code === "string"))
    || !Array.isArray(value.steps) || value.steps.length !== value.total_steps
    || !value.steps.every((s, i) => record(s) && s.id === i + 1 && typeof s.user_text === "string"
      && typeof s.response_text === "string" && [...states.slice(1), "pending", "skipped"].includes(String(s.state))
      && validEvidence(s.evidence) && Array.isArray(s.checks)
      && s.checks.every(c => record(c) && typeof c.name === "string" && typeof c.passed === "boolean" && typeof c.detail === "string"))
    || !validCoverage(value.coverage ?? [], value.total_steps)
    || (value.diagnostics !== undefined && !validDiagnostics(value.diagnostics))) {
    throw new DemoApiError("protocol");
  }
  // The server owns deep ContextPack validation; the browser checks its rendering boundary.
  return {
    ...value,
    coverage: value.coverage ?? [],
    diagnostics: value.diagnostics ?? {
      state: "pending", task_count: 0, trace_count: 0, log_record_count: 0, incident_count: 0, notes: [],
    },
    steps: value.steps.map(s => ({
      ...s, evidence: s.evidence === null ? null : {
        ...s.evidence, calls: s.evidence.calls ?? [], tasks: s.evidence.tasks ?? [],
        cleanup_state: s.evidence.cleanup_state ?? null,
      },
    })),
  } as unknown as RunSnapshot;
}
async function request(path: string, method: "GET" | "POST", signal?: AbortSignal, body?: string): Promise<unknown> {
  const controller = new AbortController();
  const abort = () => controller.abort();
  signal?.addEventListener("abort", abort, { once: true });
  if (signal?.aborted) abort();
  const timer = window.setTimeout(abort, 5000);
  try {
    const response = await fetch(base + path, {
      method, credentials: "omit", cache: "no-store", signal: controller.signal,
      headers: { "Content-Type": "application/json" }, ...(body ? { body } : {}),
    });
    if (!response.ok) {
      const data: unknown = await response.json().catch(() => null);
      const rawCode = record(data) && record(data.error) ? data.error.code : null;
      const code = ["run_active", "run_limit", "demo_unavailable"].includes(String(rawCode)) ? String(rawCode) : "rejected";
      const rejected = [400, 403, 404, 409, 413, 415, 422, 429].includes(response.status)
        || (response.status === 503 && code === "demo_unavailable");
      throw new DemoApiError(rejected ? code : "connection", response.status, rejected);
    }
    try { return await response.json(); }
    catch { throw new DemoApiError("protocol"); }
  } catch (error) {
    if (error instanceof DemoApiError) throw error;
    throw new DemoApiError("connection");
  } finally {
    window.clearTimeout(timer);
    signal?.removeEventListener("abort", abort);
  }
}
function validateId(id: string) {
  if (!uuid.test(id)) throw new DemoApiError("invalid_id");
}
export async function listScenarios(signal?: AbortSignal): Promise<Scenarios> {
  const value = await request("/scenarios", "GET", signal);
  if (!record(value) || typeof value.enabled !== "boolean" || !Array.isArray(value.items)
    || !value.items.every(s => record(s) && scenario(s.id) && typeof s.title === "string" && typeof s.description === "string"
      && ((s.id === "library-basic" && s.total_steps === undefined) || (count(s.total_steps) && s.total_steps > 0 && s.total_steps <= 12)))
    || new Set(value.items.map(s => s.id)).size !== value.items.length) {
    throw new DemoApiError("protocol");
  }
  return { ...value, items: value.items.map(s => ({ ...s, total_steps: s.total_steps ?? 6 })) } as Scenarios;
}
export async function startRun(
  requestId: string, signal?: AbortSignal, scenarioId: ScenarioId = "library-basic",
): Promise<RunSnapshot> {
  validateId(requestId);
  if (!scenario(scenarioId)) throw new DemoApiError("protocol");
  return decodeRun(await request("/runs", "POST", signal, JSON.stringify({
    scenario_id: scenarioId, request_id: requestId,
  })), requestId, scenarioId);
}
export async function getRun(runId: string, signal?: AbortSignal): Promise<RunSnapshot> {
  validateId(runId);
  return decodeRun(await request(`/runs/${runId}`, "GET", signal), runId);
}
