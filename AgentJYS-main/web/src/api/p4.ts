import { ApiError } from "./client";

const explicitBase = (import.meta.env.VITE_P4_API_BASE_URL ?? "").trim().replace(/\/$/, "");
export const p4ApiBaseUrl = explicitBase || "/p4-api";

export interface P4Health {
  status: "healthy" | "degraded" | string;
  live: boolean;
  p3_reachable: boolean;
  p3_base_url: string;
  detail?: string | null;
  checked_at: string;
}

export interface P4Message {
  message_id: string;
  role: "user" | "assistant";
  content: string;
  created_at: string;
  trace_id?: string | null;
  memory_id?: string | null;
  context_memory_refs: string[];
}

export interface P4Session {
  session_id: string;
  agent_id: string;
  tenant_id: string;
  user_id: string;
  messages: P4Message[];
  created_at: string;
  updated_at: string;
}

export interface P4TurnResult {
  session: P4Session;
  user_message: P4Message;
  assistant_message: P4Message;
  context: {
    status?: string;
    complete?: boolean;
    trace_id?: string | null;
    memories?: Array<Record<string, unknown>>;
    memory_refs?: string[];
    missing_sources?: string[];
    degradation_reasons?: Record<string, string>;
  };
  memory: Record<string, unknown>;
  p3_calls: string[];
}

export interface P4Task {
  task_id: string;
  state: string;
  trace_id?: string;
  chunk_count?: number;
  error?: string;
  [key: string]: unknown;
}

async function p4Request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${p4ApiBaseUrl}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(init?.headers ?? {}),
    },
  });
  const text = await response.text();
  let payload: any = null;
  try {
    payload = text ? JSON.parse(text) : null;
  } catch {
    throw new ApiError("P4 returned an invalid response", response.status, "Internal", text);
  }
  if (!response.ok) {
    const message = payload?.error || response.statusText || "P4 request failed";
    const category = response.status === 404 ? "Not Found" : response.status >= 500 ? "Unavailable" : "Invalid Argument";
    throw new ApiError(message, response.status, category, payload);
  }
  return payload as T;
}

function p4Post<T>(path: string, body: unknown): Promise<T> {
  return p4Request<T>(path, { method: "POST", body: JSON.stringify(body) });
}

export function getP4Health(): Promise<P4Health> {
  return p4Request<P4Health>("/health");
}

export function createP4Session(): Promise<P4Session> {
  return p4Post<P4Session>("/api/v1/sessions", {
    agent_id: "company-assistant",
    tenant_id: "demo-tenant",
    user_id: "demo-user",
  });
}

export function sendP4Message(
  sessionId: string,
  content: string,
  durableMemory: boolean,
): Promise<P4TurnResult> {
  return p4Post<P4TurnResult>(`/api/v1/sessions/${sessionId}/messages`, {
    content,
    durable_memory: durableMemory,
    max_context_tokens: 2048,
  });
}

export function submitP4Document(
  sessionId: string,
  sourceId: string,
  text: string,
): Promise<P4Task> {
  return p4Post<P4Task>(`/api/v1/sessions/${sessionId}/documents`, {
    source_id: sourceId,
    text,
  });
}

export function getP4Task(taskId: string): Promise<P4Task> {
  return p4Request<P4Task>(`/api/v1/tasks/${taskId}`);
}
