import { isMockMode, requestJson } from "./client";
import type { SystemStatusResponse } from "./types";
import { mockSystemStatus } from "../mock/data";

export function getSystemStatus(): Promise<SystemStatusResponse> {
  if (isMockMode) return Promise.resolve(mockSystemStatus);
  return requestJson<SystemStatusResponse>("/api/status");
}
