import type { RunSnapshot, Scenarios } from "./types";

export const id = "a1100000-0000-4000-8000-000000000001";
export const scenarios: Scenarios = {
  items: [{ id: "library-basic", title: "图书馆里的记忆", description: "六步真实调用。", total_steps: 6 }],
  enabled: true, unavailable_reason: null,
};
export function snapshot(state: RunSnapshot["state"] = "running", runId = id): RunSnapshot {
  return {
    run_id: runId, scenario_id: "library-basic", state, current_step: 3, total_steps: 6,
    mode: { profile: "local", embedding: "lexical", semantic_processing: "literal_baseline",
      object_storage: "local_sqlite", scheduling: "temporal_v1", executor: "local_filesystem_cache" },
    steps: Array.from({ length: 6 }, (_, i) => ({
      id: i + 1, user_text: `预设发言 ${i + 1}`,
      state: state === "passed" || i < 3 ? "passed" : "pending",
      response_text: i === 2 ? "这是服务端实际返回的测试内容" : "",
      checks: i === 2 ? [{ name: "目标引用", passed: true, detail: "返回本轮记忆" }] : [],
      evidence: i === 2 ? { method: "POST", path: "/p3/recall", operation_id: "op_demo_3",
        job_id: null, memories: [], source_ids: [], task_ids: [], recall_id: "recall_demo",
        elapsed_ms: 12, processing: [], context: null, calls: [], tasks: [], cleanup_state: null } : null,
    })),
    error: null, coverage: [],
    diagnostics: { state: "pending", task_count: 0, trace_count: 0,
      log_record_count: 0, incident_count: 0, notes: [] },
  };
}
export function reply(data: unknown, status = 200) {
  return new Response(JSON.stringify(data), { status, headers: { "Content-Type": "application/json" } });
}
