import type { SystemStatus, SystemStatusResponse } from "../api/types";

export function deriveSystemStatus(data?: SystemStatusResponse | null): SystemStatus {
  const status = data?.runtime_health?.overall;
  if (!status) return "Unavailable";
  if (status.includes("UNAVAILABLE")) return "Unavailable";
  if (status.includes("DEGRADED") || status.includes("PROTECT")) return "Degraded";
  return "Healthy";
}
