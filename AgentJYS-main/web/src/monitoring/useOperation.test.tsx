import { act, renderHook } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { get } from "./api";
import { useOperation } from "./useMonitor";

vi.mock("./api", async (original) => ({ ...(await original<typeof import("./api")>()), get: vi.fn() }));
afterEach(() => { vi.useRealTimers(); vi.clearAllMocks(); });

it("follows the original operation from pending to completion and clears revoked results", async () => {
  vi.useFakeTimers();
  let state = "pending";
  vi.mocked(get).mockImplementation(async (path) => path.endsWith("/result")
    ? { recall_id: "original", content: "authorized result" }
    : { task_id: "original", state, temporal: { workflow_id: "original-workflow" } });
  const hook = renderHook(() => useOperation("original", "token"));
  await act(async () => {});
  expect(hook.result.current.data?.operation.state).toBe("pending");
  expect(hook.result.current.data?.result).toBeUndefined();
  state = "succeeded";
  await act(async () => { await vi.advanceTimersByTimeAsync(2000); });
  expect(hook.result.current.data?.result).toEqual({ recall_id: "original", content: "authorized result" });
  vi.mocked(get).mockRejectedValue(new Error("当前身份没有此诊断权限。"));
  await act(async () => { await vi.advanceTimersByTimeAsync(2000); });
  expect(hook.result.current.data).toBeUndefined();
  expect(hook.result.current.error).toContain("权限");
  hook.unmount();
});

it("shows terminal failure without requesting an old success result", async () => {
  vi.mocked(get).mockResolvedValue({ task_id: "failed", state: "attention_required", error_code: "EXECUTION_INTERRUPTED" });
  const hook = renderHook(() => useOperation("failed", "token"));
  await act(async () => {});
  expect(hook.result.current.data?.operation.state).toBe("attention_required");
  expect(get).toHaveBeenCalledTimes(1);
  hook.unmount();
});

it("clears data when the authenticated identity changes", async () => {
  vi.mocked(get).mockResolvedValue({ task_id: "old", state: "pending" });
  const hook = renderHook(({ token }) => useOperation("old", token), { initialProps: { token: "one" } });
  await act(async () => {});
  hook.rerender({ token: "" });
  expect(hook.result.current.data).toBeUndefined();
  hook.unmount();
});
