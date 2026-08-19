export const isMockMode = import.meta.env.VITE_USE_MOCK === "true";

const rawBase = (import.meta.env.VITE_AETHER_API_BASE_URL ?? "").trim();
export const apiBaseUrl = rawBase.replace(/\/$/, "");

export class ApiError extends Error {
  status: number;
  category: string;
  payload: unknown;

  constructor(message: string, status: number, category: string, payload: unknown) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.category = category;
    this.payload = payload;
  }
}

function categoryFor(status: number, payload: any): string {
  const code = payload?.runtime_error?.code;
  if (code === "VALIDATION_ERROR") return "Invalid Argument";
  if (code === "BUSY") return "Busy";
  if (code === "TIMEOUT") return "Timeout";
  if (code === "DEPENDENCY_UNAVAILABLE") return "Unavailable";
  if (status === 404) return "Not Found";
  if (status === 422) return "Invalid Argument";
  if (status >= 500) return "Internal";
  return "Request Error";
}

export async function requestJson<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${apiBaseUrl}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(init?.headers ?? {}),
    },
  });
  const text = await response.text();
  const payload = text ? JSON.parse(text) : null;
  if (!response.ok) {
    const message = payload?.error || payload?.detail || response.statusText || "Request failed";
    throw new ApiError(message, response.status, categoryFor(response.status, payload), payload);
  }
  return payload as T;
}

export function postJson<T>(path: string, body: unknown): Promise<T> {
  return requestJson<T>(path, {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function newId(prefix: string): string {
  if ("crypto" in window && "randomUUID" in window.crypto) {
    return `${prefix}-${window.crypto.randomUUID().replaceAll("-", "")}`;
  }
  return `${prefix}-${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;
}
