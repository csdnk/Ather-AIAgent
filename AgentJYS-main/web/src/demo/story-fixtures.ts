import { id, snapshot } from "./test-fixtures";
import type { RunSnapshot, Scenarios } from "./types";

export const fullScenarios = {
  enabled: true, unavailable_reason: null,
  items: [
    { id: "library-full", title: "图书馆里的记忆", description: "规则与原文", total_steps: 9 },
    { id: "weather-weekend", title: "雨天的周末计划", description: "跨会话召回", total_steps: 8 },
    { id: "preference-update", title: "偏好可以被更正", description: "版本与生命周期", total_steps: 8 },
    { id: "learning-review", title: "让学习留下线索", description: "反思与提炼", total_steps: 8 },
    { id: "forget-sources", title: "记住，也能够忘记", description: "删除与撤销", total_steps: 7 },
  ],
} as Scenarios;

export function storySnapshot(
  scenario = "weather-weekend", state: RunSnapshot["state"] = "running", runId = id,
): RunSnapshot {
  const total = scenario === "library-full" ? 9 : scenario === "forget-sources" ? 7 : 8;
  const base = snapshot(state, runId);
  return {
    ...base, scenario_id: scenario, total_steps: total,
    steps: Array.from({ length: total }, (_, i) => base.steps[i] ?? {
      id: i + 1, user_text: `系统步骤 ${i + 1}`, state: "pending",
      response_text: "", checks: [], evidence: null,
    }),
    coverage: [
      { method: "POST", path: "/p3/remember", state: "passed", calls: 3,
        http_statuses: [200], step_ids: [1, 2, 3], detail: "保存检查通过" },
      { method: "GET", path: "/p3/tasks/{task_id}", state: "called", calls: 2,
        http_statuses: [200], step_ids: [6], detail: "等待真实任务" },
      { method: "POST", path: "/p3/remember/distill", state: "blocked", calls: 1,
        http_statuses: [200], step_ids: [7], detail: "模型条件不足" },
      { method: "POST", path: "/p3/backups", state: "unexecuted", calls: 0,
        http_statuses: [], step_ids: [], detail: "仅限隔离运维验收" },
    ],
    diagnostics: { state: "incomplete", task_count: 2, trace_count: 1,
      log_record_count: 0, incident_count: 0, notes: ["仅采集本轮有限窗口，非全量审计"] },
  } as RunSnapshot;
}
