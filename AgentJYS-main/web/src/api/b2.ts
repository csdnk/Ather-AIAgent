import { isMockMode, newId, postJson, requestJson } from "./client";
import type { B2SearchResponse, ContextResponse, MemoryRecord, TaskStatus } from "./types";
import { mockContext, mockLongTextTask, mockMemory } from "../mock/data";

export interface Scope {
  sessionId: string;
  agentId: string;
  userId: string;
  tenantId: string;
}

export function writeMemory(scope: Scope, content: string): Promise<MemoryRecord> {
  if (isMockMode) return Promise.resolve({ ...mockMemory, content });
  return postJson<MemoryRecord>("/api/v1/memory/events", {
    event_type: "user_memory",
    session_id: scope.sessionId,
    agent_id: scope.agentId,
    user_id: scope.userId,
    tenant_id: scope.tenantId,
    content,
    source: "user",
    importance: 0.95,
    request_id: newId("b2-req"),
    trace_id: newId("b2-trace"),
    metadata: {
      channel: "web",
    },
  });
}

export function buildContext(scope: Scope, query: string): Promise<ContextResponse> {
  if (isMockMode) return Promise.resolve(mockContext);
  return postJson<ContextResponse>("/api/v1/context", {
    session_id: scope.sessionId,
    agent_id: scope.agentId,
    user_id: scope.userId,
    tenant_id: scope.tenantId,
    query,
    max_candidates: 20,
    request_id: newId("ctx-req"),
    trace_id: newId("ctx-trace"),
  });
}

export function submitLongText(scope: Scope, text: string, sourceId: string): Promise<TaskStatus> {
  if (isMockMode) return Promise.resolve(mockLongTextTask);
  return postJson<TaskStatus>("/api/v1/b2/long-text", {
    session_id: scope.sessionId,
    agent_id: scope.agentId,
    user_id: scope.userId,
    tenant_id: scope.tenantId,
    text,
    source_id: sourceId,
    request_id: newId("long-req"),
    trace_id: newId("long-trace"),
  });
}

export function getB2Task(taskId: string): Promise<TaskStatus> {
  if (isMockMode) return Promise.resolve({ ...mockLongTextTask, task_id: taskId });
  return requestJson<TaskStatus>(`/api/v1/b2/tasks/${taskId}`);
}

export function searchB2(scope: Scope, query: string): Promise<B2SearchResponse> {
  if (isMockMode) {
    return Promise.resolve({
      items: [{ memory_id: mockMemory.id, text: mockMemory.content, score: 0.91 }],
    });
  }
  return postJson<B2SearchResponse>("/api/v1/b2/search", {
    query,
    session_id: scope.sessionId,
    agent_id: scope.agentId,
    user_id: scope.userId,
    tenant_id: scope.tenantId,
    limit: 10,
    request_id: newId("search-req"),
    trace_id: newId("search-trace"),
  });
}
