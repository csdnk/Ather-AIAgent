import { act, renderHook } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { get } from "./api";
import { useMonitor } from "./useMonitor";

vi.mock("./api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("./api")>()),
  get: vi.fn(),
}));
afterEach(() => {
  vi.useRealTimers();
  vi.clearAllMocks();
});

it("does not request protected data before connecting", () => {
  const hook = renderHook(() =>
    useMonitor("", 5, 0, null, "", null, "business"),
  );
  expect(get).not.toHaveBeenCalled();
  expect(hook.result.current.samples).toEqual([]);
});

it("clears failed snapshots, keeps gaps unknown, and recovers on the next poll", async () => {
  vi.useFakeTimers();
  vi.mocked(get).mockImplementation(async (path) =>
    path === "/p3/runtime"
      ? { pending_tasks: 3, unacknowledged_deliveries: 2 }
      : { items: [] },
  );
  const hook = renderHook(() =>
    useMonitor("test-token", 5, 0, null, "", null, "business"),
  );
  await act(async () => {});
  expect(hook.result.current.snapshot.runtime.data?.pending_tasks).toBe(3);
  vi.mocked(get).mockRejectedValue(new TypeError("network error"));
  await act(async () => {
    await vi.advanceTimersByTimeAsync(5000);
  });
  expect(hook.result.current.snapshot.runtime.data).toBeUndefined();
  expect(hook.result.current.snapshot.runtime.error).toContain("无法连接");
  expect(hook.result.current.samples.at(-1)?.pending).toBeNull();
  vi.mocked(get).mockImplementation(async (path) =>
    path === "/p3/runtime"
      ? { pending_tasks: 1, unacknowledged_deliveries: 0 }
      : { items: [] },
  );
  await act(async () => {
    await vi.advanceTimersByTimeAsync(5000);
  });
  expect(hook.result.current.snapshot.runtime.data?.pending_tasks).toBe(1);
  expect(hook.result.current.samples.map((s) => s.pending)).toEqual([
    3,
    null,
    1,
  ]);
  hook.unmount();
  const calls = vi.mocked(get).mock.calls.length;
  await act(async () => {
    await vi.advanceTimersByTimeAsync(10000);
  });
  expect(get).toHaveBeenCalledTimes(calls);
});
