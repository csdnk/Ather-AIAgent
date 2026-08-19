import { isMockMode, newId, postJson, requestJson } from "./client";
import type {
  B3CandidateResponse,
  ScheduleHistoryItem,
  ScheduleRunResult,
  SchedulableObject,
} from "./types";
import { mockB3Candidates, mockScheduleHistory, mockScheduleRun } from "../mock/data";
import type { Scope } from "./b2";

export function getScheduleHistory(): Promise<{ items: ScheduleHistoryItem[] }> {
  if (isMockMode) return Promise.resolve({ items: mockScheduleHistory });
  return requestJson<{ items: ScheduleHistoryItem[] }>("/api/schedules");
}

export function getB3Candidates(scope: Scope, query: string): Promise<B3CandidateResponse> {
  if (isMockMode) return Promise.resolve(mockB3Candidates);
  return postJson<B3CandidateResponse>("/api/v1/b3/candidates", {
    session_id: scope.sessionId,
    agent_id: scope.agentId,
    user_id: scope.userId,
    tenant_id: scope.tenantId,
    query,
    max_candidates: 20,
    request_id: newId("b3-candidates"),
    trace_id: newId("b3-trace"),
  });
}

export function runSchedule(
  objects: SchedulableObject[],
  resourceState: B3CandidateResponse["resource_state"],
): Promise<ScheduleRunResult> {
  if (isMockMode) return Promise.resolve(mockScheduleRun);
  return postJson<ScheduleRunResult>("/api/v1/schedules", {
    objects,
    resource_state: resourceState,
    request_id: newId("sched-req"),
    trace_id: newId("sched-trace"),
  });
}
